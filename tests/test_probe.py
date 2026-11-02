"""Unit tests for probe.py (Declarative Bottom-15-Lines Terminal Blocker Probe & macOS Auto-Away)."""
from __future__ import annotations

import plistlib
import subprocess
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import probe


class TerminalBottom15ProbeTests(unittest.TestCase):
    def test_claude_bash_permission_boxed_and_unboxed(self):
        boxed = """
╭──────────────────────────────────────────────────────────────────────────────╮
│ Bash command                                                                 │
│                                                                              │
│   pytest -q tests/test_probe.py                                              │
│   Run unit test suite                                                        │
│                                                                              │
│ Do you want to proceed?                                                      │
│ ❯ 1. Yes                                                                     │
│   2. Yes, and don't ask again for pytest commands in /Users/alice/project     │
│   3. No                                                                      │
│                                                                              │
│ Esc to cancel · Tab to amend                                                 │
╰──────────────────────────────────────────────────────────────────────────────╯
"""
        res = probe.classify_pane_text(boxed)
        self.assertTrue(res["ok"])
        self.assertEqual(res["state"], "awaiting_permission")
        self.assertEqual(res["choice_style"], "numbered")
        self.assertEqual(res["keys"], {"once": "1", "session": "2", "deny": "3"})
        self.assertIn("pytest -q tests/test_probe.py", res["command"])
        self.assertIn("Bash command", res["tool_summary"])
        self.assertTrue(res["fingerprint"])

    def test_claude_edit_session_permission_and_codex_prompt(self):
        claude_edit = """
Edit file
  src/channels.py
Do you want to make this edit to channels.py?
  1. Yes
❯ 2. Yes, allow all edits during this session (shift+tab)
  3. No
"""
        res_edit = probe.classify_pane_text(claude_edit)
        self.assertEqual(res_edit["state"], "awaiting_permission")
        self.assertEqual(res_edit["keys"], {"once": "1", "session": "2", "deny": "3"})
        self.assertTrue(res_edit["choices"][1]["selected"])

        # Cursor movement must not alter fingerprint
        claude_edit_cur1 = claude_edit.replace("  1. Yes", "❯ 1. Yes").replace("❯ 2.", "  2.")
        self.assertEqual(probe.classify_pane_text(claude_edit_cur1)["fingerprint"], res_edit["fingerprint"])

        codex_screen = """
Would you like to run the following command?
$ git push --force origin feature/away
❯ 1. Yes (y)
  2. Yes, always allow running git in this session (a)
  3. No, and tell Codex what to do differently (esc)
"""
        res_codex = probe.classify_pane_text(codex_screen)
        self.assertEqual(res_codex["state"], "awaiting_permission")
        self.assertEqual(res_codex["command"], "git push --force origin feature/away")
        self.assertEqual(res_codex["keys"], {"once": "1", "session": "2", "deny": "3"})

    def test_gemini_four_option_menu_maps_deny_to_option_4(self):
        gemini_screen = """
Allow execution of: 'npm run build && npm test'?
❯ 1. Yes, allow once
  2. Yes, allow always
  3. Modify with external editor
  4. No, suggest changes (esc)
"""
        res = probe.classify_pane_text(gemini_screen)
        self.assertEqual(res["state"], "awaiting_permission")
        self.assertEqual(res["command"], "npm run build && npm test")
        self.assertEqual(res["keys"], {"once": "1", "session": "2", "deny": "4"})

    def test_trust_folder_prompt_maps_once_and_session_to_1_and_deny_to_2(self):
        trust_screen = """
Do you trust the files in this folder?
/Users/alice/.gemini/session-hub
❯ 1. Yes, I trust this folder
  2. No, exit
Enter to confirm · Esc to exit
"""
        res = probe.classify_pane_text(trust_screen)
        self.assertEqual(res["state"], "awaiting_trust")
        self.assertEqual(res["keys"], {"once": "1", "session": "1", "deny": "2"})
        self.assertTrue(res["fingerprint"])

    def test_interactive_question_and_yn_prompt(self):
        question_screen = """
Which deployment target should we use?
❯ 1. Staging (us-west)
  2. Production (global)
  3. Local dry-run
↑/↓ to navigate · Enter to select
"""
        res_q = probe.classify_pane_text(question_screen)
        self.assertEqual(res_q["state"], "awaiting_question")
        self.assertEqual(res_q["choice_style"], "numbered")
        self.assertEqual(len(res_q["choices"]), 3)

        yn_screen = """
$ rm -rf dist/build_cache
Allow deleting cached build artifacts? [y/N]
"""
        res_yn = probe.classify_pane_text(yn_screen)
        self.assertEqual(res_yn["state"], "awaiting_permission")
        self.assertEqual(res_yn["choice_style"], "yn")
        self.assertEqual(res_yn["keys"], {"once": "y", "session": None, "deny": "n"})
        self.assertEqual(res_yn["command"], "rm -rf dist/build_cache")

    def test_agy_hotkey_approval_box_and_scrolled_history_counterexample(self):
        agy_approval_screen = """
子代理刚跑完点点的两轮追问，马上把点点的完整回答交回来！

• Messaged 1 subagent (ctrl+o to expand)

点点子代理正在收尾，跑完即刻唤醒。

│ self needs approval for chrome-devtools/navigate_page
│ ────────────────────────────────────────────────────────────
│ ● chrome-devtools/navigate_page({"pageId":4,"type":"url","url":"about:blank"})
│ ctrl+k approve · alt+j manage

> 
"""
        res = probe.classify_pane_text(agy_approval_screen)
        self.assertTrue(res["ok"])
        self.assertEqual(res["state"], "awaiting_permission")
        self.assertEqual(res["choice_style"], "hotkey")
        self.assertEqual(res["agent"], "self")
        self.assertEqual(res["tool"], "chrome-devtools/navigate_page")
        self.assertEqual(res["keys"].get("once"), "C-k")
        self.assertEqual(res["keys"].get("manage"), "M-j")
        self.assertIsNone(res["keys"].get("deny"))
        choice_pairs = [(c.get("id"), c.get("key")) for c in res["choices"]]
        self.assertEqual(choice_pairs, [("approve", "C-k"), ("manage", "M-j")])
        self.assertIn("navigate_page", res["command"])
        self.assertTrue(res["fingerprint"])

        # Counterexample 1: Same approval text scrolled up in history above completed output and prompt
        scrolled_history_screen = """
│ self needs approval for chrome-devtools/navigate_page
│ ────────────────────────────────────────────────────────────
│ ● chrome-devtools/navigate_page({"pageId":4,"type":"url","url":"about:blank"})
│ ctrl+k approve · alt+j manage
• chrome-devtools/navigate_page completed
页面已跳转完成。
> 
"""
        res_scrolled = probe.classify_pane_text(scrolled_history_screen)
        self.assertEqual(res_scrolled["state"], "idle")

        # Counterexample 2: Approval box above an active spinner after approval was granted
        spinner_after_approval = """
│ self needs approval for chrome-devtools/navigate_page
│ ctrl+k approve · alt+j manage
✻ Running navigate_page… (esc to interrupt · 2s)
> 
"""
        res_working = probe.classify_pane_text(spinner_after_approval)
        self.assertEqual(res_working["state"], "working")

    def test_working_vs_idle_and_false_positive_guards(self):
        # 1. Active spinner / esc to interrupt -> working
        working_screen = """
⏺ I will inspect the tests now.
✻ Cooking… (esc to interrupt · 14s)
"""
        self.assertEqual(probe.classify_pane_text(working_screen)["state"], "working")
        # OpenCode's status row drops the "to"
        opencode_screen = """
  ┃  Build · GPT-6.1 Terra Pro OpenRouter
   ⬝⬝⬝⬝⬝■■■ esc interrupt                                     $0.00  ctrl+p commands
"""
        self.assertEqual(probe.classify_pane_text(opencode_screen)["state"], "working")

        # 2. Braille spinner in pane_title -> working
        self.assertEqual(probe.classify_pane_text("❯ ", pane_title="⠋ claude")["state"], "working")

        # 3. Completed "✻ Cooked for 28s" -> idle (NOT working!)
        cooked_screen = """
⏺ All 17 unit tests passed.
✻ Cooked for 28s
❯ 
"""
        self.assertEqual(probe.classify_pane_text(cooked_screen)["state"], "idle")

        # 4. Numbered markdown list in assistant transcript followed by prompt -> idle
        transcript_list = """
⏺ Summary of changes:
  1. Yes, we updated probe.py
  2. Added macOS HIDIdleTime check
  3. Wired Telegram callback_query
❯ 
"""
        self.assertEqual(probe.classify_pane_text(transcript_list)["state"], "idle")

        # 5. User unsent draft starting with "❯ 1. fix this" -> idle
        draft_screen = """
⏺ Ready.
❯ 1. fix this bug in probe.py
"""
        self.assertEqual(probe.classify_pane_text(draft_screen)["state"], "idle")

    def test_long_multiline_command_extracted_from_context_above_tail_15(self):
        lines = [
            "Bash command",
            "$ python3 -m unittest discover -s tests -p 'test_*.py' -v",
        ] + [f"  --arg-{i}=value_{i}" for i in range(14)] + [
            "Do you want to proceed?",
            "❯ 1. Yes",
            "  2. Yes, and don't ask again",
            "  3. No",
        ]
        res = probe.classify_pane_text("\n".join(lines))
        self.assertEqual(res["state"], "awaiting_permission")
        self.assertEqual(len(res["tail"]), 15)
        self.assertIn("python3 -m unittest discover", res["command"])

    def test_resolve_pool_pane_and_probe_session_pane(self):
        calls = []

        def fake_runner(argv):
            calls.append(argv)
            cmd_str = " ".join(argv)
            if "list-windows" in cmd_str:
                return "reader\nw-claude-111122223333\n"
            if "display" in cmd_str:
                return "%42\t28\t0\tclaude"
            if "capture-pane" in cmd_str:
                return "\n".join([f"line-{i}" for i in range(1, 25)] + ["Do you want to proceed? [y/n]"])
            return ""

        probed = probe.probe_session_pane("claude:1111222233334444", runner=fake_runner)
        self.assertTrue(probed["ok"])
        self.assertEqual(probed["window"], "w-claude-111122223333")
        self.assertEqual(probed["pane_id"], "%42")
        self.assertEqual(probed["state"], "awaiting_permission")
        self.assertEqual(probed["keys"], {"once": "y", "session": None, "deny": "n"})

        # Header pane (height <= 2) must be refused
        def header_runner(argv):
            if "list-windows" in " ".join(argv):
                return "w-claude-111122223333\n"
            if "display" in " ".join(argv):
                return "%16\t2\t0\theader"
            return ""

        refused = probe.resolve_pool_pane("111122223333", runner=header_runner)
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["error"], "refusing_header_pane")


