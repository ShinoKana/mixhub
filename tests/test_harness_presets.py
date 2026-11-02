#!/usr/bin/env python3
"""
Claude Code with other models behind it (harness presets):
  1. `HARNESS_BRAIN_PRESETS` endpoints, badges and model slots.
  2. Process-level isolated launch (`build_launch`) without polluting `~/.claude/settings.json` or `~/.codex/config.toml`.
  3. Cross-brain Resume Signature Guard (`check_resume`, `fork_without_thinking`, `guarded_resume`) preventing HTTP 400 errors.
  4. Multi-brain subprovider badge detection (`CC·DS`, `CC·Kimi`, `CC·OR`, `CC·Qwen`, `CC·GLM`).
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import providers


class SATierHarnessMatrixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="mixhub_sa_matrix_")
        self.base = Path(self.tmp)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_harness_brain_presets_s_and_a_tier_completeness(self) -> None:
        presets = providers.HARNESS_BRAIN_PRESETS
        expected_s = {"claude", "codex", "agy", "cc-deepseek", "cc-kimi", "cc-openrouter"}
        expected_a = {"cc-qwen", "cc-glm", "cx-deepseek", "cx-openrouter"}
        self.assertTrue(expected_s.issubset(presets.keys()))
        self.assertTrue(expected_a.issubset(presets.keys()))

        self.assertEqual({k for k, p in presets.items() if p["tier"] == "S"}, expected_s)
        self.assertEqual({k for k, p in presets.items() if p["tier"] == "A"}, expected_a)

        # Verify required fields on every preset
        for key, p in presets.items():
            self.assertIn(p["tier"], ("S", "A"), msg=key)
            self.assertIn(p["harness"], ("claude", "codex", "agy"), msg=key)
            self.assertTrue(p["brain"], msg=key)
            self.assertTrue(p["badge"], msg=key)
            self.assertTrue(callable(p["launch_env_builder"]), msg=key)

        # Verify key endpoints and badges
        self.assertEqual(presets["cc-deepseek"]["badge"], "CC·DS")
        self.assertEqual(presets["cc-deepseek"]["base_url"], "https://api.deepseek.com/anthropic")
        self.assertEqual(presets["cc-kimi"]["badge"], "CC·Kimi")
        self.assertIn("moonshot", presets["cc-kimi"]["base_url"])
        self.assertEqual(presets["cc-openrouter"]["badge"], "CC·OR")
        self.assertEqual(presets["cc-openrouter"]["base_url"], "https://openrouter.ai/api")
        self.assertEqual(presets["cc-qwen"]["badge"], "CC·Qwen")
        self.assertIn("dashscope", presets["cc-qwen"]["base_url"])
        self.assertEqual(presets["cc-glm"]["badge"], "CC·GLM")
        self.assertEqual(presets["cx-deepseek"]["badge"], "CX·DS")
        self.assertEqual(presets["cx-openrouter"]["badge"], "CX·OR")

    def test_process_isolated_launch_never_touches_global_claude_settings(self) -> None:
        fake_home = self.base / "home"
        claude_dir = fake_home / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        global_settings = claude_dir / "settings.json"
        initial_settings = json.dumps({"theme": "dark", "env": {"ANTHROPIC_MODEL": "claude-opus-4-6"}})
        global_settings.write_text(initial_settings, encoding="utf-8")

        dirty_base_env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "ANTHROPIC_BASE_URL": "https://leaked.example.com",
            "ANTHROPIC_API_KEY": "leaked-key",
            "ANTHROPIC_MODEL": "leaked-model",
            "DEEPSEEK_API_KEY": "ds-test-secret",
        }

        # 1. Claude Code + DeepSeek (`cc-deepseek`)
        with providers.build_launch(
            "cc-deepseek",
            cwd=str(self.base),
            resume="sess-123",
            base_env=dirty_base_env,
            home_dir=str(fake_home),
        ) as spec:
            self.assertEqual(spec.argv[0], "claude")
            self.assertIn("--settings", spec.argv)
            self.assertIn("--resume", spec.argv)
            self.assertEqual(spec.env["ANTHROPIC_BASE_URL"], "https://api.deepseek.com/anthropic")
            self.assertEqual(spec.env["ANTHROPIC_AUTH_TOKEN"], "ds-test-secret")
            self.assertNotIn("ANTHROPIC_API_KEY", spec.env)
            # All 6 model slots mapped so background Haiku/subagent calls don't 400
            for slot in (
                "ANTHROPIC_MODEL",
                "ANTHROPIC_SMALL_FAST_MODEL",
                "ANTHROPIC_DEFAULT_OPUS_MODEL",
                "ANTHROPIC_DEFAULT_SONNET_MODEL",
                "ANTHROPIC_DEFAULT_HAIKU_MODEL",
                "CLAUDE_CODE_SUBAGENT_MODEL",
            ):
                self.assertEqual(spec.env[slot], "deepseek-chat", msg=slot)
            # Temporary settings file exists during context
            self.assertEqual(len(spec.temp_files), 1)
            tmp_settings_path = Path(spec.temp_files[0])
            self.assertTrue(tmp_settings_path.is_file())

        # Temporary settings file cleaned up on exit, and global ~/.claude/settings.json is untouched!
        self.assertFalse(tmp_settings_path.exists())
        self.assertEqual(global_settings.read_text(encoding="utf-8"), initial_settings)

        # 2. Codex + DeepSeek (`cx-deepseek`) uses `-c model_provider=...`
        with providers.build_launch(
            "cx-deepseek",
            resume="cx-999",
            base_env=dirty_base_env,
            home_dir=str(fake_home),
        ) as cx_spec:
            cmd_joined = " ".join(cx_spec.argv)
            self.assertIn("codex resume cx-999", cmd_joined)
            self.assertIn('model_provider="mixhub_cx_deepseek"', cmd_joined)
            self.assertIn('model_providers.mixhub_cx_deepseek.base_url="https://api.deepseek.com/v1"', cmd_joined)

        # 3. Missing API key raises MissingKeyError
        with self.assertRaises(providers.MissingKeyError):
            providers.build_launch("cc-kimi", base_env={"PATH": "/usr/bin"})

    def test_cross_brain_resume_signature_guard_and_fork_without_thinking(self) -> None:
        # Create a Claude JSONL containing an official Anthropic signed `thinking` block
        signed_claude_jsonl = self.base / "11111111-2222-3333-4444-555555555555.jsonl"
        rows = [
            {
                "uuid": "u-1",
                "parentUuid": None,
                "sessionId": "11111111-2222-3333-4444-555555555555",
                "type": "user",
                "message": {"role": "user", "content": [{"type": "text", "text": "Design the auth state machine"}]},
            },
            {
                "uuid": "u-2",
                "parentUuid": "u-1",
                "sessionId": "11111111-2222-3333-4444-555555555555",
                "type": "assistant",
                "message": {
                    "model": "claude-opus-4-6",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "thinking",
                            "thinking": "Analyzing state transitions...",
                            "signature": "ErUBCkYICBgCIkD9f8a7s6d8f7a6s8d7f6a8s7d6f==",
                        },
                        {"type": "text", "text": "Here is the state machine design."},
                    ],
                },
            },
        ]
        original_text = "\n".join(json.dumps(r) for r in rows) + "\n"
        signed_claude_jsonl.write_text(original_text, encoding="utf-8")

        # 1. Resuming on native `claude` (same brain = anthropic) is ALLOWED
        chk_same = providers.check_resume(signed_claude_jsonl, "claude")
        self.assertTrue(chk_same.ok)
        self.assertEqual(chk_same.target, "anthropic")

        # 2. Resuming on `cc-deepseek` (foreign brain = deepseek) is BLOCKED due to signed thinking block (HTTP 400 guard)
        chk_cross = providers.check_resume(signed_claude_jsonl, "cc-deepseek")
        self.assertFalse(chk_cross.ok)
        self.assertIn("thinking_signature_conflict", chk_cross.reason)
        self.assertIn("400", chk_cross.reason)

        # 3. `guarded_resume(..., auto_fork=False)` raises `ResumeSignatureError`
        with self.assertRaises(providers.ResumeSignatureError):
            providers.guarded_resume(
                "cc-deepseek",
                "11111111-2222-3333-4444-555555555555",
                session_path=signed_claude_jsonl,
                auto_fork=False,
                api_key="ds-key",
            )

        # 4. `guarded_resume(..., auto_fork=True)` strips thinking blocks into a new forked file without touching source
        with providers.guarded_resume(
            "cc-deepseek",
            "11111111-2222-3333-4444-555555555555",
            session_path=signed_claude_jsonl,
            auto_fork=True,
            api_key="ds-key",
        ) as forked_spec:
            resumed_sid = forked_spec.argv[forked_spec.argv.index("--resume") + 1]
            self.assertNotEqual(resumed_sid, "11111111-2222-3333-4444-555555555555")
            forked_file = self.base / f"{resumed_sid}.jsonl"
            self.assertTrue(forked_file.is_file())
            forked_content = forked_file.read_text(encoding="utf-8")
            self.assertNotIn('"signature"', forked_content)
            self.assertIn("Here is the state machine design.", forked_content)
            # Original file is 100% untouched
            self.assertEqual(signed_claude_jsonl.read_text(encoding="utf-8"), original_text)

    def test_detect_claude_subprovider_across_s_and_a_tiers(self) -> None:
        self.assertEqual(
            providers.detect_claude_subprovider(env={"ANTHROPIC_BASE_URL": "https://api.deepseek.com/anthropic"}),
            "CC·DS",
        )
        self.assertEqual(
            providers.detect_claude_subprovider(env={"ANTHROPIC_BASE_URL": "https://api.moonshot.ai/anthropic"}),
            "CC·Kimi",
        )
        self.assertEqual(
            providers.detect_claude_subprovider(env={"ANTHROPIC_BASE_URL": "https://openrouter.ai/api"}),
            "CC·OR",
        )
        self.assertEqual(
            providers.detect_claude_subprovider(
                env={"ANTHROPIC_BASE_URL": "https://dashscope.aliyuncs.com/apps/anthropic"}
            ),
            "CC·Qwen",
        )
        self.assertEqual(
            providers.detect_claude_subprovider(env={"ANTHROPIC_BASE_URL": "https://api.z.ai/api/anthropic"}),
            "CC·GLM",
        )
        self.assertEqual(
            providers.detect_claude_subprovider(env={"ANTHROPIC_BASE_URL": "https://api.anthropic.com"}),
            "Claude",
        )

    def test_borrowed_preset_needs_its_own_key_and_manual_overrides(self) -> None:
        home = self.base / "borrowed_home"
        claude = home / ".claude"
        claude.mkdir(parents=True)
        kwargs = {"home_dir": str(home), "env": {}}
        self.assertFalse(providers.is_provider_active_and_authenticated("cc-kimi", **kwargs))
        self.assertEqual(providers.get_provider_readiness_state("cc-kimi", **kwargs)["state"], "installed_unready")

        tagged_session = claude / "projects" / "project" / "kimi-sid.jsonl"
        tagged_session.parent.mkdir(parents=True)
        tagged_session.write_text("\n".join(json.dumps(row) for row in (
            {"type": "system", "MIXHUB_PRESET": "cc-kimi"},
            {"type": "user", "message": {"role": "user", "content": "A real Kimi prompt"}},
        )) + "\n")
        history = providers.get_provider_readiness_state("cc-kimi", **kwargs)
        self.assertEqual(history["state"], "history_only")
        self.assertEqual(history["non_empty_session_count"], 1)
        self.assertTrue(providers.is_provider_active_and_authenticated("cc-kimi", **kwargs))
        self.assertFalse(providers.is_provider_active_and_authenticated("cc-deepseek", **kwargs))
        tagged_session.unlink()

        keyed = {"home_dir": str(home), "env": {"MOONSHOT_API_KEY": "moonshot-test"}}
        self.assertTrue(providers.is_provider_active_and_authenticated("cc-kimi", **keyed))
        self.assertEqual(providers.get_provider_readiness_state("cc-kimi", **keyed)["state"], "ready")
        settings = claude / "settings.json"
        settings.write_text(json.dumps({"env": {"MOONSHOT_API_KEY": "configured-in-settings"}}))
        self.assertTrue(providers.is_provider_active_and_authenticated("cc-kimi", **kwargs))

        prefs = self.base / "readiness_preferences.json"
        prefs.write_text(json.dumps({"always_hide_providers": ["cc-kimi"]}))
        self.assertFalse(providers.is_provider_active_and_authenticated(
            "cc-kimi", preferences_path=str(prefs), **keyed
        ))
        settings.unlink()
        prefs.write_text(json.dumps({"always_show_providers": ["cc-kimi"]}))
        shown = providers.get_provider_readiness_state("cc-kimi", preferences_path=str(prefs), **kwargs)
        self.assertTrue(shown["active"])
        self.assertEqual(shown["state"], "history_only")

    def test_native_history_count_ignores_metadata_only_sessions(self) -> None:
        home = self.base / "native_history_home"
        claude_session = home / ".claude" / "projects" / "project" / "claude-sid.jsonl"
        claude_session.parent.mkdir(parents=True)
        claude_session.write_text(json.dumps({"type": "system", "sessionId": "claude-sid"}) + "\n")
        kwargs = {"home_dir": str(home), "env": {}}
        self.assertEqual(providers.get_provider_readiness_state("claude", **kwargs)["non_empty_session_count"], 0)
        claude_session.write_text(json.dumps({
            "type": "user", "message": {"role": "user", "content": "A real prompt"}
        }) + "\n")
        self.assertEqual(providers.get_provider_readiness_state("claude", **kwargs)["state"], "history_only")
        self.assertEqual(providers.get_provider_readiness_state("claude-code", **kwargs)["provider"], "claude")

        codex_session = home / ".codex" / "sessions" / "2026" / "10" / "25" / "rollout-sid.jsonl"
        codex_session.parent.mkdir(parents=True)
        codex_session.write_text(json.dumps({"type": "session_meta", "payload": {"id": "sid"}}) + "\n")
        self.assertEqual(providers.get_provider_readiness_state("codex", **kwargs)["non_empty_session_count"], 0)
        with codex_session.open("a") as stream:
            stream.write(json.dumps({
                "type": "response_item",
                "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Hello"}]},
            }) + "\n")
        self.assertEqual(providers.get_provider_readiness_state("codex", **kwargs)["state"], "history_only")

        agy_root = home / ".gemini" / "antigravity-cli"
        agy_root.mkdir(parents=True)
        with closing(sqlite3.connect(agy_root / "conversation_summaries.db")) as con, con:
            con.execute("CREATE TABLE conversation_summaries(conversation_id TEXT, title TEXT, preview TEXT)")
            con.execute("INSERT INTO conversation_summaries VALUES ('empty', '', '')")
            con.execute("INSERT INTO conversation_summaries VALUES ('real', 'Actual work', '')")
        self.assertEqual(providers.get_provider_readiness_state("agy", **kwargs)["non_empty_session_count"], 1)


if __name__ == "__main__":
    unittest.main()
