#!/usr/bin/env python3
"""
Unit tests for:
  1. `rules_sync.ensure_workspace_trusted(cwd)` (Claude, AGY/Gemini, Codex workspace trust pre-seeding,
     atomic write, backup creation, and existing JSON/TOML field preservation).
  2. Bidirectional Native Title Sync (`read_native_title` & `write_native_title`) across
     Claude Code (`.jsonl` custom-title + `title_overrides.json`),
     AGY (`conversation_summaries.db` + `brain/<cid>` + `title_overrides.json`),
     and Codex (`rollout-*.jsonl` + `title_overrides.json`).
  3. Modular package structure (`mixhub.core`, `mixhub.providers`, `mixhub.handoff`,
     `mixhub.channels`).
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import providers
import rules_sync


class WorkspaceTrustAndNativeTitleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.home = self.base / "home"
        self.home.mkdir(parents=True, exist_ok=True)
        self.workspace = self.home / "my_project"
        self.workspace.mkdir(parents=True, exist_ok=True)
        # Pre-trusting workspaces is opt-in; these tests exercise the opted-in path
        prefs = self.base / "Preferences"
        prefs.write_text(json.dumps({"trust_all_workspaces": True}), encoding="utf-8")
        self._env = unittest.mock.patch.dict(os.environ, {"MIXHUB_PREFERENCES_PATH": str(prefs)})
        self._env.start()

    def tearDown(self) -> None:
        self._env.stop()
        self.tmp.cleanup()

    def test_ensure_workspace_trusted_preserves_existing_json_fields_and_is_idempotent(self) -> None:
        claude_cfg = self.home / ".claude.json"
        realpath_ws = os.path.realpath(str(self.workspace))
        initial_claude = {
            "numStartups": 42,
            "oauthAccount": {"email": "dev@example.com"},
            "projects": {
                str(self.workspace): {
                    "allowedTools": ["Bash", "Read"],
                    "mcpServers": {"local": {"command": "node"}},
                    "hasTrustDialogAccepted": False,
                },
                realpath_ws: {
                    "allowedTools": ["Bash", "Read"],
                    "mcpServers": {"local": {"command": "node"}},
                    "hasTrustDialogAccepted": False,
                },
                "/Users/other/kept_project": {
                    "hasTrustDialogAccepted": True,
                    "lastCost": 1.25,
                },
            },
        }
        claude_cfg.write_text(json.dumps(initial_claude, indent=2), encoding="utf-8")

        agy_cfg = self.home / ".gemini" / "antigravity-cli" / "settings.json"
        agy_cfg.parent.mkdir(parents=True, exist_ok=True)
        agy_cfg.write_text(
            json.dumps(
                {
                    "model": "Gemini 4 Argon (High)",
                    "altScreenMode": "always",
                    "trustedWorkspaces": ["/existing/workspace"],
                },
                indent=2,
            ),
            encoding="utf-8",
        )

        codex_cfg = self.home / ".codex" / "config.toml"
        codex_cfg.parent.mkdir(parents=True, exist_ok=True)
        codex_cfg.write_text(
            'model = "gpt-6-sol"\n\n[projects."/existing/codex"]\ntrust_level = "trusted"\n',
            encoding="utf-8",
        )

        res = rules_sync.ensure_workspace_trusted(str(self.workspace), home_dir=str(self.home))
        self.assertTrue(res["ok"])
        self.assertTrue(res["trusted"])
        self.assertIn("claude", res["updated"])
        self.assertIn("agy", res["updated"])
        self.assertIn("codex", res["updated"])

        # Verify backup file was created before atomic replacement
        self.assertTrue((self.home / ".claude.json.bak").is_file())

        # Verify Claude JSON preserved all unrelated keys & nested fields
        after_claude = json.loads(claude_cfg.read_text(encoding="utf-8"))
        self.assertEqual(after_claude["numStartups"], 42)
        self.assertEqual(after_claude["oauthAccount"]["email"], "dev@example.com")
        self.assertEqual(after_claude["projects"]["/Users/other/kept_project"]["lastCost"], 1.25)

        ws_entry = after_claude["projects"][str(self.workspace)]
        self.assertTrue(ws_entry["hasTrustDialogAccepted"])
        self.assertTrue(ws_entry["hasClaudeMdExternalIncludesApproved"])
        self.assertTrue(ws_entry["hasClaudeMdExternalIncludesWarningShown"])
        self.assertEqual(ws_entry["allowedTools"], ["Bash", "Read"])
        self.assertEqual(ws_entry["mcpServers"], {"local": {"command": "node"}})

        # Verify AGY settings.json & trustedFolders.json
        after_agy = json.loads(agy_cfg.read_text(encoding="utf-8"))
        self.assertEqual(after_agy["model"], "Gemini 4 Argon (High)")
        self.assertIn("/existing/workspace", after_agy["trustedWorkspaces"])
        self.assertIn(str(self.workspace), after_agy["trustedWorkspaces"])

        trusted_folders = json.loads(
            (self.home / ".gemini" / "trustedFolders.json").read_text(encoding="utf-8")
        )
        self.assertEqual(trusted_folders[str(self.workspace)], "TRUST_FOLDER")

        # Verify Codex config.toml
        after_codex = codex_cfg.read_text(encoding="utf-8")
        self.assertIn('model = "gpt-6-sol"', after_codex)
        self.assertIn('[projects."/existing/codex"]', after_codex)
        self.assertIn(f'[projects."{self.workspace}"]', after_codex)
        self.assertIn('trust_level = "trusted"', after_codex)

        # Second call is idempotent
        res_second = rules_sync.ensure_workspace_trusted(str(self.workspace), home_dir=str(self.home))
        self.assertTrue(res_second["ok"])
        self.assertEqual(res_second["updated"], [])

    def test_ensure_workspace_trusted_single_arg_temp_dir_and_corrupt_json_guard(self) -> None:
        # Calling `ensure_workspace_trusted(cwd)` with only 1 argument on a temp dir works
        # and sandboxes inside the temp dir without touching real ~/.claude.json
        standalone_ws = self.base / "standalone_ws"
        standalone_ws.mkdir(parents=True, exist_ok=True)
        (standalone_ws / ".claude.json").write_text(
            json.dumps({"customField": "preserved", "projects": {}}),
            encoding="utf-8",
        )
        res = rules_sync.ensure_workspace_trusted(str(standalone_ws))
        self.assertTrue(res["ok"])
        self.assertTrue(res["trusted"])
        loaded = json.loads((standalone_ws / ".claude.json").read_text(encoding="utf-8"))
        self.assertEqual(loaded["customField"], "preserved")
        self.assertTrue(loaded["projects"][str(standalone_ws)]["hasTrustDialogAccepted"])

        # Corrupt ~/.claude.json is never overwritten with empty dict
        corrupt_home = self.base / "corrupt_home"
        corrupt_ws = corrupt_home / "ws"
        corrupt_ws.mkdir(parents=True, exist_ok=True)
        bad_json_path = corrupt_home / ".claude.json"
        bad_raw = "{broken json: do not destroy"
        bad_json_path.write_text(bad_raw, encoding="utf-8")
        rules_sync.ensure_workspace_trusted(str(corrupt_ws), home_dir=str(corrupt_home))
        self.assertEqual(bad_json_path.read_text(encoding="utf-8"), bad_raw)

    def test_claude_native_title_read_and_write_sync(self) -> None:
        sid = "11111111-2222-3333-4444-555555555555"
        jsonl_path = self.base / f"{sid}.jsonl"
        overrides_path = self.base / "title_overrides.json"
        rows = [
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "Initial Claude prompt about auth"}],
                },
            },
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Sure, inspecting auth module."}],
                },
            },
        ]
        jsonl_path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
            encoding="utf-8",
        )

        # 1. Read fallback native title from first user prompt
        initial_title = providers.read_native_title(
            "claude", sid, str(jsonl_path), overrides_path=str(overrides_path)
        )
        self.assertEqual(initial_title, "Initial Claude prompt about auth")

        # 2. Write custom title -> appends `custom-title` record to .jsonl + syncs title_overrides.json
        wres = providers.write_native_title(
            "claude",
            sid,
            str(jsonl_path),
            "Claude OAuth2 Refactor",
            overrides_path=str(overrides_path),
        )
        self.assertTrue(wres["ok"])
        self.assertTrue(wres["native_updated"])

        # Verify .jsonl tail record
        lines = [json.loads(ln) for ln in jsonl_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        self.assertEqual(lines[-1]["type"], "custom-title")
        self.assertEqual(lines[-1]["customTitle"], "Claude OAuth2 Refactor")
        self.assertEqual(lines[-1]["sessionId"], sid)

        # Verify title_overrides.json
        ov_data = json.loads(overrides_path.read_text(encoding="utf-8"))
        self.assertEqual(ov_data[f"claude:{sid}"], "Claude OAuth2 Refactor")

        # 3. Read back updated title via both `providers` and `rules_sync`
        self.assertEqual(
            providers.read_native_title("claude", sid, str(jsonl_path), overrides_path=str(overrides_path)),
            "Claude OAuth2 Refactor",
        )
        self.assertEqual(
            providers.read_native_title("claude", sid, str(jsonl_path), overrides_path=str(overrides_path)),
            "Claude OAuth2 Refactor",
        )

    def test_agy_native_title_read_and_write_sqlite_and_brain(self) -> None:
        cid_db = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        cid_brain = "99999999-8888-7777-6666-555555555555"
        agy_root = self.base / "antigravity-cli"
        agy_root.mkdir(parents=True, exist_ok=True)
        db_path = agy_root / "conversation_summaries.db"
        overrides_path = self.base / "agy_overrides.json"

        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """
            CREATE TABLE conversation_summaries (
                conversation_id TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT "",
                preview TEXT NOT NULL DEFAULT ""
            )
            """
        )
        conn.execute(
            "INSERT INTO conversation_summaries (conversation_id, title, preview) VALUES (?, ?, ?)",
            (cid_db, "AGY Original SQLite Title", "Preview text"),
        )
        conn.commit()
        conn.close()

        # 1. Read from conversation_summaries.db
        self.assertEqual(
            providers.read_native_title("agy", cid_db, str(db_path), overrides_path=str(overrides_path)),
            "AGY Original SQLite Title",
        )

        # 2. Write new title -> updates both title_overrides.json and conversation_summaries.db
        wres = providers.write_native_title(
            "agy",
            cid_db,
            str(db_path),
            "AGY Renamed Kernel Task",
            overrides_path=str(overrides_path),
        )
        self.assertTrue(wres["ok"])
        self.assertTrue(wres["native_updated"])
        self.assertEqual(
            providers.read_native_title("agy", cid_db, str(db_path), overrides_path=str(overrides_path)),
            "AGY Renamed Kernel Task",
        )

        conn = sqlite3.connect(str(db_path))
        db_row = conn.execute(
            "SELECT title FROM conversation_summaries WHERE conversation_id = ?",
            (cid_db,),
        ).fetchone()
        conn.close()
        self.assertEqual(db_row[0], "AGY Renamed Kernel Task")

        # 3. Read from brain/<cid> transcript when not in SQLite
        brain_logs = agy_root / "brain" / cid_brain / ".system_generated" / "logs"
        brain_logs.mkdir(parents=True, exist_ok=True)
        tx_file = brain_logs / "transcript.jsonl"
        tx_file.write_text(
            json.dumps(
                {
                    "step_index": 0,
                    "type": "USER_INPUT",
                    "content": "<USER_REQUEST>Build MixHub modular subpackages</USER_REQUEST>",
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        self.assertEqual(
            providers.read_native_title(
                "agy", cid_brain, str(agy_root / "brain" / cid_brain), overrides_path=str(overrides_path)
            ),
            "Build MixHub modular subpackages",
        )

    def test_codex_native_title_read_and_write_sync(self) -> None:
        sid = "019a1234-5678-7abc-def0-123456789abc"
        rollout_path = self.base / f"rollout-2026-10-24-{sid}.jsonl"
        overrides_path = self.base / "codex_overrides.json"
        rows = [
            {
                "type": "session_meta",
                "payload": {"id": sid, "cwd": str(self.workspace)},
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "# AGENTS.md instructions for repo"}],
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Fix Codex writer lock handoff"}],
                },
            },
        ]
        rollout_path.write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
            encoding="utf-8",
        )

        # 1. Reads first real user prompt (skipping # AGENTS.md envelope)
        self.assertEqual(
            providers.read_native_title("codex", sid, str(rollout_path), overrides_path=str(overrides_path)),
            "Fix Codex writer lock handoff",
        )

        # 2. Write new title -> updates title_overrides.json and returns updated title on read
        wres = providers.write_native_title(
            "codex",
            sid,
            str(rollout_path),
            "Codex Lock Release & Relay",
            overrides_path=str(overrides_path),
        )
        self.assertTrue(wres["ok"])
        self.assertEqual(
            providers.read_native_title("codex", sid, str(rollout_path), overrides_path=str(overrides_path)),
            "Codex Lock Release & Relay",
        )

    def test_mixhub_modular_subpackages_import_and_core_symbols(self) -> None:
        import mixhub
        import mixhub.channels
        import mixhub.core
        import mixhub.handoff
        import mixhub.providers

        # 1. mixhub.core: 5-state machine
        self.assertEqual(
            mixhub.core.ALL_SESSION_STATES,
            ("WORKING", "AWAITING", "UNREAD", "IDLE", "HIBERNATED"),
        )
        self.assertEqual(
            mixhub.core.classify_session_state({"isLive": True, "agentState": "working"}),
            mixhub.core.SessionState.WORKING,
        )
        self.assertEqual(
            mixhub.core.classify_session_state({"isLive": True, "agentState": "wait_input"}),
            mixhub.core.SessionState.AWAITING,
        )
        self.assertEqual(
            mixhub.core.classify_session_state(
                {"tool": "claude", "id": "s1", "isLive": True, "agentState": "done_idle", "updatedAt": 200.0},
                seen_timestamps={"claude:s1": 100.0},
            ),
            mixhub.core.SessionState.UNREAD,
        )
        self.assertEqual(
            mixhub.core.classify_session_state({"isLive": True, "agentState": "done_idle", "updatedAt": 100.0}),
            mixhub.core.SessionState.IDLE,
        )
        self.assertEqual(
            mixhub.core.classify_session_state({"isLive": False, "inWorkingSet": False}),
            mixhub.core.SessionState.HIBERNATED,
        )

        # 2. mixhub.providers: trust + title sync
        self.assertIs(mixhub.providers.ensure_workspace_trusted, rules_sync.ensure_workspace_trusted)
        self.assertIs(mixhub.providers.read_native_title, providers.read_native_title)
        self.assertIs(mixhub.providers.write_native_title, providers.write_native_title)

        # 3. mixhub.handoff: relay helpers
        redacted = mixhub.handoff.redact_secrets("token sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789")
        self.assertIsInstance(redacted, str)
        self.assertNotIn("sk-ant-api03", redacted)
        self.assertEqual(
            mixhub.handoff.resolve_handoff_mode("auto", "agy", "claude", has_errors=True),
            "escalate",
        )
        self.assertEqual(
            mixhub.handoff.resolve_handoff_mode("auto", "claude", "codex"),
            "delegate",
        )
        self.assertGreater(mixhub.handoff.estimate_tokens("hello world"), 0)

        # 4. mixhub.channels: mobile card
        card = mixhub.channels.format_mobile_card("Auth Task", "All tests passed")
        self.assertIn("Auth Task", card)
        # 5. cursor math lives with the agy PTY filter
        import mixhub.filters.agy_claude.vscreen as vscreen

        self.assertEqual(mixhub.core.textwidth.disp_width("你好"), 4)
        col = vscreen.char_idx_to_col("你好abc", 2)
        self.assertEqual(vscreen.col_to_char_idx("你好abc", col), 2)


if __name__ == "__main__":
    unittest.main()