class TwoLayerStructuralAndHotReloadTests(unittest.TestCase):
    def test_layer1_structural_geometry_detects_unknown_cli_wording_and_extracts_snippet(self):
        # Completely novel CLI wording (no "Do you want to proceed?", no "Yes"/"No" keywords)
        novel_pane = """
        ╭──────────────────────────────────────────────────────────────╮
        │ Sandbox Gate v9                                              │
        │   curl -H "Authorization: Bearer sk-secret1234567890abcd"    │
        │    Confirm action on production cluster:                      │
        │ ❯ 1. Go ahead once                                           │
        │   2. Remember for workspace                                  │
        │   3. Hold off                                                │
        ╰──────────────────────────────────────────────────────────────╯
        """
        res = probe.classify_pane_text(novel_pane)
        self.assertEqual(res["state"], "awaiting_permission")
        self.assertEqual(res["match_layer"], "structural")
        self.assertEqual(res["rule"], "structural_permission_menu")
        self.assertEqual(res["keys"], {"once": "1", "session": "2", "deny": "3"})
        self.assertEqual(
            res["keys_source"],
            {"once": "positional", "session": "positional", "deny": "positional"},
        )
        self.assertLessEqual(len(res["raw_excerpt"]), 6)
        self.assertIn("❯ 1. Go ahead once", res["prompt_snippet"])
        self.assertNotIn("sk-secret1234567890abcd", res["prompt_snippet"])
        self.assertIn("[REDACTED]", res["prompt_snippet"])

        # Novel [y/N] prompt phrasing also hits structural layer
        novel_yn = """
        Stage-2 migration ready for /srv/app
        Commit schema changes now? [y/N]
        """
        res_yn = probe.classify_pane_text(novel_yn)
        self.assertEqual(res_yn["state"], "awaiting_permission")
        self.assertEqual(res_yn["match_layer"], "structural")
        self.assertEqual(res_yn["choice_style"], "yn")
        self.assertEqual(res_yn["keys"], {"once": "y", "session": None, "deny": "n"})

    def test_opus_four_false_positive_guards_cases_a_b_e_f(self):
        # Case A: Assistant numbered list 1..3 above composer rule + user draft `❯ 4. also bump version`
        case_a = """
        ⏺ Plan:
          1. Update schema
          2. Run migrations
          3. Restart workers
        ────────────────────────────────────────────────────────
        ❯ 4. also bump version
        ────────────────────────────────────────────────────────
          ? for shortcuts
        """
        res_a = probe.classify_pane_text(case_a)
        self.assertEqual(res_a["state"], "idle")

        # Case B: Old permission prompt above an active spinner (`esc to interrupt`)
        case_b = """
        Do you want to proceed?
        ❯ 1. Yes
          2. No
        ✻ Thinking… (esc to interrupt · 4s)
        """
        res_b = probe.classify_pane_text(case_b)
        self.assertEqual(res_b["state"], "working")

        # Case E: Tool stdout output under `⎿` containing `[y/N]`
        case_e = """
        ⏺ Bash(./deploy.sh)
          ⎿  Checking environment...
          ⎿  Continue deployment? [y/N]
        """
        res_e = probe.classify_pane_text(case_e)
        self.assertEqual(res_e["state"], "idle")

        # Case F: Assistant transcript prose (`⏺`) quoting an un-cursored numbered list
        case_f = """
        ⏺ Do you want to allow these edits? Options:
          1. Yes
          2. No
        """
        res_f = probe.classify_pane_text(case_f)
        self.assertEqual(res_f["state"], "idle")

    def test_layer2_probe_rules_json_hot_reload_and_corrupt_json_resilience(self):
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            rules_file = os.path.join(tmpdir, "probe_rules.json")

            # 1. File does not exist -> falls back to built-in rules
            r0 = probe.load_probe_rules(rules_file)
            self.assertEqual(r0["source"], "builtin")
            self.assertIsNone(r0["sig"])

            future_cli_screen = """
            ╭──────────────────────────────────────────────╮
            │ AgentV10 Security Check                      │
            │   Shall we dispatch this shell task?         │
            │ ❯ 1. Dispatch once                           │
            │   2. Dispatch for entire session             │
            │   3. Veto task                               │
            ╰──────────────────────────────────────────────╯
            """
            # Before adding custom rule, Layer 1 structural heuristic catches it
            c_before = probe.classify_pane_text(future_cli_screen, rules_path=rules_file)
            self.assertEqual(c_before["state"], "awaiting_permission")
            self.assertEqual(c_before["match_layer"], "structural")

            # 2. Write custom rules to probe_rules.json -> hot-reloads on mtime/size change without code changes
            custom_cfg = {
                "version": 1,
                "permission_patterns": [r"Shall we dispatch this shell task\?"],
                "once_patterns": [r"^Dispatch once\b"],
                "session_patterns": [r"entire session\b"],
                "deny_patterns": [r"^Veto task\b"],
                "false_positive_patterns": [r"MockInteractiveTutorial:"],
            }
            with open(rules_file, "w", encoding="utf-8") as fh:
                json.dump(custom_cfg, fh)

            c_after = probe.classify_pane_text(future_cli_screen, rules_path=rules_file)
            self.assertEqual(c_after["state"], "awaiting_permission")
            self.assertEqual(c_after["match_layer"], "rules")
            self.assertEqual(c_after["keys"], {"once": "1", "session": "2", "deny": "3"})
            self.assertEqual(
                c_after["keys_source"],
                {"once": "semantic", "session": "semantic", "deny": "semantic"},
            )

            # Custom false_positive_patterns vetoes matching screens
            vetoed_screen = """
            MockInteractiveTutorial:
            Shall we dispatch this shell task?
            ❯ 1. Dispatch once
              2. Veto task
            """
            c_veto = probe.classify_pane_text(vetoed_screen, rules_path=rules_file)
            self.assertEqual(c_veto["state"], "idle")

            # 3. Corrupt JSON written mid-save retains last-good compiled rules and reports error
            with open(rules_file, "w", encoding="utf-8") as fh:
                fh.write("{broken json,,,")
            r_broken = probe.load_probe_rules(rules_file, force=True)
            self.assertTrue(r_broken["error"])
            c_still_works = probe.classify_pane_text(future_cli_screen, rules_path=rules_file)
            self.assertEqual(c_still_works["match_layer"], "rules")

    def test_server_probe_override_and_subagent_priority_rollup(self):
        import json
        import sqlite3
        import tempfile
        import time
        from pathlib import Path
        import server

        parent_id = "22222222-2222-4222-8222-222222222222"
        child_id = "99999999-9999-4999-8999-999999999999"

        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            root = home / "hub"
            root.mkdir(parents=True, exist_ok=True)
            now = time.time()

            # Seed Antigravity DB with parent (not_fully_idle=1, status=RUNNING) and child subagent
            agy_db = home / ".gemini/antigravity-cli/conversation_summaries.db"
            agy_db.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(agy_db)
            conn.execute(
                "CREATE TABLE conversation_summaries("
                "conversation_id TEXT, title TEXT, preview TEXT, workspace_uris TEXT, "
                "parent_conversation_id TEXT, step_count INTEGER, status TEXT, not_fully_idle INTEGER)"
            )
            conn.execute(
                "INSERT INTO conversation_summaries VALUES(?,?,?,?,?,?,?,?)",
                (parent_id, "Chrome Browser Automation", "preview", '["file:///workspace"]', "", 5, "RUNNING", 1),
            )
            conn.execute(
                "INSERT INTO conversation_summaries VALUES(?,?,?,?,?,?,?,?)",
                (child_id, "Subagent self", "preview", '["file:///workspace"]', parent_id, 3, "RUNNING", 1),
            )
            conn.commit()
            conn.close()

            def write_jsonl(p: Path, rows: list[dict], mtime: float):
                p.parent.mkdir(parents=True, exist_ok=True)
                with p.open("w", encoding="utf-8") as fh:
                    for r in rows:
                        fh.write(json.dumps(r, ensure_ascii=False) + "\n")
                os.utime(p, (mtime, mtime))

            parent_tx = home / f".gemini/antigravity-cli/brain/{parent_id}/.system_generated/logs/transcript.jsonl"
            write_jsonl(
                parent_tx,
                [
                    {"step_index": 0, "type": "USER_INPUT", "content": "<USER_REQUEST>Open browser</USER_REQUEST>"},
                    {
                        "step_index": 1,
                        "type": "PLANNER_RESPONSE",
                        "content": "Messaged 1 subagent",
                        "tool_calls": [{"name": "invoke_subagent", "args": {"conversationId": child_id}}],
                    },
                ],
                mtime=now - 5,
            )

            # Child subagent has an unclosed tool_call (navigate_page blocked on approval)
            child_tx = home / f".gemini/antigravity-cli/brain/{child_id}/.system_generated/logs/transcript.jsonl"
            write_jsonl(
                child_tx,
                [
                    {"step_index": 0, "type": "USER_INPUT", "content": "Navigate to about:blank"},
                    {
                        "step_index": 1,
                        "type": "PLANNER_RESPONSE",
                        "content": "Calling navigate_page",
                        "tool_calls": [
                            {"name": "mcp_chrome-devtools_navigate_page", "args": {"pageId": 4, "url": "about:blank"}}
                        ],
                    },
                ],
                mtime=now - 2,
            )

            hub = server.Hub(str(home), str(root))
            hub.processes = lambda: [
                {
                    "pid": 4242,
                    "ppid": 1,
                    "tty": "ttys008",
                    "rss_mb": 120.0,
                    "etime": "01:00",
                    "tool": "agy",
                    "args": ["agy", "--conversation", parent_id],
                    "wrapper": False,
                }
            ]
            hub.open_files = lambda _: {}

            # 1. Without terminal probe, child's unclosed_tool + RUNNING DB makes parent "working"
            s0 = hub.discover(force=True)["agy:" + parent_id]
            self.assertEqual(s0["agentState"], "working")

            # 2. With fresh terminal probe seeing agy approval box, parent flips to "wait_input"
            #    and child subagent rollup MUST NOT overwrite it back to "working"!
            pane_probe = probe.classify_pane_text(
                """
                │ self needs approval for chrome-devtools/navigate_page
                │ ────────────────────────────────────────────────────────────
                │ ● chrome-devtools/navigate_page({"pageId":4,"type":"url","url":"about:blank"})
                │ ctrl+k approve · alt+j manage
                > 
                """
            )
            self.assertEqual(pane_probe["state"], "awaiting_permission")
            hub.set_pane_probe(f"agy:{parent_id}", pane_probe)
            s1 = hub.discover(force=True)["agy:" + parent_id]
            self.assertEqual(s1["agentState"], "wait_input")
            self.assertEqual(s1.get("stateSource"), "probe")

            # 3. Stale probe (> 5.0s old) is ignored -> falls back to transcript/DB state ("working")
            hub.set_pane_probe(f"agy:{parent_id}", pane_probe, mono_ts=time.monotonic() - 6.0)
            s2 = hub.discover(force=True)["agy:" + parent_id]
            self.assertEqual(s2["agentState"], "working")

            # 4. When probe transitions to "working" after user presses ctrl+k, immediately exits wait_input
            working_probe = probe.classify_pane_text("✻ Navigating… (esc to interrupt · 1s)\n> ")
            hub.set_pane_probe(f"agy:{parent_id}", working_probe)
            s3 = hub.discover(force=True)["agy:" + parent_id]
            self.assertEqual(s3["agentState"], "working")


if __name__ == "__main__":
    unittest.main()
