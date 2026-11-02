#!/usr/bin/env python3
"""Unit tests for rules_sync.py CLAUDE.md read-only protection and cc-deepseek API_TIMEOUT_MS."""
from __future__ import annotations

import builtins
import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import providers
import rules_sync
from rules_sync import CLAUDE_MD_READONLY_MSG, _assert_never_write_claude_md, sync_rules


def _snap(path: Path) -> tuple[int, int, int, str]:
    st = os.stat(path)
    return st.st_mtime_ns, st.st_ino, st.st_size, hashlib.sha256(path.read_bytes()).hexdigest()


class RulesSyncReadOnlyAndDeepSeekTimeoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.home = self.base / "home"
        (self.home / ".claude").mkdir(parents=True, exist_ok=True)
        self.proj = self.base / "proj"
        self.proj.mkdir(parents=True, exist_ok=True)
        self.state = str(self.base / "state")
        (self.proj / "CLAUDE.md").write_text("# rules\n- hand written\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _with_no_claude_writes(self):
        real_open = builtins.open
        real_replace = os.replace
        real_rename = os.rename

        def guarded_open(file, mode="r", *a, **kw):
            if any(c in mode for c in "wax+") and os.path.basename(str(file)).casefold().startswith("claude.md"):
                raise AssertionError(f"write-open on {file}")
            return real_open(file, mode, *a, **kw)

        def guarded_replace(src, dst, *a, **kw):
            if os.path.basename(str(dst)).casefold() == "claude.md":
                raise AssertionError(f"os.replace onto {dst}")
            return real_replace(src, dst, *a, **kw)

        def guarded_rename(src, dst, *a, **kw):
            if os.path.basename(str(dst)).casefold() == "claude.md":
                raise AssertionError(f"os.rename onto {dst}")
            return real_rename(src, dst, *a, **kw)

        return (
            patch.dict(os.environ, {"HOME": str(self.home), "HUB_HOME": str(self.home)}),
            patch("mixhub.rules.sync.ensure_workspace_trusted", return_value={"stub": True}),
            patch.object(builtins, "open", side_effect=guarded_open),
            patch.object(os, "replace", side_effect=guarded_replace),
            patch.object(os, "rename", side_effect=guarded_rename),
        )

    def test_default_sync_never_touches_claude_md(self) -> None:
        (self.proj / "AGENTS.md").write_text("# agents\n- rule A\n", encoding="utf-8")
        before = _snap(self.proj / "CLAUDE.md")
        p_env, p_trust, p_open, p_rep, p_ren = self._with_no_claude_writes()
        with p_env, p_trust, p_open, p_rep, p_ren:
            for kw in ({}, {"force": True}, {"force": True, "approve_policy": True}, {"apply": True}):
                res = sync_rules(str(self.proj), state_dir=self.state, dry_run=False, **kw)
                self.assertEqual(_snap(self.proj / "CLAUDE.md"), before)
                info = res["files"]["CLAUDE.md"]
                self.assertFalse(info["wrote"])
                self.assertTrue(info["read_only"])
                self.assertEqual(info["protection_notice"], "read-only source of truth; write skipped")
                self.assertTrue(info["changed"])
                self.assertTrue(info["diff"].startswith("--- a/CLAUDE.md"))
                self.assertIn("rule A", (self.proj / ".gemini" / "GEMINI.md").read_text(encoding="utf-8"))

    def test_bootstrap_reads_claude_md_only(self) -> None:
        before = _snap(self.proj / "CLAUDE.md")
        p_env, p_trust, p_open, p_rep, p_ren = self._with_no_claude_writes()
        with p_env, p_trust, p_open, p_rep, p_ren:
            res = sync_rules(str(self.proj), state_dir=self.state)
            self.assertEqual(res["bootstrapped_from"], "CLAUDE.md")
            self.assertIn("hand written", (self.proj / "AGENTS.md").read_text(encoding="utf-8"))
            self.assertIn("hand written", (self.proj / ".gemini" / "GEMINI.md").read_text(encoding="utf-8"))
            self.assertEqual(_snap(self.proj / "CLAUDE.md"), before)

    def test_from_claude_exports_one_way_and_is_idempotent(self) -> None:
        before = _snap(self.proj / "CLAUDE.md")
        p_env, p_trust, p_open, p_rep, p_ren = self._with_no_claude_writes()
        with p_env, p_trust, p_open, p_rep, p_ren:
            res = sync_rules(str(self.proj), state_dir=self.state, source="claude", force=True)
            self.assertTrue(res["files"]["AGENTS.md"]["wrote"])
            self.assertIn("hand written", (self.proj / ".gemini" / "GEMINI.md").read_text(encoding="utf-8"))
            res2 = sync_rules(str(self.proj), state_dir=self.state, source="claude")
            self.assertFalse(res2["files"]["AGENTS.md"]["wrote"])
            self.assertFalse(res2["files"][".gemini/GEMINI.md"]["wrote"])
            self.assertEqual(_snap(self.proj / "CLAUDE.md"), before)

    def test_from_claude_refuses_self_import(self) -> None:
        (self.proj / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            sync_rules(str(self.proj), state_dir=self.state, source="claude")
        self.assertFalse((self.proj / "AGENTS.md").exists())

    def test_explicit_claude_target_raises_permission_error(self) -> None:
        before = _snap(self.proj / "CLAUDE.md")
        cases = (
            {"target": "claude"},
            {"target": "CLAUDE.md"},
            {"targets": ["gemini", "claude"]},
            {"targets": ["~/.claude/CLAUDE.md"]},
            {"target": "claude", "force": True, "approve_policy": True},
            {"target": "claude", "apply": True},
        )
        for kw in cases:
            with self.subTest(kw=kw):
                with self.assertRaisesRegex(PermissionError, "read-only source of truth"):
                    sync_rules(str(self.proj), state_dir=self.state, dry_run=False, **kw)
                self.assertEqual(_snap(self.proj / "CLAUDE.md"), before)
                self.assertFalse((self.proj / "AGENTS.md").exists())

    def test_claude_target_dry_run_is_preview_only(self) -> None:
        (self.proj / "AGENTS.md").write_text("# agents\n", encoding="utf-8")
        before = _snap(self.proj / "CLAUDE.md")
        p_env, p_trust, p_open, p_rep, p_ren = self._with_no_claude_writes()
        with p_env, p_trust, p_open, p_rep, p_ren:
            res = sync_rules(str(self.proj), state_dir=self.state, dry_run=True, target="claude")
            self.assertEqual(list(res["files"]), ["CLAUDE.md"])
            self.assertTrue(bool(res["files"]["CLAUDE.md"]["diff"]))
            self.assertEqual(_snap(self.proj / "CLAUDE.md"), before)

    def test_guard_names_and_aliases(self) -> None:
        for name in ("CLAUDE.md", "claude.md", "Claude.MD"):
            with self.assertRaisesRegex(PermissionError, CLAUDE_MD_READONLY_MSG):
                _assert_never_write_claude_md(str(self.proj / name))
        _assert_never_write_claude_md(str(self.proj / "AGENTS.md"))

        link = self.proj / "AGENTS.md"
        link.symlink_to("CLAUDE.md")
        with self.assertRaises(PermissionError):
            _assert_never_write_claude_md(str(link))
        link.unlink()
        os.link(self.proj / "CLAUDE.md", link)
        with self.assertRaises(PermissionError):
            _assert_never_write_claude_md(str(link))

    def test_from_claude_with_symlinked_agents_md_refuses(self) -> None:
        (self.proj / "AGENTS.md").symlink_to("CLAUDE.md")
        before = _snap(self.proj / "CLAUDE.md")
        with self.assertRaises(PermissionError):
            sync_rules(str(self.proj), state_dir=self.state, source="claude")
        self.assertEqual(_snap(self.proj / "CLAUDE.md"), before)

    def test_cc_deepseek_api_timeout_ms_600000(self) -> None:
        p = providers.HARNESS_BRAIN_PRESETS["cc-deepseek"]
        env = p["launch_env_builder"](p, "sk-test", "")
        self.assertEqual(env["API_TIMEOUT_MS"], "600000")
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "https://api.deepseek.com/anthropic")
        self.assertEqual(env["ANTHROPIC_MODEL"], "deepseek-chat")

        p_kimi = providers.HARNESS_BRAIN_PRESETS["cc-kimi"]
        self.assertNotIn("API_TIMEOUT_MS", p_kimi["launch_env_builder"](p_kimi, "k", ""))


if __name__ == "__main__":
    unittest.main()
