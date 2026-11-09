import datetime
import json
import re
import os
import shlex
import signal
import sqlite3
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mixhub.hub.summary
import server
from mixhub.tui.hubtui import evq
from mixhub.proc.guard import capture_process_identity

C = "11111111-1111-4111-8111-111111111111"
A = "22222222-2222-4222-8222-222222222222"
X = "33333333-3333-4333-8333-333333333333"
CHILD = "44444444-4444-4444-8444-444444444444"


def claude_user(text):
    return {"type": "user", "cwd": "/workspace", "message": {"role": "user", "content": text}}


class HubTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.root = self.home / "hub"
        self.root.mkdir()
        self.hub = server.Hub(str(self.home), str(self.root))
        self.hub.processes = lambda: []
        self.hub.open_files = lambda _: {}
        self.claude_path = self.home / (".claude/projects/project/" + C + ".jsonl")
        self.write_rows(self.claude_path, [claude_user("first real prompt"), {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "answer"}]}}, claude_user("last real prompt")])

    def tearDown(self):
        self.temp.cleanup()

    def write_json(self, path, value):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value), encoding="utf-8")

    def write_rows(self, path, rows):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def make_agy(self, parent="", cwd="file:///workspace"):
        path = self.home / ".gemini/antigravity-cli/conversation_summaries.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE conversation_summaries(conversation_id TEXT, title TEXT, preview TEXT, workspace_uris TEXT, parent_conversation_id TEXT, step_count INTEGER)")
        db.execute("INSERT INTO conversation_summaries VALUES(?,?,?,?,?,?)", (A, "Agy title", "preview", json.dumps([cwd]), parent, 3))
        db.commit()
        db.close()
        path = self.home / (".gemini/antigravity-cli/brain/" + A + "/.system_generated/logs/transcript.jsonl")
        self.write_rows(path, [{"step_index": 0, "type": "USER_INPUT", "content": "<USER_REQUEST>real agy input</USER_REQUEST><ADDITIONAL_METADATA>ignore</ADDITIONAL_METADATA>"}, {"step_index": 1, "type": "PLANNER_RESPONSE", "thinking": "reasoning", "content": "answer", "tool_calls": [{"name": "read_file", "args": {"path": "a"}}]}, {"step_index": 2, "type": "GENERIC", "content": "result"}])
        return path

    def make_codex(self):
        path = self.home / ".codex/state_5.sqlite"
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path)
        # Deliberately different order and missing optional columns.
        db.execute("CREATE TABLE threads(cwd TEXT, id TEXT, first_user_message TEXT, title TEXT, extra TEXT)")
        db.execute("INSERT INTO threads VALUES(?,?,?,?,?)", ("/workspace", X, "db first", "DB title", "ignored"))
        db.commit()
        db.close()
        path = self.home / (".codex/sessions/2026/10/23/rollout-test-" + X + ".jsonl")
        self.write_rows(path, [{"type": "session_meta", "payload": {"id": X, "cwd": "/workspace", "timestamp": "2026-10-23T00:00:00Z"}}, {"type": "response_item", "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "# AGENTS.md instructions\nnoise"}, {"type": "input_text", "text": "<environment_context>private config</environment_context>\nactual codex question"}]}}, {"type": "response_item", "payload": {"type": "function_call", "name": "shell", "arguments": "ls", "call_id": "call1"}}, {"type": "response_item", "payload": {"type": "function_call_output", "call_id": "call1", "output": "result"}}])
        return path

    def listed(self, **query):
        data = self.hub.listing({k: [str(v)] for k, v in query.items()})
        return list(data["sessions"])

    def test_claude_title_priority_and_derived_slug(self):
        path = self.home / ".claude/sessions/123.json"
        self.write_json(path, {"pid": 123, "sessionId": C, "name": "dev-97", "nameSource": "derived"})
        s = self.hub.get("claude", C, True)
        self.assertEqual(s["title"], "first real prompt")
        self.assertEqual(s["slug"], "dev-97")
        with self.claude_path.open("a") as f:
            f.write(json.dumps({"type": "ai-title", "aiTitle": "AI title"}) + "\n")
        self.assertEqual(self.hub.get("claude", C, True)["title"], "AI title")
        with self.claude_path.open("a") as f:
            f.write(json.dumps({"type": "custom-title", "customTitle": "custom title"}) + "\n")
        self.assertEqual(self.hub.get("claude", C, True)["title"], "custom title")
        self.write_json(path, {"pid": 123, "sessionId": C, "name": "user title", "nameSource": "user"})
        self.assertEqual(self.hub.get("claude", C, True)["title"], "user title")
        self.write_json(self.home / ".claude/session_labels.json", {C: "label title"})
        self.assertEqual(self.hub.get("claude", C, True)["title"], "label title")
        self.hub.action("claude", C, "rename", {"title": "hub title"})
        self.assertEqual(self.hub.get("claude", C)["title"], "hub title")

    def test_claude_latest_title_unification_and_sidecar_sync(self):
        project = self.home / ".claude/projects/project"
        sid = lambda n: f"5555555{n}-5555-4555-8555-555555555555"
        t0 = time.time() - 1000

        def filler(n_bytes, tag):
            rows, total = [], 0
            while total < n_bytes:
                row = {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": f"{tag} {len(rows)} " + "f" * 1000}]}}
                rows.append(row)
                total += len(json.dumps(row)) + 1
            return rows

        def big_head(text):
            return [claude_user(text), {"type": "attachment", "attachment": {"type": "file", "content": "A" * 200 * 1024}}]

        def append(path, *rows):
            with Path(path).open("a", encoding="utf-8") as f:
                for row in rows:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")

        def native_rename(title, sess):
            # Claude Code 2.1.x `/rename` order: custom-title, then agent-name.
            return ({"type": "custom-title", "customTitle": title, "sessionId": sess},
                    {"type": "agent-name", "agentName": title, "sessionId": sess})

        # 1. 64 KiB tail catches an ai-title 35 KiB before EOF; a 1.5 MiB-deep one needs the cold reverse scan.
        self.assertEqual(server.TAIL_BYTES, 64 * 1024)
        self.assertGreaterEqual(server.INDEX_CACHE_SCHEMA_VERSION, 15)
        near, deep = project / (sid(1) + ".jsonl"), project / (sid(2) + ".jsonl")
        self.write_rows(near, big_head("near prompt") + [{"type": "ai-title", "aiTitle": "Near AI Title"}] + filler(35 * 1024, "near"))
        self.write_rows(deep, big_head("deep prompt") + [{"type": "ai-title", "aiTitle": "Deep AI Title"}] + filler(1536 * 1024, "deep"))
        with patch.object(mixhub.hub.summary, "claude_reverse_title_scan", wraps=server.claude_reverse_title_scan) as rev:
            self.assertEqual(self.hub.summary(str(near), "claude")["aiTitle"], "Near AI Title")
            rev.assert_not_called()
            first = self.hub.summary(str(deep), "claude")
            self.assertEqual(first["aiTitle"], "Deep AI Title")
            self.assertEqual(rev.call_count, 1)
            self.assertEqual(first["_titleScanEnd"], deep.stat().st_size)

            # 2. Incremental carry-forward: a custom-title pushed out of the tail by 100 KiB of filler still wins,
            # the deep ai-title is carried forward, and no second backward scan runs.
            append(deep, {"type": "custom-title", "customTitle": "Fresh Custom Title", "sessionId": sid(2)}, *filler(100 * 1024, "more"))
            second = self.hub.summary(str(deep), "claude")
            self.assertEqual(rev.call_count, 1)
        self.assertEqual(second["customTitle"], "Fresh Custom Title")
        self.assertEqual(second["aiTitle"], "Deep AI Title")
        self.assertNotIn("customTitleFromTail", second)
        self.assertEqual(self.hub.get("claude", sid(2), True)["title"], "Fresh Custom Title")

        # 3. `<project_dir>/<sid>/custom-title.json` sidecar vs transcript custom-title: tail record, then mtime.
        side = project / (sid(3) + ".jsonl")
        sidecar = project / sid(3) / "custom-title.json"
        self.write_rows(side, [claude_user("sidecar session prompt")])
        self.write_json(sidecar, {"customTitle": "Sidecar Title"})
        self.assertEqual(self.hub.get("claude", sid(3), True)["title"], "Sidecar Title")
        append(side, {"type": "custom-title", "customTitle": "Transcript Title", "sessionId": sid(3)})
        os.utime(sidecar, (t0, t0))
        os.utime(side, (t0 + 100, t0 + 100))
        self.assertEqual(self.hub.get("claude", sid(3), True)["title"], "Transcript Title")
        os.utime(sidecar, (t0 + 200, t0 + 200))
        self.assertEqual(self.hub.get("claude", sid(3), True)["title"], "Sidecar Title")

        # 4. Claude `agent-name` titles the session without ever setting the agy subagent flag `agentName`.
        agent = project / (sid(4) + ".jsonl")
        self.write_rows(agent, [claude_user("agent session prompt"), {"type": "agent-name", "agentName": "Agent Title", "sessionId": sid(4)}])
        s = self.hub.get("claude", sid(4), True)
        self.assertEqual(s["title"], "Agent Title")
        self.assertFalse(s.get("agentName"))
        self.assertFalse(s["isSubagent"])
        self.assertFalse(server.is_temp_session(s))
        self.assertIn(sid(4), [x["id"] for x in self.listed()])
        # An agent-name equal to the derived slug is not a user-chosen title.
        slugged = project / (sid(9) + ".jsonl")
        self.write_rows(slugged, [claude_user("slug session prompt"), {"type": "agent-name", "agentName": "dev-42", "sessionId": sid(9)}])
        self.write_json(self.home / ".claude/sessions/4242.json", {"pid": 4242, "sessionId": sid(9), "name": "dev-42", "nameSource": "derived"})
        self.assertEqual(self.hub.get("claude", sid(9), True)["title"], "slug session prompt")

        # 5. titleNativeSeen: Claude re-appending the old native title keeps the Hub rename; a brand-new native title wins.
        seen = project / (sid(5) + ".jsonl")
        self.write_rows(seen, [claude_user("seen session prompt"), *native_rename("OldTitle", sid(5))])
        os.utime(seen, (t0, t0))
        self.assertEqual(self.hub.get("claude", sid(5), True)["title"], "OldTitle")
        self.hub.action("claude", sid(5), "rename", {"title": "HubTitle"})
        self.assertEqual(self.hub.get("claude", sid(5), True)["title"], "HubTitle")
        stored = self.hub.state()["sessions"]["claude:" + sid(5)]
        self.assertEqual(stored["title"], "HubTitle")
        self.assertIn("OldTitle", stored["titleNativeSeen"])
        self.assertIn("HubTitle", stored["titleNativeSeen"])
        append(seen, *native_rename("OldTitle", sid(5)))  # reAppendSessionMetadata from the stale CLI process
        self.assertEqual(self.hub.get("claude", sid(5), True)["title"], "HubTitle")
        append(seen, *native_rename("NewRemoteTitle", sid(5)))
        self.assertEqual(self.hub.get("claude", sid(5), True)["title"], "NewRemoteTitle")

        # 6. Native rename sync: custom-title + agent-name records and the sidecar, never `sessions/<pid>.json`.
        synced = project / (sid(6) + ".jsonl")
        synced.write_text(json.dumps(claude_user("sync session prompt")), encoding="utf-8")  # no trailing newline
        os.utime(synced, (t0, t0))
        pid_file = self.home / ".claude/sessions/6262.json"
        self.write_json(pid_file, {"pid": 6262, "sessionId": sid(6), "name": "pid user title", "nameSource": "user"})
        pid_before = (pid_file.read_bytes(), pid_file.stat().st_mtime_ns)
        self.hub.get("claude", sid(6), True)
        self.hub.action("claude", sid(6), "rename", {"title": "Synced Title"})
        lines = [json.loads(line) for line in synced.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[1:], [{"type": "agent-name", "agentName": "Synced Title", "sessionId": sid(6)},
                                     {"type": "custom-title", "customTitle": "Synced Title", "sessionId": sid(6)}])
        self.assertAlmostEqual(synced.stat().st_mtime, t0, places=3)
        self.assertEqual(json.loads((project / sid(6) / "custom-title.json").read_text(encoding="utf-8")), {"customTitle": "Synced Title"})
        self.assertEqual((pid_file.read_bytes(), pid_file.stat().st_mtime_ns), pid_before)
        s = self.hub.get("claude", sid(6), True)
        self.assertEqual(s["title"], "Synced Title")
        self.assertEqual(s["sidecarCustomTitle"], "Synced Title")
        self.assertEqual(s["claudeAgentName"], "Synced Title")

        # 6b. AGY & Codex native `/rename` overrides previous Hub rename (`titleNativeSeen`), and Hub rename writes back to DB.
        self.make_agy()
        self.make_codex()
        self.assertEqual(self.hub.get("agy", A, True)["title"], "Agy title")
        self.hub.action("agy", A, "rename", {"title": "Cyberduck"})
        self.assertEqual(self.hub.get("agy", A, True)["title"], "Cyberduck")
        conn_a = sqlite3.connect(self.home / ".gemini/antigravity-cli/conversation_summaries.db")
        self.assertEqual(
            conn_a.execute("SELECT title FROM conversation_summaries WHERE conversation_id=?", (A,)).fetchone()[0],
            "Cyberduck",
        )
        conn_a.execute("UPDATE conversation_summaries SET title='duck' WHERE conversation_id=?", (A,))
        conn_a.commit()
        conn_a.close()
        self.hub._agy_row_memo.clear()
        self.hub._db_sig_cache.clear()
        self.assertEqual(self.hub.get("agy", A, True)["title"], "duck")
        self.hub.action("codex", X, "rename", {"title": "Codex Hub Title"})
        self.assertEqual(self.hub.get("codex", X, True)["title"], "Codex Hub Title")
        conn_x = sqlite3.connect(self.home / ".codex/state_5.sqlite")
        self.assertEqual(
            conn_x.execute("SELECT title FROM threads WHERE id=?", (X,)).fetchone()[0],
            "Codex Hub Title",
        )
        conn_x.execute("UPDATE threads SET title='Codex Native Renamed' WHERE id=?", (X,))
        conn_x.commit()
        conn_x.close()
        self.hub._codex_row_memo.clear()
        self.hub._db_sig_cache.clear()
        self.assertEqual(self.hub.get("codex", X, True)["title"], "Codex Native Renamed")

        # 7. `<pasted_content>`: closed with outer text, closed wrapping the whole prompt, unclosed/truncated, stray close.
        cp = server.clean_prompt
        self.assertEqual(" ".join(cp('请看这个 <pasted_content id="1">huge paste\nmore</pasted_content> 怎么改').split()), "请看这个 怎么改")
        self.assertEqual(cp('<pasted_content id="2">整段都是粘贴</pasted_content>'), "整段都是粘贴")
        self.assertEqual(cp('<pasted_content id="35a7"> 日本常驻个人 AI 工作站需求说明'), "日本常驻个人 AI 工作站需求说明")
        self.assertEqual(cp('先看下 <pasted_content id="9">被截断的粘贴'), "先看下")
        self.assertEqual(cp("hello</pasted_content>"), "hello")

        # 8. sessions-index.json enriches transcript-backed sessions only; no phantom session without a .jsonl.
        phantom = sid(7)
        indexed = project / (sid(8) + ".jsonl")
        self.write_rows(indexed, [{"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": "answer"}]}}])
        self.write_json(project / "sessions-index.json", {"version": 1, "entries": [
            {"sessionId": sid(8), "summary": "Index Summary", "firstPrompt": "index first prompt"},
            {"sessionId": phantom, "customTitle": "Phantom Title", "firstPrompt": "phantom prompt"}]})
        s = self.hub.get("claude", sid(8), True)
        self.assertEqual(s["title"], "Index Summary")
        self.assertEqual(s["firstPrompt"], "index first prompt")
        self.assertNotIn("claude:" + phantom, self.hub.discover(True))
        with self.assertRaises(KeyError):
            self.hub.get("claude", phantom)
        hidden = ("customTitleFromTail", "sidecarCustomTitle", "sidecarMtime", "claudeAgentName", "indexSummary",
                  "userNameMtime", "_titleScanIno", "_titleScanEnd")
        for row in self.listed(show_temp=1):
            self.assertFalse(set(hidden) & set(row))

    def test_claude_real_prompts_and_tool_pairing(self):
        self.write_rows(self.claude_path, [claude_user("<ide_opened_file>noise</ide_opened_file><system-reminder>noise</system-reminder>real"), {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "thinking", "thinking": "plan"}, {"type": "tool_use", "name": "Read", "id": "t1", "input": {"path": "x"}}]}}, claude_user([{"type": "tool_result", "tool_use_id": "t1", "content": "not a prompt"}])])
        s = self.hub.get("claude", C)
        self.assertEqual(s["firstPrompt"], "real")
        self.assertEqual(s["lastPrompt"], "real")
        records = list(self.hub.transcript(s))
        self.assertEqual([r["role"] for r in records], ["user", "thinking", "tool", "tool"])
        self.assertEqual(records[-1]["name"], "Read")

    def test_wrapper_and_instruction_cleaning(self):
        self.assertEqual(server.clean_prompt("<INSTRUCTIONS>rules</INSTRUCTIONS>", "codex"), "")
        self.assertEqual(server.clean_prompt("<environment_context>noise</environment_context>hello", "codex"), "hello")
        self.assertEqual(server.clean_prompt("<command-name>/resume</command-name><command-args>id</command-args>"), "")
        self.assertEqual(server.clean_prompt("please use <div>hello</div>"), "please use <div>hello</div>")

    def test_agy_transcript(self):
        self.make_agy()
        s = self.hub.get("agy", A)
        self.assertEqual(s["lastPrompt"], "real agy input")
        self.assertEqual(s["cwd"], "/workspace")
        entries = list(self.hub.transcript(s))
        self.assertEqual([e["kind"] for e in entries], ["message", "thinking", "message", "tool_call", "tool_result"])
        self.assertEqual(entries[-1]["name"], "read_file")

    def test_agy_subagent_fallback_only_without_user_input(self):
        path = self.make_agy(parent=CHILD)
        fallback = {"step_index": 0, "type": "SYSTEM_MESSAGE", "content": "[Message] role=user content=delegated task"}
        self.write_rows(path, [fallback, {"type": "PLANNER_RESPONSE", "content": "done"}])
        self.assertEqual(list(self.hub.transcript(self.hub.get("agy", A)))[0]["text"], "delegated task")
        self.write_rows(path, [fallback, {"type": "USER_INPUT", "content": "actual"}])
        self.assertEqual([e["text"] for e in self.hub.transcript(self.hub.get("agy", A, True))], ["actual"])

    def test_codex_optional_columns_index_and_injected_blocks(self):
        self.make_codex()
        self.write_rows(self.home / ".codex/session_index.jsonl", [{"id": X, "thread_name": "index title"}])
        s = self.hub.get("codex", X)
        self.assertEqual(s["title"], "index title")
        self.assertEqual(s["firstPrompt"], "actual codex question")
        records = list(self.hub.transcript(s))
        self.assertEqual(records[0]["text"], "actual codex question")
        self.assertEqual(records[-1]["name"], "shell")

    def test_discover_orphan_rollouts_and_snapshots(self):
        self.write_rows(self.home / ".codex/sessions/2026/10/23/rollout-no-uuid.jsonl", [{"type": "session_meta", "payload": {"id": X, "cwd": "/orphan"}}])
        self.write_json(self.home / ".claude/session-snapshot.json", {"sessions": [{"uuid": CHILD, "summary": "snapshot", "cwd": "/old", "mtime": 10}]})
        self.assertEqual(self.hub.get("codex", X)["cwd"], "/orphan")
        self.assertEqual(self.hub.get("claude", CHILD)["title"], "snapshot")

    def test_active_entries_without_a_process_expire_to_recent_after_a_day_idle(self):
        self.hub._save_working_set({"claude:" + C})
        old = time.time() - server.WORKING_SET_IDLE_EXPIRE_SEC - 600
        os.utime(self.claude_path, (old, old))
        self.hub.scanned = 0
        s = next(s for s in self.listed() if s["id"] == C)
        self.assertFalse(s["inWorkingSet"])
        self.assertFalse(s["isLive"])
        self.assertNotIn("claude:" + C, self.hub.working_set())
        # Recent activity keeps it in Active
        self.hub._save_working_set({"claude:" + C})
        os.utime(self.claude_path, None)
        self.hub.scanned = 0
        self.assertTrue(next(s for s in self.listed() if s["id"] == C)["inWorkingSet"])

    def test_exit_after_an_earlier_hub_hibernate_still_moves_to_recent(self):
        key = "claude:" + C
        hib_at = time.time() - 3 * 3600
        self.hub.save(key, {"cwd": str(self.home), "hibernatedAt": hib_at, "hibernatedByHub": True})
        self.hub._save_working_set({key})
        exit_dir = Path(self.hub.root) / "data" / "last_exit"
        exit_dir.mkdir(parents=True, exist_ok=True)
        (exit_dir / f"{C}.json").write_text(json.dumps({"ts": time.time(), "rc": 0, "tool": "claude"}))
        self.hub.scanned = 0
        s = next(s for s in self.listed() if s["id"] == C)
        self.assertFalse(s["inWorkingSet"])
        # A hub hibernate after the exit keeps it in Active
        self.hub.save(key, {"cwd": str(self.home), "hibernatedAt": time.time() + 60, "hibernatedByHub": True})
        self.hub._save_working_set({key})
        self.hub.scanned = 0
        self.assertTrue(next(s for s in self.listed() if s["id"] == C)["inWorkingSet"])

    def test_move_to_recent_action_drops_an_active_session(self):
        self.hub._save_working_set({"claude:" + C})
        self.hub.scanned = 0
        self.assertTrue(next(s for s in self.listed() if s["id"] == C)["inWorkingSet"])
        self.hub.action("claude", C, "move_to_recent", {})
        self.hub.scanned = 0
        s = next(s for s in self.listed() if s["id"] == C)
        self.assertFalse(s["inWorkingSet"])
        self.assertEqual(s["agentState"], "dormant")

    def test_temporary_sessions_live_and_pinned_exceptions(self):
        self.make_agy(parent=CHILD)
        self.assertNotIn(A, [s["id"] for s in self.listed()])
        self.assertIn(A, [s["id"] for s in self.listed(show_temp=1)])
        self.hub.action("agy", A, "pin", {"pinned": True})
        self.assertIn(A, [s["id"] for s in self.listed()])
        self.hub.action("agy", A, "pin", {"pinned": False})
        self.hub.processes = lambda: [self.process("agy", 123)]
        self.hub.open_files = lambda _: {123: {"paths": ["/brain/" + A + "/x"], "cwd": "/tmp"}}
        self.hub.scanned = 0
        # Running subagents/child tasks must NOT appear or be counted in default listing (only when show_temp=1 or pinned)
        self.assertNotIn(A, [s["id"] for s in self.listed()])
        self.assertEqual(self.hub.listing({})["counts"]["agy"], 0)
        self.assertTrue(next(s for s in self.listed(show_temp=1) if s["id"] == A)["isLive"])

    def test_consultant_and_tmp_filter(self):
        # A prompt template the user marks as automated (Preferences `ignore_prompt_patterns`) is hidden by default
        prefs = self.home / "prefs.json"
        prefs.write_text(json.dumps({"ignore_prompt_patterns": ["has:不是执行者"]}), encoding="utf-8")
        with patch.dict(os.environ, {"MIXHUB_PREFERENCES_PATH": str(prefs)}):
            self.write_rows(self.claude_path, [claude_user("你是顾问，不是执行者。给建议")])
            self.assertEqual(len(self.listed()), 0)
            self.assertEqual(len(self.listed(show_temp=1)), 1)

    def test_codex_automated_task_and_referenced_filters(self):
        self.assertTrue(server.is_codex_automated("vscode", "Codex Companion Task: <task> audit", "--no-write <task>\nReview"))
        self.assertTrue(server.is_codex_automated("vscode", "## Referenced ChatGPT conversation:", "## Referenced ChatGPT conversation:\nUntrusted"))
        self.assertTrue(server.is_codex_automated("vscode", "Read-only review", "Read-only strategic review of this trading bot"))
        self.assertFalse(server.is_codex_automated("cli", "帮我看下这个文件夹是干嘛的", "帮我看下这个文件夹是干嘛的"))

    def test_tui_cjk_display_width(self):
        import tui
        # CJK characters must count as 2 display columns and never overflow max_cols
        s = "数据库迁移需求说明与回滚方案（测试长标题截断不溢出）"
        truncated = tui.disp_trunc(s, 18)
        self.assertLessEqual(tui.disp_width(truncated), 18)
        padded = tui.disp_pad(s, 18)
        self.assertEqual(tui.disp_width(padded), 18)
        wrapped = tui.disp_wrap(s, 20)
        self.assertTrue(all(tui.disp_width(line) <= 20 for line in wrapped))

    def test_sample_read_budget_cache_and_invalidation(self):
        self.write_rows(self.claude_path, [claude_user("prompt " + str(i) + "x" * 500) for i in range(400)])
        real_open = open
        reads = []
        class Tracked:
            def __init__(self, f): self.f = f
            def __enter__(self): return self
            def __exit__(self, *a): self.f.close()
            def read(self, n=-1):
                self_value = self.f.read(n); reads.append(len(self_value)); return self_value
            def seek(self, *a): return self.f.seek(*a)
        def tracked(path, *args, **kwargs):
            f = real_open(path, *args, **kwargs)
            return Tracked(f) if str(path) == str(self.claude_path) else f
        with patch("builtins.open", tracked):
            a = self.hub.summary(str(self.claude_path), "claude")
            b = self.hub.summary(str(self.claude_path), "claude")
        # Title-less transcript: head + tail plus one bounded backward title scan, then served from cache.
        self.assertLessEqual(sum(reads), server.HEAD_BYTES + server.TAIL_BYTES + self.claude_path.stat().st_size)
        self.assertEqual(a, b)
        self.assertFalse(a["messageCountExact"])
        self.assertTrue(a["lastPrompt"].startswith("prompt 399"))
        with self.claude_path.open("a") as f: f.write(json.dumps(claude_user("new last")) + "\n")
        reads.clear()
        with patch("builtins.open", tracked):
            c = self.hub.summary(str(self.claude_path), "claude")
        # Appends take the incremental path: no second backward scan.
        self.assertLessEqual(sum(reads), server.HEAD_BYTES + server.TAIL_BYTES + 8 * 1024)
        self.assertEqual(c["lastPrompt"], "new last")

    def test_full_reader_reaches_both_ends_and_skips_corrupt_live_line(self):
        self.write_rows(self.claude_path, [claude_user("message " + str(i) + "x" * 100) for i in range(1500)])
        with self.claude_path.open("a") as f: f.write('{"type":"user"')
        records = list(self.hub.transcript(self.hub.get("claude", C)))
        self.assertEqual(len(records), 1500)
        self.assertTrue(records[0]["text"].startswith("message 0x"))
        self.assertTrue(records[-1]["text"].startswith("message 1499x"))

    @staticmethod
    def process(tool, pid=123, **extra):
        return {"pid": pid, "ppid": 1, "tty": "ttys013", "rss_mb": 100.5, "etime": "01:00", "tool": tool, "args": [], "wrapper": False, **extra}

    # A process in a MixHub window, with the identity the kill guard checks.
    RESIDENT = {"resident": True, "boot_id": "bt:1", "start_time": "Sat Oct 31 00:00:00 2026"}

    @staticmethod
    def guard_diag(pid, ident):
        """What the kill guard sees for a stand-in process, for the failure message."""
        from mixhub.proc.classify import is_recognized_agent_command
        from mixhub.proc.guard import inspect_live_process
        from mixhub.proc.identity import get_boot_id
        live = inspect_live_process(pid)
        return {"ident": ident, "live_boot": get_boot_id(use_cache=False), "live": live,
                "agent": live and is_recognized_agent_command(live[1])}

    def live_claude(self, pid=123, status="idle", **extra):
        self.write_json(self.home / (".claude/sessions/" + str(pid) + ".json"), {"pid": pid, "sessionId": C, "cwd": "/workspace", "status": status})
        self.hub.processes = lambda: [self.process("claude", pid, **extra)]
        self.hub.scanned = 0

    def test_process_parser_filters_helpers_background_and_exec(self):
        data = """PID PPID TTY RSS ELAPSED COMMAND
1 0 ttys001 102400 01:00 claude --resume abc
2 0 ?? 102400 01:00 claude
3 0 ttys002 4096 01:00 node /opt/bin/codex resume
4 3 ttys002 204800 01:00 /vendor/bin/codex resume
5 0 ttys003 1000 01:00 npm exec chrome-devtools-mcp
6 0 ttys003 1000 01:00 grep claude
7 0 ttys004 1000 01:00 codex exec -s workspace-write
8 0 ttys005 1000 01:00 claude -p hello
9 0 ttys006 1000 01:00 agy --mode auto
10 0 ttys007 1000 01:00 zsh -c 'codex resume'
"""
        result = server.parse_processes(data)
        self.assertEqual([p["pid"] for p in result], [1, 4, 9])
        self.assertEqual(result[1]["rss_mb"], 200.0)

    def test_stale_pid_state_does_not_make_session_live(self):
        self.write_json(self.home / ".claude/sessions/999.json", {"pid": 999, "sessionId": C, "status": "idle"})
        self.assertFalse(self.hub.get("claude", C)["isLive"])

    def test_live_agy_prefers_parent_and_picker_synthetic(self):
        self.make_agy()
        path = self.home / ".gemini/antigravity-cli/conversation_summaries.db"
        db = sqlite3.connect(path)
        db.execute("INSERT INTO conversation_summaries VALUES(?,?,?,?,?,?)", (CHILD, "child", "", "[]", A, 0))
        db.commit(); db.close()
        self.hub.processes = lambda: [self.process("agy", 1), self.process("codex", 2), self.process("agy", 3)]
        self.hub.open_files = lambda _: {1: {"paths": ["/brain/" + CHILD, "/brain/" + A], "cwd": "/workspace"}}
        sessions = self.hub.discover()
        self.assertTrue(sessions.get("agy:" + A)["isLive"])
        self.assertFalse(sessions.get("agy:" + CHILD)["isLive"])
        self.assertTrue(sessions.get("codex:live-codex-2")["synthetic"])
        self.assertTrue(sessions.get("agy:live-agy-3")["synthetic"])

    def test_live_codex_rollout_and_resume_argument(self):
        self.make_codex()
        self.hub.processes = lambda: [{**self.process("codex"), "args": ["resume", X]}]
        self.assertTrue(self.hub.get("codex", X)["isLive"])
        self.hub.processes = lambda: [self.process("codex")]
        self.hub.open_files = lambda _: {123: {"paths": ["/sessions/rollout-date-" + X + ".jsonl"]}}
        self.assertTrue(self.hub.get("codex", X, True)["isLive"])

    def test_multiple_processes_same_session_counted_once(self):
        self.live_claude()
        self.write_json(self.home / ".claude/sessions/124.json", {"pid": 124, "sessionId": C})
        self.hub.processes = lambda: [self.process("claude", 123), self.process("claude", 124)]
        data = self.hub.listing({})
        self.assertEqual(data["counts"]["live"], 1)
        self.assertEqual(data["counts"]["liveProcesses"], 2)
        self.assertEqual(data["counts"]["liveMemoryMB"], 201.0)

    def test_resume_command_quoting_and_dry_run(self):
        for tool, suffix in [("claude", "--resume"), ("agy", "--conversation"), ("codex", "resume")]:
            s = {"tool": tool, "id": X, "cwd": "/tmp/User's folder; $(touch nope)\n你好"}
            command = server.resume_command(s)
            self.assertEqual(shlex.split(command), ["cd", s["cwd"], "&&", tool, suffix, X])
        with patch("mixhub.hub.actions.run") as run:
            result = self.hub.action("claude", C, "resume", {"dry_run": True})
            run.assert_not_called()
        self.assertEqual(result["command"], "cd /workspace && claude --resume " + C)
        self.assertIn('tell application "Terminal"', result["script"])

    def test_focus_tmux_dry_run_does_not_select_or_activate(self):
        def fake_run(args, **kwargs):
            if args[1] == "list-panes": return "work:2.1|/dev/ttys013\n"
            if args[1] == "list-clients": return "work|/dev/ttys008|123\n"
            self.fail("dry run mutated terminal")
        with patch("mixhub.hub.actions.run", fake_run):
            result = self.hub.focus(self.process("claude"), True)
        self.assertEqual(result["tty"], "ttys008")
        self.assertEqual(result["tmuxCommands"][0], ["tmux", "select-window", "-t", "work:2"])
        self.assertIn('tty of t is "/dev/ttys008"', result["script"])

    def test_focus_non_tmux_and_stale_rejected(self):
        with patch("mixhub.hub.actions.run", side_effect=FileNotFoundError):
            result = self.hub.focus(self.process("claude"), True)
        self.assertEqual(result["tty"], "ttys013")
        with self.assertRaises(ValueError): self.hub.action("claude", C, "focus", {"dry_run": True})

    def test_hibernate_dry_run_has_no_side_effects(self):
        self.live_claude(**self.RESIDENT)
        with patch("server.os.kill") as kill:
            result = self.hub.action("claude", C, "hibernate", {"dry_run": True})
            kill.assert_not_called()
        self.assertFalse(Path(self.hub.state_path).exists())
        self.assertEqual(result["state"]["lastPid"], 123)

    def test_hibernate_signals_only_disposable_process_and_persists(self):
        # The guard only signals what looks like an agent CLI (covered in test_proc_guard); a Python stand-in is not.
        agent = patch("mixhub.proc.guard.is_recognized_agent_command", return_value=True)
        agent.start()
        self.addCleanup(agent.stop)
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            ident = capture_process_identity(process.pid)
            extra = {"resident": True, "boot_id": ident.boot_id, "start_time": ident.start_time}
            self.live_claude(process.pid, **extra)
            self.hub.processes = lambda: [self.process("claude", process.pid, **extra)] if process.poll() is None else []
            result = self.hub.action("claude", C, "hibernate", {})
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.fail(f"not signalled: {self.guard_diag(process.pid, ident)}")
            self.assertEqual(process.returncode, -signal.SIGTERM)
            self.assertTrue(result["saved"])
            saved = json.loads(Path(self.hub.state_path).read_text())["sessions"]["claude:" + C]
            self.assertEqual(saved["lastPid"], process.pid)
            self.assertIn("hibernatedAt", saved)
        finally:
            if process.poll() is None: process.terminate(); process.wait()

    def test_hibernate_fallback_sigint(self):
        self.live_claude(**self.RESIDENT)
        with patch("server.os.kill") as kill, patch("server.time.sleep"), patch("server.time.monotonic", side_effect=range(10000)), \
                patch("mixhub.hub.lifecycle.safe_kill_process", lambda ident, sig, kill_fn: kill_fn(ident.pid, sig) or True):
            result = self.hub.action("claude", C, "hibernate", {})
        self.assertEqual(kill.call_args_list[0].args, (123, signal.SIGTERM))
        self.assertEqual(kill.call_args_list[-1].args, (123, signal.SIGINT))
        self.assertEqual(result["pendingPids"], [123])

    def test_idle_batch_uses_only_explicit_idle(self):
        self.live_claude(status="working", **self.RESIDENT)
        self.assertEqual(self.hub.hibernate_idle({"dry_run": True})["results"], [])
        self.live_claude(status="idle", **self.RESIDENT)
        self.assertEqual(len(self.hub.hibernate_idle({"dry_run": True})["results"]), 1)

    def test_sleep_leaves_agents_mixhub_did_not_start(self):
        # A Claude idling at its prompt in another terminal (iTerm, VS Code): every sleep path leaves it running.
        self.live_claude(status="idle", boot_id="bt:1", start_time="Sat Oct 31 00:00:00 2026")
        with patch("server.os.kill") as kill, patch("mixhub.hub.lifecycle.safe_kill_process") as guarded:
            self.assertEqual(self.hub.hibernate_idle({})["results"], [])
            with self.assertRaises(ValueError):
                self.hub.action("claude", C, "hibernate", {})
            self.hub.close_session("claude", C)
        kill.assert_not_called()
        guarded.assert_not_called()

    def test_hibernate_never_signals_a_process_it_cannot_identify(self):
        self.live_claude(resident=True)
        with patch("server.os.kill") as kill, patch("server.time.sleep"), patch("server.time.monotonic", side_effect=range(10000)):
            result = self.hub.action("claude", C, "hibernate", {})
        kill.assert_not_called()
        self.assertEqual(result["pendingPids"], [123])

    def test_metadata_persists_atomic_updates_and_rejects_invalid(self):
        threads = [threading.Thread(target=self.hub.save, args=("claude:" + C, update)) for update in ({"title": "renamed"}, {"pinned": True})]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        new_hub = server.Hub(str(self.home), str(self.root))
        self.assertEqual(new_hub.state()["sessions"]["claude:" + C], {"title": "renamed", "pinned": True})
        with self.assertRaises(ValueError): self.hub.action("claude", C, "pin", {"pinned": "false"})
        Path(self.hub.state_path).write_text("broken")
        with self.assertRaises(ValueError): self.hub.save("claude:" + C, {"title": "bad"})
        self.assertEqual(Path(self.hub.state_path).read_text(), "broken")

    def test_api_filters_and_counts(self):
        self.make_agy(); self.make_codex()
        self.assertEqual([s["tool"] for s in self.listed(tool="agy")], ["agy"])
        self.assertEqual([s["id"] for s in self.listed(q="actual codex")], [X])
        self.assertEqual(self.hub.listing({})["counts"]["total"], 3)

    def test_http_streaming_security_and_error_contract(self):
        httpd = server.http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        httpd.hub = self.hub
        thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
        base = "http://127.0.0.1:" + str(httpd.server_port)
        try:
            with urllib.request.urlopen(base + "/api/sessions") as response:
                self.assertEqual(len(json.load(response)["sessions"]), 1)
                self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
            data = json.load(urllib.request.urlopen(base + "/api/sessions/claude/" + C))
            self.assertEqual(data["messageCount"], 3)
            for headers, body, code in [({"Origin": "https://attacker.example", "Content-Type": "application/json"}, b"{}", 403), ({"Content-Type": "text/plain"}, b"{}", 403), ({"Content-Type": "application/json"}, b"invalid", 400)]:
                request = urllib.request.Request(base + "/api/sessions/claude/" + C + "/resume", data=body, headers=headers)
                with self.assertRaises(urllib.error.HTTPError) as error: urllib.request.urlopen(request)
                self.assertEqual(error.exception.code, code)
            request = urllib.request.Request(base + "/api/health", headers={"Host": "attacker.example"})
            with self.assertRaises(urllib.error.HTTPError) as error: urllib.request.urlopen(request)
            self.assertEqual(error.exception.code, 403)
        finally:
            httpd.shutdown(); httpd.server_close(); thread.join()

    def test_tail_window_reader_and_auto_expand(self):
        # Write 300 messages (>150KB) with CJK multi-byte text to verify UTF-8 safe tail slicing
        rows = [claude_user(f"第 {i} 条中文消息 " + "测试内容" * 120) for i in range(300)]
        self.write_rows(self.claude_path, rows)
        s = self.hub.get("claude", C, True)
        tail_entries, is_trunc = self.hub.transcript_window(s, tail_bytes=32768)
        self.assertTrue(is_trunc)
        self.assertGreaterEqual(len(tail_entries), 20)
        self.assertTrue(tail_entries[-1]["text"].startswith("第 299 条中文消息"))
        full_entries, full_trunc = self.hub.transcript_window(s, tail_bytes=None)
        self.assertFalse(full_trunc)
        self.assertEqual(len(full_entries), 300)

    def test_tail_window_reaches_back_to_the_last_prompt_past_huge_tool_output(self):
        # A long agent run after the last prompt: the preview (and its prompt marks) still needs that prompt
        def tool_turn(i):
            return [{"type": "assistant", "cwd": "/workspace", "message": {"role": "assistant", "content": [
                        {"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {"command": "ls"}}]}},
                    {"type": "user", "cwd": "/workspace", "message": {"role": "user", "content": [
                        {"type": "tool_result", "tool_use_id": f"t{i}", "content": "x" * 100000}]}}]
        rows = [claude_user("first prompt")]
        for i in range(10):
            rows += tool_turn(i)
        rows.append(claude_user("the last real prompt"))
        for i in range(10, 40):  # ~3MB of tool output after it
            rows += tool_turn(i)
        self.write_rows(self.claude_path, rows)
        s = self.hub.get("claude", C, True)
        entries, _ = self.hub.transcript_window(s)
        prompts = [e["text"] for e in entries if e.get("role") == "user" and e.get("kind") == "message"]
        self.assertIn("the last real prompt", prompts)

    def test_compaction_does_not_age_the_session_to_its_first_prompt(self):
        # After /compact the file ends with a summary row plus a pile of context attachments; the row age
        # must still come from the turn just before it, not from the first prompt in the head
        def at(row, ts):
            return dict(row, timestamp=time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(ts)))
        now = time.time()
        rows = [at(claude_user("first prompt"), now - 86400 * 2)]
        rows += [at(claude_user(f"filler {i} " + "x" * 2000), now - 86400 * 2) for i in range(20)]
        rows.append(at({"type": "assistant", "cwd": "/workspace",
                        "message": {"role": "assistant", "content": [{"type": "text", "text": "last reply"}]}}, now - 120))
        rows.append(at({"type": "attachment", "attachment": {"type": "x", "content": "y" * 30000}}, now - 120))
        rows.append(at({"type": "system", "subtype": "compact_boundary", "content": "Conversation compacted"}, now - 60))
        rows.append(at(dict(claude_user("This session is being continued " + "s" * 10000), isCompactSummary=True), now - 60))
        rows += [at({"type": "attachment", "attachment": {"type": "x", "content": "y" * 10000}}, now - 60)
                 for _ in range(4)]
        self.write_rows(self.claude_path, rows)
        self.assertGreater(self.hub.summary(str(self.claude_path), "claude")["updatedAt"], now - 600)

    def test_disk_index_cache_persistence(self):
        self.hub.discover(True)
        self.assertTrue(Path(self.hub.disk_cache_path).exists())
        hub2 = server.Hub(str(self.home), str(self.root))
        hub2.processes = lambda: []
        hub2.open_files = lambda _: {}
        self.assertIn(str(self.claude_path), hub2.cache)
        self.assertEqual(hub2.get("claude", C)["lastPrompt"], "last real prompt")

    def test_zero_emoji_and_subagent_ctx_annotation(self):
        import re as _re
        import agy_claude_filter
        emoji_re = _re.compile(r"[\U0001F300-\U0001FAFF\u2615\u2693\u26A0\u2B06\u2B07]")
        repo_root = Path(__file__).resolve().parents[1]
        # The root modules are shims now; the code they used to hold lives in the `mixhub` package
        package_files = tuple(str(p.relative_to(repo_root)) for p in sorted((repo_root / "mixhub").rglob("*.py")))
        for rel in ("bin/hub", "bin/mixhub", "install.sh", "mixhub/web/static/app.js", "mixhub/web/static/index.html", "mixhub/web/static/style.css", "tests/test_hub.py", "tests/browser_check.mjs", *package_files):
            content = (repo_root / rel).read_text(encoding="utf-8")
            self.assertIsNone(emoji_re.search(content), f"Found forbidden emoji in {rel}")

        # Verify Agent(self: ...) -> Agent(Argon: ...) + dynamic role override + Ext(...) badge
        agy_claude_filter.SUB_LIVE_CACHE["ts"] = 0.0
        sample = b"\x1b[38;5;111m\xe2\x97\x8f Agent(self: \xe6\xbb\x9a\xe5\x8a\xa8\xe7\x98\xa6\xe8\xba\xab) Running command \xc2\xb7 15s\x1b[m"
        out, _ = agy_claude_filter.transform_chunk(sample)
        self.assertIn(b"Agent(Argon: \xe6\xbb\x9a\xe5\x8a\xa8\xe7\x98\xa6\xe8\xba\xab)", out)

        live_sub_file = self.root / "sub_live.json"
        live_ext_file = self.root / "ext_live.json"
        live_sub_file.write_text(
            json.dumps({
                "ts": time.time(),
                "model": "Flash",
                "role": "Flash·清包缓存保SDXL",
                "ctx": "54k",
            }),
            encoding="utf-8",
        )
        live_ext_file.write_text(
            json.dumps({
                "pid": os.getpid(),
                "tool": "ext-runner",
                "model": "ext-pro",
                "title": "架构审查",
                "start_ts": time.time() - 8,
            }),
            encoding="utf-8",
        )
        from mixhub.filters.agy_claude import stream as agy_stream  # owner of the badge paths

        orig_sub_path = agy_stream.SUB_LIVE_PATH
        orig_ext_path = agy_stream.ACTIVE_EXT_PATH
        try:
            agy_stream.SUB_LIVE_PATH = str(live_sub_file)
            agy_stream.ACTIVE_EXT_PATH = str(live_ext_file)
            agy_claude_filter.SUB_LIVE_CACHE["ts"] = 0.0
            out2, _ = agy_claude_filter.transform_chunk(sample)
            self.assertIn("Agent(Flash: 清包缓存保SDXL)".encode("utf-8"), out2)
            self.assertIn(b"ctx 54k", out2)
            self.assertIn("Ext(ext-pro: 架构审查)".encode("utf-8"), out2)
        finally:
            agy_stream.SUB_LIVE_PATH = orig_sub_path
            agy_stream.ACTIVE_EXT_PATH = orig_ext_path
            agy_claude_filter.SUB_LIVE_CACHE["ts"] = 0.0
            agy_claude_filter.SUB_LIVE_CACHE.update({"model": "Argon", "role": "", "ctx": "", "ext_tag": ""})

    def test_four_state_classifier_orthogonal_icons_and_prompt_click_cursor(self):
        import tui
        import agy_claude_filter

        # 1. 4-state tail classifier: wait_input vs done_idle vs unclosed_tool vs ball_in_model
        self.assertEqual(
            server.classify_tail_entries([{"role": "tool", "kind": "tool_call", "name": "AskUserQuestion", "text": "q"}]),
            "wait_input",
        )
        self.assertEqual(
            server.classify_tail_entries([{"role": "tool", "kind": "tool_call", "name": "Bash", "text": "pytest"}]),
            "unclosed_tool",
        )
        self.assertEqual(
            server.classify_tail_entries([{"role": "assistant", "kind": "message", "text": "已完成修改，要不要继续部署？"}]),
            "wait_input",
        )
        self.assertEqual(
            server.classify_tail_entries([{"role": "assistant", "kind": "message", "text": "以上全部测试通过并已上线生效。"}]),
            "done_idle",
        )
        self.assertEqual(
            server.classify_tail_entries([{"role": "user", "kind": "message", "text": "请检查这个函数"}]),
            "ball_in_model",
        )
        # Mid-turn Claude assistant message with stop_reason=="tool_use" or trailing tool_result stays ball_in_model
        mid_turn_rows = [
            {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "stop_reason": "tool_use",
                    "content": [{"type": "text", "text": "我先检查一下远程连接配置。"}],
                },
            }
        ]
        mid_entries = list(server.entries(mid_turn_rows, "claude"))
        self.assertEqual(mid_entries[-1].get("stopReason"), "tool_use")
        self.assertEqual(server.classify_tail_entries(mid_entries, "claude"), "ball_in_model")
        self.assertEqual(
            server.classify_tail_entries(
                [
                    {"role": "assistant", "kind": "message", "text": "正在执行检查。", "stopReason": "tool_use"},
                    {"role": "tool", "kind": "tool_result", "name": "Bash", "text": "ok (no interrupted by user)"},
                ],
                "claude",
            ),
            "ball_in_model",
        )

        # 2. Orthogonal Shape (Pinned ★/☆ vs Unpinned ◆/◇) x Color (working/wait_input/done_idle/dormant)
        self.assertEqual(tui.status_icon_and_pair({"pinned": True, "isLive": True, "agentState": "working"}), ("★", tui.P_LIVE, True))
        self.assertEqual(tui.status_icon_and_pair({"pinned": True, "isLive": True, "agentState": "wait_input"}), ("★", tui.P_WAIT, True))
        self.assertEqual(tui.status_icon_and_pair({"pinned": True, "isLive": True, "agentState": "done_idle"}), ("★", tui.P_DONE, False))
        self.assertEqual(tui.status_icon_and_pair({"pinned": True, "isLive": False, "agentState": "dormant"}), ("☆", tui.P_DIM, False))
        self.assertEqual(tui.status_icon_and_pair({"pinned": False, "isLive": True, "agentState": "working"}), ("◆", tui.P_LIVE, True))
        self.assertEqual(tui.status_icon_and_pair({"pinned": False, "isLive": True, "agentState": "wait_input"}), ("◆", tui.P_WAIT, True))
        self.assertEqual(tui.status_icon_and_pair({"pinned": False, "isLive": True, "agentState": "done_idle"}), ("◆", tui.P_DONE, False))
        self.assertEqual(tui.status_icon_and_pair({"pinned": False, "isLive": False, "agentState": "dormant"}), ("◇", tui.P_DIM, False))

        # 3. 32-col Sidebar Row Layout + Single-column Brand Badge + Right Thinking Spinner
        self.assertEqual(tui.agent_badge_and_pair({"tool": "claude", "isLive": True, "agentState": "working"}), ("◇", tui.P_AGENT_CLAUDE, tui.P_AGENT_CLAUDE_SEL))
        self.assertEqual(tui.agent_badge_and_pair({"tool": "agy", "isLive": False, "agentState": "dormant"}), ("·", tui.P_AGENT_AGY, tui.P_AGENT_AGY_SEL))
        self.assertEqual(tui.agent_badge_and_pair({"tool": "codex", "pinned": True, "isLive": False}), ("·", tui.P_AGENT_CODEX, tui.P_AGENT_CODEX_SEL))
        for title in ("", "ASCII short", "写周报：Q3 复盘草稿测试超长中文标题不折行", "Mix 中英文 1234567890"):
            for unread in (False, True):
                cells = tui.render_sidebar_row_cells(
                    {"tool": "claude", "title": title, "pinned": unread, "isLive": True, "agentState": "done_idle", "updatedAt": time.time() - 180},
                    left_w=32,
                    is_unread=unread,
                )
                self.assertEqual(cells["disp_w"], 32)
                self.assertEqual(cells["dot_ch"], "◇")
                self.assertEqual(cells["dot_x"], 2)
                self.assertEqual(cells["dot2_ch"], " ")
                self.assertEqual(cells["title_x"], 5)
                self.assertEqual(cells["age_x"], 27)
                self.assertEqual(cells["unread_x"], 30)
                self.assertEqual(cells["unread_ch"], " ")
                self.assertEqual(cells["show_dot"], unread)
                self.assertFalse(cells["is_working"])
                self.assertTrue(cells["full_str"].endswith(" "))
        # Cross-agent relay session (`handoffFrom="claude"`, `tool="codex"`) keeps its own tool color and hollow diamond `◇`
        relay_cells = tui.render_sidebar_row_cells(
            {"tool": "codex", "handoffFrom": "claude", "title": "Relay task", "pinned": False, "isLive": True, "agentState": "working", "updatedAt": time.time() - 15},
            left_w=32,
            is_unread=False,
        )
        self.assertEqual(relay_cells["disp_w"], 32)
        self.assertEqual(relay_cells["dot_ch"], "◇")
        self.assertEqual(relay_cells["dot2_ch"], " ")
        self.assertEqual(relay_cells["dot_pair"], tui.P_AGENT_CODEX)
        self.assertEqual(relay_cells["origin_pair"], tui.P_AGENT_CODEX)
        self.assertEqual(relay_cells["unread_ch"], " ")
        self.assertTrue(relay_cells["is_working"])
        self.assertEqual(relay_cells["dot2_pair"], tui.P_AGENT_CODEX)
        # Unread waiting sessions highlight title and age without another glyph.
        wait_cells = tui.render_sidebar_row_cells(
            {"tool": "agy", "title": "Waiting reply", "pinned": False, "isLive": True, "agentState": "wait_input", "updatedAt": time.time() - 60},
            left_w=32,
            is_unread=True,
        )
        self.assertEqual(wait_cells["dot_ch"], "◇")
        self.assertEqual(wait_cells["dot_pair"], tui.P_AGENT_AGY)
        self.assertEqual(wait_cells["unread_ch"], " ")
        self.assertTrue(wait_cells["show_dot"])

        # 4. Prompt click-to-move-cursor in agy_claude_filter (single-line + multi-line wrapped CJK)
        tracker = agy_claude_filter.PromptCursorTracker()
        tracker.term_rows = 30
        tracker.term_cols = 100
        # Simulate agy rendering `> 你好世界测试光标` with cursor at end (column 19, char index 8)
        tracker.update_from_output(b"\x1b[K> \xe4\xbd\xa0\xe5\xa5\xbd\xe4\xb8\x96\xe7\x95\x8c\xe6\xb5\x8b\xe8\xaf\x95\xe5\x85\x89\xe6\xa0\x87\x1b[?7h\x1b[19G")
        self.assertEqual(tracker.cur_col, 19)
        # Simulate left-click at column 7 or 8 (both halves of 2-col CJK character "世", char index 2) on row 27: 6 Left arrows
        arrows = agy_claude_filter.filter_stdin_mouse(b"\x1b[<0;8;27M\x1b[<0;8;27m", tracker)
        self.assertEqual(arrows, b"\x1b[D" * 6)

        # Simulate multi-line wrapped CJK prompt (`row 16`: `> 你好检查行一`, `row 17`: `  第二行尾部`) with cursor at end of row 17 (col 13, total idx 11)
        tracker2 = agy_claude_filter.PromptCursorTracker()
        tracker2.term_rows = 20
        tracker2.term_cols = 60
        tracker2.update_from_output(
            b"\x1b[K> \xe4\xbd\xa0\xe5\xa5\xbd\xe6\xa3\x80\xe6\x9f\xa5\xe8\xa1\x8c\xe4\xb8\x80\r\n"
            b"\x1b[K  \xe7\xac\xac\xe4\xba\x8c\xe8\xa1\x8c\xe5\xb0\xbe\xe9\x83\xa8\x1b[?7h\x1b[17;13H"
        )
        self.assertEqual(tracker2.lines, ["你好检查行一", "第二行尾部"])
        self.assertEqual(tracker2.top_row, 16)
        self.assertEqual(tracker2.cur_idx, 11)
        # Click on "检" (char index 2) on wrapped top row 16 col 7 -> should emit 9 Left arrows (`11 - 2 = 9`)
        arrows2 = agy_claude_filter.filter_stdin_mouse(b"\x1b[<0;7;16M\x1b[<0;7;16m", tracker2)
        self.assertEqual(arrows2, b"\x1b[D" * 9)

        # 5. New session at row 14 (term_rows=40, top_row=None): pure ASCII incremental input via feed_stdin + Ink diff output
        tracker3 = agy_claude_filter.PromptCursorTracker()
        tracker3.term_rows = 40
        tracker3.term_cols = 80
        tracker3.update_from_output(b"\r\n>\r\n\xe2\x94\x80\xe2\x94\x80\r\x1b[4A\x1b[2C\x1b[?25h")
        tracker3.feed_stdin(b"hello world")
        tracker3.update_from_output(b"\x1b[?25lh\x1b[?25h\x1b[?25le\x1b[?25h\x1b[?25ll\x1b[?25h")
        self.assertEqual(tracker3.text, "hello world")
        self.assertEqual(tracker3.cur_idx, 11)
        # Click on row 14, col 5 ('l' at char index 2) -> 9 Left arrows (11 - 2 = 9)
        arrows3 = agy_claude_filter.filter_stdin_mouse(b"\x1b[<0;5;14M\x1b[<0;5;14m", tracker3)
        self.assertEqual(arrows3, b"\x1b[D" * 9)
        self.assertEqual(tracker3.cur_idx, 2)
        self.assertEqual(tracker3.last_prompt_row, 14)

        # 6. Mixed CJK + ASCII input, Left arrow \x1b[D + \x1b[?25l\x08\x1b[?25h, then mouse click
        tracker4 = agy_claude_filter.PromptCursorTracker()
        tracker4.term_rows = 40
        tracker4.term_cols = 80
        agy_claude_filter.filter_stdin_mouse(b"\x1b[14;3R", tracker4)
        self.assertEqual(tracker4.last_prompt_row, 14)
        agy_claude_filter.filter_stdin_mouse("hello 你好 world".encode("utf-8"), tracker4)
        tracker4.update_from_output(
            b"\x1b[?25l\x1b[?7l\r\x1b[K> hello \xe4\xbd\xa0\xe5\xa5\xbd\x1b[?7h\x1b[13G\x1b[?25h"
            b"\x1b[?25l \x1b[?25h\x1b[?25lw\x1b[?25h"
        )
        self.assertEqual(tracker4.text, "hello 你好 world")
        self.assertEqual(tracker4.cur_idx, 14)
        # Press Left arrow twice on stdin + echoed \x08 on stdout -> cur_idx moves from 14 to 12 ( without double-counting )
        agy_claude_filter.filter_stdin_mouse(b"\x1b[D\x1b[D", tracker4)
        tracker4.update_from_output(b"\x1b[?25l\x08\x1b[?25h\x1b[?25l\x08\x1b[?25h")
        self.assertEqual(tracker4.cur_idx, 12)
        # Click on col 5 ('l' at char index 2) on row 14 -> 10 Left arrows (12 - 2 = 10)
        arrows4 = agy_claude_filter.filter_stdin_mouse(b"\x1b[<0;5;14M\x1b[<0;5;14m", tracker4)
        self.assertEqual(arrows4, b"\x1b[D" * 10)
        self.assertEqual(tracker4.cur_idx, 2)

        # 7. Multi-line wrapped input when top_row is None (incremental wrap + cross-line mouse clicks)
        tracker5 = agy_claude_filter.PromptCursorTracker()
        tracker5.term_rows = 40
        tracker5.term_cols = 20
        line_a = "A" * 18
        line_b = "BBBBBBBBBBBBB"
        tracker5.feed_stdin((line_a + line_b).encode("utf-8"))
        tracker5.update_from_output(
            b"\x1b[?25lBB\r\n  BBBBBBBBBBBBB\x1b[K\r\n\xe2\x94\x80\xe2\x94\x80\r\x1b[4A\x1b[15C\x1b[?25h"
        )
        self.assertEqual(tracker5.lines, [line_a, line_b])
        self.assertEqual(tracker5.cur_idx, 31)
        # Click on row 14 (line 0), col 5 (char index 2) -> 29 Left arrows (31 - 2 = 29)
        arrows5a = agy_claude_filter.filter_stdin_mouse(b"\x1b[<0;5;14M\x1b[<0;5;14m", tracker5)
        self.assertEqual(arrows5a, b"\x1b[D" * 29)
        self.assertEqual(tracker5.cur_idx, 2)
        # Click on row 15 (line 1), col 6 (char index 18 + 3 = 21) -> 19 Right arrows (21 - 2 = 19)
        arrows5b = agy_claude_filter.filter_stdin_mouse(b"\x1b[<0;6;15M\x1b[<0;6;15m", tracker5)
        self.assertEqual(arrows5b, b"\x1b[C" * 19)
        self.assertEqual(tracker5.cur_idx, 21)

        # 8. Real agy CJK prompt + partial footer refresh (\n\n...\x1b[2A\x1b[46D and \n\n\n...\x1b[3A\x1b[58D) must NOT reset cur_idx to 0
        tracker6 = agy_claude_filter.PromptCursorTracker()
        tracker6.term_rows = 40
        tracker6.term_cols = 80
        agy_claude_filter.filter_stdin_mouse("你好世界测试".encode("utf-8"), tracker6)
        tracker6.update_from_output(
            b"\x1b[?25l\x1b[?7l\r\x1b[K> \xe4\xbd\xa0\xe5\xa5\xbd\xe4\xb8\x96\xe7\x95\x8c\xe6\xb5\x8b\xe8\xaf\x95\x1b[?7h\x1b[15G"
            b"\n\n\x1b[44C\x1b[1K G\x1b[2A\x1b[46D\x1b[?25h"
            b"\x1b[?25l\x1b[?7l\r\x1b[K> \xe4\xbd\xa0\xe5\xa5\xbd\xe4\xb8\x96\xe7\x95\x8c\xe6\xb5\x8b\xe8\xaf\x95\x1b[?7h\x1b[15G"
            b"\n\n\n\x1b[9D/1M 0% \xe2\x94\x82 Gemini 5h 14% \xe2\x86\xbb28m \xc2\xb7 wk 22% \xe2\x94\x82 Claude 5h 0% \xe2\x86\xbb4h59m \xc2\xb7 wk 30%\x1b[3A\x1b[58D\x1b[?25h"
        )
        self.assertEqual(tracker6.cur_idx, 6)
        self.assertEqual(tracker6.cur_col, 15)
        arrows6 = agy_claude_filter.filter_stdin_mouse(b"\x1b[<0;7;14M\x1b[<0;7;14m", tracker6)
        self.assertEqual(arrows6, b"\x1b[D" * 4)
        self.assertEqual(tracker6.cur_idx, 2)

        # 9. Chunked OSC 11 / DCS terminal replies on stdin must forward to agy intact without polluting tracker.text
        tracker7 = agy_claude_filter.PromptCursorTracker()
        tracker7.term_rows = 40
        tracker7.term_cols = 80
        f1 = agy_claude_filter.filter_stdin_mouse(b"\x1b[?1;2c\x1b]11;rgb:1e1e/", tracker7)
        f2 = agy_claude_filter.filter_stdin_mouse(b"1e1e/1e1e\x07\x1bP>|xterm\x1b\\", tracker7)
        self.assertEqual(f1 + f2, b"\x1b[?1;2c\x1b]11;rgb:1e1e/1e1e/1e1e\x07\x1bP>|xterm\x1b\\")
        self.assertEqual(tracker7.text, "")
        self.assertEqual(tracker7.cur_idx, 0)
        self.assertEqual(agy_claude_filter.filter_stdin_mouse(b"\x1b", tracker7), b"\x1b")

        # 10. Multi-line CJK full redraw with footer \r\x1b[5A\x1b[18C\x1b[?25h restores cursor to upper wrapped line (line 0)
        tracker8 = agy_claude_filter.PromptCursorTracker()
        tracker8.term_rows = 40
        tracker8.term_cols = 30
        tracker8.update_from_output(
            b"\x1b[?7l\r\n\x1b[K> \xe4\xb8\x80\xe4\xba\x8c\xe4\xb8\x89\xe5\x9b\x9b\xe4\xba\x94\xe5\x85\xad\xe4\xb8\x83\xe5\x85\xab\xe4\xb9\x9d\xe5\x8d\x81\xe7\x94\xb2\xe4\xb9\x99\xe4\xb8\x99\x1b[?7h\x1b[29G"
            b"\x1b[?7l\r\n\x1b[K  \xe4\xb8\x81\xe6\x88\x8a\xe5\xb7\xb1\xe5\xba\x9a\xe8\xbe\x9b\x1b[?7h\x1b[13G\r\n"
            b"\xe2\x94\x80\xe2\x94\x80\xe2\x94\x80\r\n Gemini 4 Argon\r\nctx 0\r\nCodex 5h\r\x1b[5A\x1b[18C\x1b[?25h"
        )
        self.assertEqual(tracker8.cur_line_idx, 0)
        self.assertEqual(tracker8.cur_col, 19)
        self.assertEqual(tracker8.cur_idx, 8)
        # Re-assert SGR mouse mode if child outputs ?1006l, and never hold \x1b[?25h in carry
        reasserted, carry_re = agy_claude_filter.transform_chunk(b"\x1b[?1002l\x1b[?1003l\x1b[?1006l")
        self.assertIn(b"\x1b[?1000h\x1b[?1006h", reasserted)
        self.assertEqual(carry_re, b"")
        vis_out, vis_carry = agy_claude_filter.transform_chunk(b"\x1b[?25l\x1b[5D\x1b[?25h")
        self.assertEqual(vis_out, b"\x1b[?25l\x1b[5D\x1b[?25h")
        self.assertEqual(vis_carry, b"")

        # 11. Stdout-only incremental ASCII tracking (real agy startup -> 'a' -> 'b..g') + X10 mouse + swallow wheel/right-click
        tracker9 = agy_claude_filter.PromptCursorTracker()
        tracker9.term_rows = 40
        tracker9.term_cols = 80
        tracker9.update_from_output(b"\r\n>\r\n\xe2\x94\x80\xe2\x94\x80\r\x1b[4A\x1b[2C\x1b[?25h")
        tracker9.update_from_output(
            b"\x1b[?25l\r\n\n\n\n\x1b[J\x1b[4A\x1b[2Ca\r\n\nctx 0 0%\r\nCodex\x1b[K\r\x1b[3A\x1b[3C\x1b[?25h"
        )
        for ch_b in (b"b", b"c", b"d", b"e", b"f", b"g"):
            tracker9.update_from_output(b"\x1b[?25l" + ch_b + b"\x1b[?25h")
        self.assertEqual(tracker9.text, "abcdefg")
        self.assertEqual(tracker9.cur_idx, 7)
        self.assertEqual(tracker9.cur_col, 10)  # 1-based 10 == 0-based col 9
        # Click 'c' (col 5, row 14) -> 5 Left arrows
        self.assertEqual(agy_claude_filter.filter_stdin_mouse(b"\x1b[<0;5;14M\x1b[<0;5;14m", tracker9), b"\x1b[D" * 5)
        # X10/Normal left-click at col 8 ('f', char idx 5), row 14 -> 3 Right arrows (5 - 2 = 3)
        self.assertEqual(
            agy_claude_filter.filter_stdin_mouse(bytes([0x1B, ord("["), ord("M"), 32, 32 + 8, 32 + 14]), tracker9),
            b"\x1b[C" * 3,
        )
        # Wheel (64/65), right-click (2), drag motion (32) must produce 0 bytes to agy
        self.assertEqual(
            agy_claude_filter.filter_stdin_mouse(b"\x1b[<64;10;14M\x1b[<65;10;14M\x1b[<2;10;14M\x1b[<32;10;14M", tracker9),
            b"",
        )

    def test_unread_state_machine_and_debounced_left_icon_pin_click(self):
        import tui

        class DummyScr:
            def getmaxyx(self):
                return (30, 120)
            def erase(self):
                pass
            def addstr(self, *a, **kw):
                pass
            def addch(self, *a, **kw):
                pass
            def refresh(self):
                pass

        self.make_agy()
        self.make_codex()
        seen_file = str(self.home / "seen.json")
        with patch.object(tui.HubTUI, "_init_colors", lambda self: None):
            app = tui.HubTUI(DummyScr(), hub_instance=self.hub, seen_path=seen_file)
            # Initial startup with no initial_selected_id starts in Welcome state (selected_idx == -1)
            self.assertEqual(app.selected_idx, -1)
            self.assertIsNone(app.current_session())

            # First bootstrap: existing historical sessions must have 0 unread noise
            for s in app.sessions:
                self.assertFalse(app.is_session_unread(s))

            # New output completed (`done_idle`) after bootstrapAt highlights unread title and age
            target = next(s for s in app.sessions if s["id"] == A)
            target["updatedAt"] = time.time() + 10.0
            target.update(attentionAt=target["updatedAt"], hasAssistantReply=True,
                          tailStatus="done_idle", interrupted=False)
            target["isLive"] = True
            target["agentState"] = "done_idle"
            self.assertTrue(app.is_session_unread(target))

            # While `working`, unread highlight stays off
            target["agentState"] = "working"
            self.assertFalse(app.is_session_unread(target))

            # Transition to `wait_input` highlights unread title and age, and `_mark_session_seen` clears it
            target["agentState"] = "wait_input"
            self.assertTrue(app.is_session_unread(target))
            app._mark_session_seen(target)
            self.assertFalse(app.is_session_unread(target))

            # Independent wheel scroll in left sidebar: adjusts list_scroll ONLY, never modifies selected_idx
            app._handle_mouse(parsed_event=(65, 5, 4, False))  # Wheel Down
            self.assertEqual(app.selected_idx, -1)
            app._handle_mouse(parsed_event=(64, 5, 4, False))  # Wheel Up
            self.assertEqual(app.selected_idx, -1)

            # Clicking the icon selects without pinning or reordering.
            agy_idx = next(i for i, s in enumerate(app.sessions) if s["id"] == A)
            order = [s["id"] for s in app.sessions]
            click_y = app._list_bounds(30)[0] + agy_idx
            with patch("tui.curses.getmouse", return_value=(0, 2, click_y, 0, getattr(tui.curses, "BUTTON1_PRESSED", 2))):
                app._handle_mouse()
            self.assertEqual(app.current_session()["id"], A)
            self.assertFalse(self.hub.get("agy", A)["pinned"])
            self.assertEqual([s["id"] for s in app.sessions], order)

            # Pinning keeps the selection
            app.toggle_pin_session(app.current_session())
            self.assertTrue(self.hub.get("agy", A)["pinned"])
            self.assertEqual(app.current_session()["id"], A)
            app.toggle_pin_session(app.current_session())
            self.assertFalse(self.hub.get("agy", A)["pinned"])
            self.assertEqual(app.current_session()["id"], A)

            # Top Row 0 borderless Agent filter tabs: `All  Claude  Gemini  Codex`
            tab_ranges = {k: (x0, x1) for k, _, x0, x1 in tui.sidebar_filter_tab_ranges(start_x=1)}
            for tkey in ("claude", "agy", "codex", "all"):
                mx = tab_ranges[tkey][0] + 1
                with patch("tui.curses.getmouse", return_value=(0, mx, 0, 0, getattr(tui.curses, "BUTTON1_PRESSED", 2))):
                    app._handle_mouse()
                self.assertEqual(app.tool_filter, tkey)

            # Universal `◧` sidebar toggle icon at Row 0, `col = left_w - 2` (28 when w=120 -> left_w=30)
            self.assertFalse(app.sidebar_collapsed)
            with patch("tui.curses.getmouse", return_value=(0, 28, 0, 0, getattr(tui.curses, "BUTTON1_PRESSED", 2))):
                app._handle_mouse()
            self.assertTrue(app.sidebar_collapsed)
            # Clicking `◧` at Row 0, `col 1` when collapsed expands the sidebar back
            with patch("tui.curses.getmouse", return_value=(0, 1, 0, 0, getattr(tui.curses, "BUTTON1_PRESSED", 2))):
                app._handle_mouse()
            self.assertFalse(app.sidebar_collapsed)

            # Dragging split divider (`⋮` at col = left_w = 30) resizes `custom_left_w`
            app._handle_mouse(parsed_event=(0, 30, 10, False))
            self.assertTrue(app._dragging_divider)
            app._handle_mouse(parsed_event=(32, 38, 10, False))
            self.assertEqual(app.custom_left_w, 38)
            app._handle_mouse(parsed_event=(0, 38, 10, True))
            self.assertFalse(app._dragging_divider)

    def make_tui_app(self, height=30, width=32):
        import tui
        scr = MagicMock()
        scr.getmaxyx.return_value = (height, width)
        with patch.object(tui.HubTUI, "_init_colors"):
            return tui.HubTUI(scr, hub_instance=self.hub, caf=MagicMock(), seen_path=str(self.home / "seen.json"))

    def test_header_init_uses_only_target_metadata(self):
        import tui
        scr = MagicMock()
        scr.getmaxyx.return_value = (2, 100)
        with patch.object(tui.HubTUI, "_init_colors"), \
             patch.object(tui.HubTUI, "_get_cached_system_metrics") as metrics, \
             patch("mixhub.tui.quota.get_cli_quota_metrics") as quota, \
             patch.object(self.hub, "discover", side_effect=AssertionError("header discovery")), \
             patch.object(tui.HubTUI, "refresh_data") as refresh, \
             patch.object(tui.HubTUI, "_sync_terminal_size"), patch.dict(os.environ, {"TMUX": ""}), \
             patch("tui.curses.color_pair", return_value=0):
            app = tui.HubTUI(scr, header_only=True, header_target=("claude", C[:12]),
                             hub_instance=self.hub, caf=MagicMock(), seen_path=str(self.home / "seen.json"))
            app.draw()
        self.assertEqual(app.current_session()["id"], C)
        self.assertEqual(len(app.sessions), 1)
        self.assertEqual(app.selected_idx, 0)
        self.assertEqual(app.counts, {"working": 0, "live": 1})
        metrics.assert_not_called()
        quota.assert_not_called()
        refresh.assert_not_called()
        self.assertFalse(Path(self.hub.disk_cache_path).exists())

    def test_header_init_falls_back_when_metadata_is_missing_or_unavailable(self):
        import tui
        from types import SimpleNamespace
        for hub in (self.hub, SimpleNamespace(home=str(self.home))):
            with self.subTest(hub=type(hub).__name__), patch.object(tui.HubTUI, "_init_colors"), \
                 patch.object(tui.HubTUI, "_get_cached_system_metrics") as metrics, \
                 patch("mixhub.tui.quota.get_cli_quota_metrics") as quota, \
                 patch.object(tui.HubTUI, "refresh_data") as refresh:
                tui.HubTUI(MagicMock(), header_only=True, header_target=("claude", "missing"),
                           hub_instance=hub, caf=MagicMock(), seen_path=str(self.home / "seen.json"))
                refresh.assert_called_once_with(force=True, reorder=True)
                metrics.assert_not_called()
                quota.assert_not_called()

    def test_main_header_hub_does_not_load_or_write_index_cache(self):
        import tui
        hub = server.Hub(str(self.home), str(self.root), readonly_cache=True)
        with patch("tui.sys.argv", ["tui.py", "--header", "claude", C]), \
             patch("mixhub.tui.app.Hub", return_value=hub) as make_hub, \
             patch("mixhub.tui.caffeinate.CaffeinateController"), patch("mixhub.tui.app.HubTUI") as app_cls, \
             patch("tui.curses.wrapper", side_effect=lambda cb: cb(MagicMock())):
            tui.main()
        make_hub.assert_called_once_with(readonly_cache=True)
        app_cls.assert_called_once()
        self.assertIs(app_cls.call_args.kwargs["hub_instance"], hub)

    def test_header_metadata_watches_only_its_tool_and_throttles(self):
        import tui
        files = {
            "state": self.hub.state_path,
            "selection": str(self.home / "active_selection.json"),
            "agy": str(self.home / ".gemini/antigravity-cli/conversation_summaries.db"),
            "agy_wal": str(self.home / ".gemini/antigravity-cli/conversation_summaries.db-wal"),
            "codex": str(self.home / ".codex/state_5.sqlite"),
            "codex_wal": str(self.home / ".codex/state_5.sqlite-wal"),
            "labels": str(self.home / ".claude/session_labels.json"),
            "transcript": str(self.claude_path),
        }
        for path in files.values():
            self.write_json(path, {})
        for tool, watched in (("agy", {"state", "agy", "agy_wal"}),
                              ("codex", {"state", "codex", "codex_wal", "transcript"}),
                              ("claude", {"state", "labels", "transcript"})):
            with self.subTest(tool=tool):
                app = tui.HubTUI.__new__(tui.HubTUI)
                app.hub, app.header_only, app.header_target = self.hub, True, (tool, C)
                app.sessions = [{"tool": tool, "id": C, "title": "Old", "path": files["transcript"]}]
                app.selected_idx, app._dirty = 0, False
                fresh = dict(app.sessions[0], title="New", shortCwd="~/project")
                with patch("tui.time.monotonic", return_value=100.0) as mono, \
                     patch("mixhub.tui.paths._effective_active_sel_path", return_value=files["selection"]) as selection, \
                     patch("tui.os.stat", wraps=os.stat) as stat, \
                     patch.object(self.hub, "get_lightweight_session_meta", return_value=fresh) as meta, \
                     patch.object(self.hub, "get", side_effect=AssertionError("full get")):
                    self.assertTrue(app._sync_header_session_meta())
                    self.assertEqual(app.sessions[0]["title"], "New")
                    self.assertEqual(app.sessions[0]["shortCwd"], "~/project")
                    self.assertTrue(app._dirty)
                    selection.assert_not_called()
                    stat_paths = {str(call.args[0]) for call in stat.call_args_list}
                    self.assertEqual(stat_paths & set(files.values()), {files[key] for key in watched})
                    meta.assert_called_once_with(tool, C)
                    app._dirty = False
                    stat.reset_mock()
                    mono.return_value = 101.99
                    self.assertFalse(app._sync_header_session_meta())
                    stat.assert_not_called()
                    mono.return_value = 102.0
                    self.assertFalse(app._sync_header_session_meta())
                    self.assertEqual(meta.call_count, 1)
                    # Unrelated selection writes must not invalidate a pinned header.
                    self.write_json(files["selection"], {"id": "different"})
                    mono.return_value = 104.0
                    self.assertFalse(app._sync_header_session_meta())
                    self.assertEqual(meta.call_count, 1)
                    # A source write with an unchanged title must not redraw.
                    self.write_json(files["state"], {"changed": tool})
                    mono.return_value = 106.0
                    self.assertFalse(app._sync_header_session_meta())
                    self.assertEqual(meta.call_count, 2)
                    self.assertFalse(app._dirty)

    def test_header_metadata_legacy_hub_never_forces_discovery(self):
        import tui
        from types import SimpleNamespace
        app = tui.HubTUI.__new__(tui.HubTUI)
        app.hub = SimpleNamespace(home=str(self.home), state_path=self.hub.state_path,
                                  get=MagicMock(return_value={"title": "New", "path": "new-path"}))
        app.header_only, app.header_target = True, None
        app.sessions = [{"id": C, "tool": "claude", "title": "Old"}]
        app.selected_idx, app._dirty = 0, False
        with patch("tui.time.monotonic", return_value=100.0), \
             patch("mixhub.tui.paths._effective_active_sel_path", return_value=str(self.home / "selection.json")) as selection:
            self.assertTrue(app._sync_header_session_meta())
        selection.assert_called_once()
        app.hub.get.assert_called_once_with("claude", C, force=False)
        self.assertEqual(app.sessions[0]["path"], "new-path")

    def test_header_wait_ignores_transcript_and_unrelated_data_writes(self):
        import tui
        app = tui.HubTUI.__new__(tui.HubTUI)
        app.hub, app.header_only, app.reader_only = self.hub, True, False
        app.header_target = ("claude", C)
        app.seen_path = str(self.home / "seen.json")
        app.sessions = [{"id": C, "tool": "claude", "path": str(self.claude_path)}]
        app.selected_idx, app._start_mono = 0, 100.0
        self.write_json(app.attention_path, {"keys": []})
        kq = MagicMock()
        constants = dict(KQ_FILTER_READ=-1, KQ_FILTER_VNODE=-4, KQ_FILTER_SIGNAL=-6, KQ_EV_ADD=1,
                         KQ_EV_CLEAR=32, KQ_NOTE_WRITE=2, KQ_NOTE_RENAME=32, KQ_NOTE_EXTEND=4,
                         KQ_NOTE_DELETE=1, KQ_NOTE_ATTRIB=8)
        with patch.multiple(evq, **constants), \
             patch.object(evq, "kqueue", return_value=kq), \
             patch.object(evq, "kevent"), \
             patch("tui.sys.stdin") as stdin, patch("tui.time.monotonic", return_value=101.0), \
             patch("tui.os.open", return_value=20) as open_fd, patch("tui.os.close") as close_fd, \
             patch("tui.os.makedirs") as mkdir, patch.object(app, "_sync_terminal_size"), \
             patch("tui.subprocess.run") as run, patch("tui.subprocess.Popen") as popen:
            stdin.fileno.return_value = 10
            for _ in range(2):
                app._wait_reader_event_kqueue()
                self.assertEqual(kq.control.call_args.args, ([], 4, 2.0))
            self.assertEqual([c.args for c in open_fd.call_args_list], [(app.attention_path, os.O_RDONLY)] * 2)
            self.assertEqual(close_fd.call_count, 2)
            self.assertEqual(kq.close.call_count, 2)
            mkdir.assert_not_called()
            run.assert_not_called()
            popen.assert_not_called()

    def test_sidebar_periodic_process_poll_waits_thirty_seconds(self):
        import tui
        app = self.make_tui_app()
        app.sidebar_only, app.sessions, app.counts = True, [], {}
        app._last_proc_poll = app._last_sidebar_poll = app._last_gc_poll = 100.0
        app.hub._proc_scanned = 100.0
        kq = MagicMock()
        kq.control.return_value = []
        constants = dict(KQ_FILTER_READ=-1, KQ_FILTER_VNODE=-4, KQ_FILTER_SIGNAL=-6, KQ_EV_ADD=1,
                         KQ_EV_CLEAR=32, KQ_NOTE_WRITE=2, KQ_NOTE_RENAME=32, KQ_NOTE_EXTEND=4, KQ_NOTE_DELETE=1)
        with patch.multiple(evq, **constants), \
             patch.object(evq, "kqueue", return_value=kq), \
             patch.object(evq, "kevent"), \
             patch("tui.sys.stdin"), patch("tui.os.path.exists", return_value=False), \
             patch("tui.os.makedirs"), patch("mixhub.tui.quota._codex_quota_sources", return_value=()), \
             patch("tui.time.monotonic") as mono, patch.object(app, "refresh_data") as refresh, \
             patch.object(app, "_poll_external_terminal_seen"):
            for now in (110.0, 128.0):
                mono.return_value = now
                app._wait_sidebar_event_kqueue()
                self.assertEqual(app.hub._proc_scanned, 100.0)
                self.assertEqual(app._last_proc_poll, 100.0)
            mono.return_value = 130.0
            app._wait_sidebar_event_kqueue()
            self.assertEqual(app.hub._proc_scanned, 0)
            self.assertEqual(app._last_proc_poll, 130.0)
            self.assertEqual(refresh.call_count, 3)

    def test_new_session_menu_lists_only_agents_this_machine_uses(self):
        import tui
        app = self.make_tui_app(height=30)
        app.sessions = [{"id": "c1", "tool": "claude", "title": "t"}]
        installed = {"claude", "codex"}
        with patch.object(self.hub, "sessions", {"claude:c1": {"tool": "claude"}}), \
             patch("tui.shutil.which", side_effect=lambda b: f"/bin/{b}" if b in installed else None), \
             patch("mixhub.providers.deepseek.load_deepseek_api_key", return_value=""):
            app._new_session_items_cache = None
            self.assertEqual([k for k, *_ in app.new_session_items()], ["claude", "codex"])
        with patch.object(self.hub, "sessions", {"claude:c1": {"tool": "claude"}, "agy:g1": {"tool": "agy"}}), \
             patch("tui.shutil.which", return_value=None), \
             patch("mixhub.providers.deepseek.load_deepseek_api_key", return_value="sk-x"):
            app._new_session_items_cache = None
            self.assertEqual([k for k, *_ in app.new_session_items()], ["claude", "agy"])

    def test_filter_menu_lists_only_agents_with_sessions(self):
        app = self.make_tui_app(height=30)
        app.sessions = [{"id": "c1", "tool": "claude", "title": "t"}, {"id": "o1", "tool": "opencode", "title": "t"}]
        with patch.object(self.hub, "sessions", {"claude:c1": {"tool": "claude"}, "opencode:o1": {"tool": "opencode"}}):
            self.assertEqual([k for k, *_ in app.provider_filter_items()], [None, "claude", "opencode"])
            app.provider_filter = "codex"  # an active filter keeps its row even with no sessions left
            self.assertEqual([k for k, *_ in app.provider_filter_items()], [None, "claude", "codex", "opencode"])
        app.provider_filter = None
        app.sessions.append({"id": "d1", "tool": "claude", "model": "deepseek-chat", "title": "t"})
        self.assertNotIn("deepseek", [k for k, *_ in app.provider_filter_items()])  # a model, not an agent

    def test_opencode_in_tui_menu_badge_and_resume(self):
        import tui
        app = self.make_tui_app(height=30)
        with patch.object(self.hub, "sessions", {}), \
             patch("tui.shutil.which", side_effect=lambda b: f"/bin/{b}" if b in ("claude", "opencode") else None), \
             patch("mixhub.providers.deepseek.load_deepseek_api_key", return_value="sk-x"):
            app._new_session_items_cache = None
            self.assertEqual([k for k, *_ in app.new_session_items()], ["claude", "opencode"])
        self.assertEqual(tui._agent_badge("opencode", "done_idle")[1], tui.P_AGENT_OPENCODE)
        self.assertEqual(tui.interactive_resume_cmd({"tool": "opencode", "id": "ses_a", "cwd": str(self.home)}),
                         ["opencode", "-s", "ses_a", str(self.home)])
        self.assertIn("opencode", [k for k, *_ in tui.sidebar_filter_tab_ranges(available_tools={"claude", "opencode"})])
        with patch("mixhub.tmux.commands._build_hubpool_cli_wrapper_cmd", return_value="true") as wrap, \
             patch("tui.subprocess.Popen"), patch("tui.subprocess.run"), \
             patch.object(app, "_write_active_selection"), patch.object(app, "_mark_session_seen"), \
             patch("mixhub.tmux.pool._record_hubpool_window"), patch.dict(tui._NEW_HUBPOOL_SESSIONS, clear=False):
            app._launch_new_agent_session("opencode")
        self.assertTrue(wrap.call_args.args[4].endswith("; opencode"))
        self.assertTrue(tui._NEW_WIN_RE.match("w-opencode-new-123"))

    def test_reader_rewrites_every_cell_when_the_previewed_session_changes(self):
        app = self.make_tui_app(height=30, width=100)
        app.reader_only = True
        app.loaded_key = ("claude", "a", 100.0, "working")
        app._present()
        app.stdscr.redrawwin.reset_mock()
        app.loaded_key = ("claude", "a", 101.0, "done_idle")
        app._present()  # same session, new message: incremental diff only
        app.stdscr.redrawwin.assert_not_called()
        app.loaded_key = ("codex", "b")
        app._present()
        app.stdscr.redrawwin.assert_called_once()

    def test_reply_line_keeps_the_spaces_around_inline_code(self):
        app = self.make_tui_app(height=30, width=120)
        app.stdscr.addstr.reset_mock()
        with patch("tui.curses.color_pair", return_value=0):
            app._draw_asst_line(3, 0, "the prompt now says `host`, and `a` `b` done", 100)
        drawn = {c.args[1]: c.args[2] for c in app.stdscr.addstr.call_args_list if c.args[0] == 3}
        line = "".join(drawn[x] for x in sorted(drawn))
        self.assertEqual(line, "the prompt now says host, and a b done")

    def test_quota_footer_shows_agents_that_have_any_session_on_this_machine(self):
        # A row per agent that has sessions here, running or not; headless script runs and subagents do not count
        import tui
        app = self.make_tui_app(height=30)
        app.sessions = []
        with patch.object(self.hub, "sessions", {"claude:c1": {"tool": "claude"}, "codex:x": {"tool": "codex"}}):
            self.assertEqual([k for k, *_ in app._footer_agents()], ["cc", "cx"])
        with patch.object(self.hub, "sessions", {"claude:c1": {"tool": "claude"},
                                                 "codex:x": {"tool": "codex", "automated": True, "isTemp": True},
                                                 "agy:s": {"tool": "agy", "isSubagent": True}}):
            self.assertEqual([k for k, *_ in app._footer_agents()], ["cc"])
        with patch.object(self.hub, "sessions", {}):
            self.assertEqual(app._footer_agents(), [])

    def test_refresh_logs_why_a_session_became_working(self):
        app = self.make_tui_app(height=20)
        row = {"id": "s1", "tool": "claude", "title": "t", "isLive": True, "agentState": "done_idle"}
        with patch.object(self.hub, "listing", return_value={"sessions": [dict(row)], "counts": {}}):
            app.refresh_data(force=True)
        flipped = dict(row, agentState="working", stateSource="probe", probeState="working", tailStatus="done_idle")
        with patch.object(self.hub, "listing", return_value={"sessions": [flipped], "counts": {}}), \
             patch("mixhub.tui.diag.log_tui_diag") as diag:
            app.refresh_data(force=True)
            app.refresh_data(force=True)  # staying `working` must not log again
        calls = [c for c in diag.call_args_list if c.args and c.args[0] == "became_working"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0].kwargs["source"], "probe")
        self.assertEqual(calls[0].kwargs["tail"], "done_idle")

    def test_sidebar_at_top_keeps_pinned_rows_still_when_a_row_appears_above_selection(self):
        app = self.make_tui_app(height=20)
        app.sidebar_only = True
        pinned = {"id": "p", "tool": "claude", "title": "pinned", "pinned": True, "isLive": False}
        active = [{"id": f"a{i}", "tool": "codex", "title": f"a{i}", "isLive": True, "agentState": "done_idle"}
                  for i in range(3)]
        recent = [{"id": f"r{i}", "tool": "codex", "title": f"r{i}", "isLive": False, "updatedAt": 100 - i}
                  for i in range(40)]  # longer than the screen, like a real Recent section
        app.sessions = [pinned] + [dict(s) for s in active] + [dict(s) for s in recent]
        app.selected_idx, app.list_scroll = 3, 0  # a2 selected, list at the very top
        fresh = [pinned, {"id": "new", "tool": "claude", "title": "new", "isLive": True, "agentState": "working"}] + active + recent
        with patch.object(self.hub, "listing", return_value={"sessions": fresh, "counts": {}}):
            app.refresh_data(force=True, reorder=True)
        self.assertEqual(app.current_session()["id"], "a2")
        self.assertEqual(app.list_scroll, 0)

    def test_sidebar_stable_refresh_and_viewport_anchor(self):
        app = self.make_tui_app(height=12)
        rows = [{"id": str(i), "tool": "codex", "title": str(i), "isLive": False} for i in range(20)]
        app.sessions = rows
        app.selected_idx, app.list_scroll = 10, 7
        old_selected = app.current_session()
        fresh = [dict(s) for s in reversed(rows) if s["id"] != "1"]
        next(s for s in fresh if s["id"] == "10").update(isLive=True, agentState="working")
        fresh.insert(0, {"id": "new", "tool": "claude", "title": "New session"})
        with patch.object(self.hub, "listing", return_value={"sessions": fresh, "counts": {}}):
            app.refresh_data(force=True)
            self.assertEqual([s["id"] for s in app.sessions], [str(i) for i in range(20) if i != 1] + ["new"])
            self.assertIs(app.current_session(), old_selected)
            self.assertTrue(app.current_session()["isLive"])
            self.assertEqual(app.selected_idx - app.list_scroll, 3)
            app.refresh_data(reorder=True)
            self.assertEqual([s["id"] for s in app.sessions], [s["id"] for s in fresh])
            self.assertEqual(app.current_session()["id"], "10")
            self.assertEqual(app.selected_idx - app.list_scroll, 3)
            # A filter change adopts canonical order even without explicit reorder.
            app.sessions.reverse()
            app.tool_filter = "codex"
            app.refresh_data()
            self.assertEqual([s["id"] for s in app.sessions], [s["id"] for s in fresh])

        # Browsing a viewport away from the selection must not jump back to it.
        app.selected_idx, app.list_scroll = 0, 10
        with patch.object(self.hub, "listing", return_value={"sessions": list(reversed(fresh)), "counts": {}}):
            app.refresh_data()
        self.assertEqual(app.list_scroll, 10)

    def test_sidebar_kqueue_input_priority_and_spinner(self):
        import tui
        app = self.make_tui_app()
        app.counts = {"working": 0}
        app.sessions = [{"id": "active", "agentState": "working"}]
        app._last_sidebar_poll = 100.0
        app._spinner_frame = -1
        app._dirty = False
        kq = MagicMock()
        # Patch kqueue constants too so this regression also runs off macOS.
        constants = dict(KQ_FILTER_READ=-1, KQ_FILTER_VNODE=-4, KQ_FILTER_SIGNAL=-6, KQ_EV_ADD=1, KQ_EV_CLEAR=32,
                         KQ_NOTE_WRITE=2, KQ_NOTE_RENAME=32, KQ_NOTE_EXTEND=4, KQ_NOTE_DELETE=1)
        session_dirs = {str(self.home / p) for p in (
            ".codex/thread-writer-locks", ".claude/sessions", ".gemini/antigravity-cli/presence",
        )}
        with patch.multiple(evq, **constants), \
             patch.object(evq, "kqueue", return_value=kq), \
             patch.object(evq, "kevent", side_effect=lambda ident, **kw: MagicMock(ident=ident)), \
             patch("tui.sys.stdin") as stdin, patch("tui.os.path.isdir", return_value=True), \
             patch("tui.os.path.exists", side_effect=lambda path: str(path) in session_dirs), \
             patch("mixhub.tui.quota._codex_quota_sources", return_value=()), \
             patch.dict(tui._QUOTA_SUMMARY_CACHE, {"codex_sources": (str(self.home), ())}), \
             patch("tui.os.open", side_effect=[20, 21, 22] * 5), patch("tui.os.close") as close, \
             patch("tui.time.monotonic") as mono, patch.object(app, "refresh_data") as refresh, \
             patch.object(app.hub, "gc_idle_resident"):  # its real `ps` would hang off macOS with os.close mocked
            stdin.fileno.return_value = 10
            kq.control.return_value = []
            mono.return_value = 100.25
            app._wait_sidebar_event_kqueue()
            self.assertEqual(kq.control.call_args.args[1:], (8, round(tui.WORKING_DIAMOND_PERIOD - (100.25 % tui.WORKING_DIAMOND_PERIOD), 4)))
            self.assertTrue(app._dirty)
            refresh.assert_not_called()

            # Directory first, stdin second: input wins and directory refresh stays pending.
            kq.control.return_value = [MagicMock(ident=20, filter=-4), MagicMock(ident=10, filter=-1)]
            mono.return_value = 101.1
            app._wait_sidebar_event_kqueue()
            self.assertEqual(app._last_input_ts, 101.1)
            self.assertTrue(app._pending_dir_refresh)
            refresh.assert_not_called()

            # The pending directory refresh then runs on the very next wakeup (0s kqueue timeout), ignoring the
            # 0.8s input throttle, with a process rescan and a re-bucket (`/exit` moves a row `Active -> Recent`)
            kq.control.return_value = []
            mono.return_value = 101.6
            app.hub._proc_scanned = 123.0
            app._wait_sidebar_event_kqueue()
            self.assertEqual(kq.control.call_args.args[1:], (8, 0.0))
            refresh.assert_called_once_with(force=True, reorder=True)
            self.assertEqual(app.hub._proc_scanned, 0)
            self.assertFalse(app._pending_dir_refresh)
            mono.return_value = 102.0
            app._wait_sidebar_event_kqueue()
            refresh.assert_called_once_with(force=True, reorder=True)

            app.sessions = []
            app.counts = {}
            mono.return_value = 104.0
            app._wait_sidebar_event_kqueue()
            self.assertEqual(kq.control.call_args.args[1:], (8, 2.0))
            self.assertEqual(refresh.call_count, 2)
            self.assertEqual(close.call_count, 15)
            self.assertEqual(kq.close.call_count, 5)

    def test_sidebar_draw_geometry_unread_and_spinner(self):
        import tui
        app = self.make_tui_app()
        app.sidebar_only = True
        app.sessions = [dict(id="row", tool="codex", title="会话标题", updatedAt=time.time() - 60, isLive=True)]
        app.counts = {"live": 1, "liveMemoryMB": 2048, "wait_input": 5, "done_idle": 13}
        color = lambda pair: pair << 8
        with patch("tui.curses.color_pair", side_effect=color), patch.object(app, "_rebalance_sidebar_width_if_needed"), \
             patch.object(app, "is_session_unread", return_value=True), patch("tui.time.monotonic", return_value=0.46):
            for mode in ("normal", "hover", "selected"):
                app.selected_idx = 0 if mode == "selected" else -1
                app.hover_idx = 0 if mode == "hover" else -1
                for state in ("wait_input", "done_idle", "working"):
                    with self.subTest(mode=mode, state=state):
                        app.sessions[0]["agentState"] = state
                        app.counts["working"] = int(state == "working")
                        app.stdscr.addstr.reset_mock()
                        app.draw()
                        calls = [c.args for c in app.stdscr.addstr.call_args_list]
                        row = {c[1]: c for c in calls if c[0] == 3}
                        self.assertEqual(row[2][2], "◈" if state == "working" else "◇")
                        self.assertEqual(tui.disp_width(row[5][2]), 21)
                        self.assertEqual(tui.disp_width(row[27][2]), 3)
                        if mode in ("hover", "selected"):
                            self.assertEqual(row[1][2], "   ")
                            self.assertEqual(row[1][3], color(tui.P_USER_BOX if mode == "selected" else tui.P_HOVER))
                        else:
                            self.assertNotIn(1, row)
                        # Col 0 only ever gets the full-row blank (clears stale / half CJK cells), never a glyph
                        self.assertEqual(set(row[0][2]), {" "})
                        self.assertEqual(row[0][3], color(tui.P_UNSEL))
                        unread = state != "working" and mode != "selected"
                        expected_title_attr = (
                            (color(tui.P_WHITE) | tui.curses.A_BOLD)
                            if (mode == "selected" or unread)
                            else color(tui.P_WHITE if mode == "hover" else tui.P_UNSEL)
                        )
                        expected_age_attr = (
                            (color(tui.P_WAIT) | tui.curses.A_BOLD)
                            if unread
                            else color(tui.P_META if mode in ("selected", "hover") else tui.P_DIM)
                        )
                        self.assertEqual(row[5][3], expected_title_attr)
                        self.assertEqual(row[27][3], expected_age_attr)
                        if mode == "hover":
                            self.assertEqual(row[27][2], "  ☆")
                        self.assertNotIn(30, row)
                        self.assertIn((2, 1, "─" * 30, color(tui.P_BORDER)), calls)
                        self.assertTrue(any("2.0G" in str(c[2]) for c in calls if c[0] == 1))
                        self.assertFalse(any(c[2] in ("⋮", "│") for c in calls))
                        self.assertFalse(any(c[0] == 28 for c in calls))

        # Top and bottom margins are excluded from mouse hit testing.
        app.selected_idx = -1
        app._handle_mouse(parsed_event=(0, 2, 2, False))
        self.assertEqual(app.selected_idx, -1)
        with patch.object(app, "_on_sidebar_selection_changed") as activate:
            app._handle_mouse(parsed_event=(0, 2, 3, False))
            self.assertEqual(app.selected_idx, 0)
            activate.assert_called_once_with(activate_live=False)
            app._handle_mouse(parsed_event=(0, 2, 3, False))
            activate.assert_called_with(activate_live=True)
        app._handle_mouse(parsed_event=(0, 30, 8, False))
        self.assertFalse(app._dragging_divider)
        self.assertEqual(app._list_bounds(12), (3, 10, 7))
        self.assertEqual(app._list_bounds(11), (2, 9, 7))
        app.sessions *= 30
        app.selected_idx, app.list_scroll = 25, 0
        app._ensure_selected_visible()
        # `--sidebar` always keeps its `▾ Active` header, so session 25 sits on visual row 26
        self.assertEqual(app._sidebar_display_rows()[0].get("key"), "active")
        self.assertEqual(app.list_scroll, 2)

    def test_sidebar_palette_fixed_width_and_tmux_bindings(self):
        import tui
        app = self.make_tui_app()
        for colors, fg in ((256, 189), (8, tui.curses.COLOR_WHITE)):
            with patch("tui.curses.start_color"), patch("tui.curses.use_default_colors"), \
                 patch.object(tui.curses, "COLORS", colors, create=True), patch("tui.curses.init_pair") as init:
                app._init_colors()
                for pair, bg in ((tui.P_AGENT_CODEX, -1), (tui.P_AGENT_CODEX_SEL, 237 if colors == 256 else tui.curses.COLOR_BLACK), (tui.P_AGENT_CODEX_HOV, 235 if colors == 256 else tui.curses.COLOR_BLACK)):
                    init.assert_any_call(pair, fg, bg)
                if colors == 256:
                    init.assert_any_call(tui.P_AGENT_CLAUDE, 174, -1)
                    init.assert_any_call(tui.P_AGENT_AGY, 75, -1)
        for i, frame in enumerate(tui.WORKING_DIAMOND_FRAMES):
            cells = tui.render_sidebar_row_cells(dict(tool="codex", isLive=True, agentState="working"), is_unread=True, now_mono=i * tui.WORKING_DIAMOND_PERIOD + 0.01)
            self.assertEqual(cells["dot_ch"], frame)
            self.assertEqual(cells["unread_ch"], " ")
            self.assertFalse(cells["show_dot"])
            self.assertEqual(tui.disp_width(frame), 1)
        self.assertEqual(tui.disp_width("◈"), 1)

        with patch.dict(os.environ, {"TMUX": "test"}), patch("tui.subprocess.run") as run:
            app.sidebar_only = True
            self.assertEqual(app._effective_left_w(80, True), 32)
            self.assertEqual(app._effective_left_w(24, True), 24)
            run.return_value.stdout = "%1|0|100|0\n%2|33|100|0"
            app.custom_left_w = 60
            app._rebalance_sidebar_width_if_needed(33)
            self.assertIn(["tmux", "-L", "mixhub", "resize-pane", "-t", "%1", "-x", "32"], [c.args[0] for c in run.call_args_list])
            run.reset_mock()
            app._rebalance_sidebar_width_if_needed(32)
            self.assertFalse(any("resize-pane" in c.args[0] for c in run.call_args_list))
            run.reset_mock()
            run.return_value.stdout = "%1|0|100|1\n%2|33|100|1"
            app._rebalance_sidebar_width_if_needed(80)
            self.assertFalse(any("resize-pane" in c.args[0] for c in run.call_args_list))

        with patch("tui.subprocess.run") as run:
            tui.configure_tmux_mouse_and_copy(None)
        cmds = [c.args[0] for c in run.call_args_list]
        self.assertIn(["tmux", "-L", "mixhub", "set-option", "-g", "mode-style", "bg=colour238,fg=colour255"], cmds)
        self.assertIn(["tmux", "-L", "mixhub", "unbind-key", "-T", "root", "MouseDrag1Border"], cmds)
        for table in ("copy-mode", "copy-mode-vi"):
            self.assertIn(["tmux", "-L", "mixhub", "bind-key", "-T", table, "MouseDown1Pane", "select-pane", ";", "send-keys", "-X", "cancel"], cmds)

        for command in ("tmux", "python3"):
            with self.subTest(command=command), patch("tui.subprocess.run") as run, \
                 patch("mixhub.tmux.outer.get_outer_hub_pane_ids", return_value=("%1", "%2")), patch("mixhub.tmux.workspace.configure_tmux_mouse_and_copy"):
                run.return_value.returncode = 0
                run.return_value.stdout = f"%1|0|{command}"
                tui.ensure_outer_dual_pane_layout()
                cmds = [c.args[0] for c in run.call_args_list]
                split_idx = next(i for i, cmd in enumerate(cmds) if "split-window" in cmd)
                split = cmds[split_idx]
                if command == "tmux":
                    self.assertEqual(split[:-1], ["tmux", "-L", "mixhub", "split-window", "-h", "-b", "-l", "32", "-t", "%1"])
                else:
                    self.assertEqual(cmds[split_idx + 1], ["tmux", "-L", "mixhub", "resize-pane", "-t", "%1", "-x", "32"])

    def test_codex_thread_writer_lock_flock_discovery(self):
        import fcntl
        self.make_codex()
        # Simulate an old transcript file (2 days old) that is newly resumed via `codex resume` picker
        old_ts = time.time() - 2 * 86400
        codex_path = self.home / (".codex/sessions/2026/10/23/rollout-test-" + X + ".jsonl")
        os.utime(codex_path, (old_ts, old_ts))

        lock_dir = self.home / ".codex/thread-writer-locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_file = lock_dir / f"{X}.lock"
        lock_file.write_text("", encoding="utf-8")

        # Without an active flock holder, lock file alone does not make session live
        self.hub.processes = lambda: [self.process("codex", 555)]
        self.assertFalse(self.hub.get("codex", X, True)["isLive"])

        # Hold an exclusive flock in a child process (just like codex app-server --managed-daemon)
        holder = subprocess.Popen(
            [
                sys.executable,
                "-c",
                f"import fcntl, time; f=open({str(lock_file)!r}, 'r+'); fcntl.flock(f.fileno(), fcntl.LOCK_EX); time.sleep(10)",
            ]
        )
        try:
            deadline = time.time() + 3.0
            while time.time() < deadline:
                if X in self.hub._probe_active_lock_uuids(lock_dir):
                    break
                time.sleep(0.05)
            s = self.hub.get("codex", X, True)
            self.assertTrue(s["isLive"])
            self.assertEqual(s["agentState"], "wait_input")
            self.assertGreater(s["updatedAt"], old_ts + 86400)
        finally:
            holder.terminate()
            holder.wait()

    def test_module_a_handoff_10_sections_and_deepseek_provider(self):
        import handoff
        import tui

        self.write_rows(
            self.claude_path,
            [
                claude_user("实现跨 Agent 接力模块。严禁新建顶层目录，必须通过全部单测。api_key=sk-1234567890abcdefghijkl"),
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "[IRREV] 决定采用 SQLite WAL + 10 节交接包。\n已否决方案：直接改写历史 JSONL。"},
                            {"type": "tool_use", "name": "Edit", "id": "tu1", "input": {"file_path": "/workspace/server.py"}},
                            {"type": "tool_use", "name": "Bash", "id": "tu2", "input": {"command": "python3 -m unittest -v"}},
                        ],
                    },
                },
                claude_user("继续完成 Codex 接力验证"),
            ],
        )

        res = self.hub.handoff({"id": C[:8], "to": "codex", "note": "Switch to Codex for verification"})
        self.assertTrue(res["ok"])
        self.assertEqual(res["handoffChain"], ["claude", "codex"])
        self.assertLess(res["token_estimate"], 2000)
        self.assertTrue(Path(res["bundle_path"]).is_file())

        md = res["markdown"]
        for idx, title in enumerate(handoff.SECTION_TITLES, 1):
            self.assertIn(f"## {idx}. {title}", md)
        self.assertNotIn("sk-1234567890abcdefghijkl", md)
        self.assertIn("[REDACTED]", md)
        self.assertIn("/workspace/server.py", md)

        # Verify server.py exposes handoffChain while tui.py keeps the source session's own Claude orange color and hollow diamond `◇`
        s_updated = self.hub.get("claude", C, True)
        self.assertEqual(s_updated["handoffChain"], ["claude", "codex"])
        cells = tui.render_sidebar_row_cells(s_updated, left_w=32, is_unread=False)
        self.assertEqual(cells["dot_ch"], "◇" if s_updated.get("isLive") else "·")
        self.assertEqual(cells["dot2_ch"], " ")
        self.assertEqual(cells["dot_pair"], tui.P_AGENT_CLAUDE)
        self.assertEqual(cells["dot2_pair"], tui.P_AGENT_CLAUDE)

        # Verify --provider deepseek uses native codex -c model_provider="deepseek" and resolves to delegate mode
        ds_res = self.hub.handoff({"id": C[:8], "to": "deepseek", "provider": "deepseek", "dry_run": True})
        self.assertIn('codex --no-daemon -c model_provider="deepseek"', ds_res["command"])
        self.assertEqual(ds_res["mode"], "delegate")
        self.assertFalse(ds_res["contract"].get("de_anchored", False))
        self.assertIn("decisions_rationale", ds_res["contract"])

        # Verify handoff.yaml sidecar file and structured fields (verified, unverified, failed_attempts)
        self.assertTrue(Path(res["yaml_path"]).is_file())
        yaml_text = Path(res["yaml_path"]).read_text(encoding="utf-8")
        self.assertIn("verified:", yaml_text)
        self.assertIn("unverified:", yaml_text)
        self.assertIn("failed_attempts:", yaml_text)
        self.assertIn("status: partially_implemented", yaml_text)

        # Scenario A (escalate): weak/daily -> mastermind strips predecessor subjective narrative (de_anchored=True)
        esc_res = self.hub.handoff({"id": C[:8], "to": "claude", "mode": "escalate", "dry_run": True})
        self.assertEqual(esc_res["mode"], "escalate")
        self.assertTrue(esc_res["contract"]["de_anchored"])
        self.assertNotIn("decisions_rationale", esc_res["contract"])
        self.assertIn("DE-ANCHORED", esc_res["markdown"])

        # Scenario C (review): cross-company independent blind review strips self-justification (blind_review=True)
        rev_res = self.hub.handoff({"id": C[:8], "to": "codex", "mode": "review", "dry_run": True})
        self.assertEqual(rev_res["mode"], "review")
        self.assertEqual(rev_res["status"], "ready_for_review")
        self.assertTrue(rev_res["contract"]["blind_review"])
        self.assertNotIn("decisions_rationale", rev_res["contract"])
        self.assertIn("BLIND REVIEW", rev_res["markdown"])

    def test_module_b_rules_sync_bidirectional_sha256_tamper_and_policy_guard(self):
        import rules_sync

        proj = self.home / "proj_rules"
        proj.mkdir(parents=True, exist_ok=True)
        agents_md = proj / "AGENTS.md"
        agents_md.write_text(
            "# Project Rules\n- Zero emoji in commits.\n\n```json hub-policy\n{\"verify_cmd\": \"pytest -q\"}\n```\n",
            encoding="utf-8",
        )
        claude_md = proj / "CLAUDE.md"
        claude_md.write_text("# Local Claude Notes\nKeep this custom note.\n", encoding="utf-8")

        # 1. Dry-run preview produces unified diff without modifying files
        dry = self.hub.sync_rules({"dir": str(proj), "dry_run": True})
        self.assertTrue(dry["ok"])
        self.assertTrue(dry["files"]["CLAUDE.md"]["changed"])
        self.assertIn("+<!-- BEGIN HUB SYNC", dry["files"]["CLAUDE.md"]["diff"])
        self.assertFalse((proj / ".gemini" / "GEMINI.md").exists())

        # 2. Real sync writes .gemini/GEMINI.md while keeping CLAUDE.md 100% read-only and untouched
        applied = self.hub.sync_rules({"dir": str(proj), "dry_run": False})
        self.assertTrue(applied["ok"])
        self.assertFalse(applied["files"]["CLAUDE.md"]["wrote"])
        self.assertTrue(applied["files"]["CLAUDE.md"]["read_only"])
        claude_text = claude_md.read_text(encoding="utf-8")
        gemini_text = (proj / ".gemini" / "GEMINI.md").read_text(encoding="utf-8")
        self.assertEqual(claude_text, "# Local Claude Notes\nKeep this custom note.\n")
        self.assertIn("<!-- END HUB SYNC -->", gemini_text)

        # 3. Tamper detection when .gemini/GEMINI.md managed block is altered directly
        gemini_md = proj / ".gemini" / "GEMINI.md"
        gemini_md.write_text(gemini_text.replace("Zero emoji", "TAMPERED RULE"), encoding="utf-8")
        tampered = self.hub.sync_rules({"dir": str(proj), "dry_run": False})
        self.assertFalse(tampered["ok"])
        self.assertIn(".gemini/GEMINI.md", tampered["tampered_targets"])
        # Force flag repairs tampered block
        repaired = self.hub.sync_rules({"dir": str(proj), "dry_run": False, "force": True})
        self.assertTrue(repaired["ok"])

        # 4. Anti-self-escalation guard when hub-policy block changes without approval
        agents_md.write_text(
            "# Project Rules\n- Zero emoji in commits.\n\n```json hub-policy\n{\"verify_cmd\": \"true\"}\n```\n",
            encoding="utf-8",
        )
        unapproved = self.hub.sync_rules({"dir": str(proj), "dry_run": False})
        self.assertFalse(unapproved["ok"])
        self.assertTrue(unapproved["policy_approval_required"])
        approved = self.hub.sync_rules({"dir": str(proj), "dry_run": False, "approve_policy": True})
        self.assertTrue(approved["ok"])
        self.assertFalse(approved["policy_approval_required"])

        # 5. Bidirectional bootstrap when AGENTS.md is absent but CLAUDE.md exists
        proj2 = self.home / "proj_bootstrap"
        proj2.mkdir(parents=True, exist_ok=True)
        (proj2 / "CLAUDE.md").write_text("# Bootstrapped Rule\nAlways run tests.\n", encoding="utf-8")
        boot = rules_sync.sync_rules(str(proj2), state_dir=str(self.root / "data"))
        self.assertTrue(boot["ok"])
        self.assertEqual(boot["bootstrapped_from"], "CLAUDE.md")
        self.assertTrue((proj2 / "AGENTS.md").is_file())
        self.assertTrue((proj2 / ".gemini" / "GEMINI.md").is_file())

    def test_preferences_host_label_and_ignore_prompt_patterns_feed_server(self):
        prefs_path = self.home / "Preferences.test"
        self.write_json(
            prefs_path,
            {"host_label": "Studio", "ignore_prompt_patterns": ["# Task: Label Vendor Policy", "has:internal grading rubric"]},
        )
        with patch.dict(os.environ, {"MIXHUB_PREFERENCES_PATH": str(prefs_path)}):
            self.assertEqual(self.hub.listing({})["host"], "Studio")
            rules = server._load_local_rules()
            self.assertIn("# Task: Label Vendor Policy", rules["automated_prefixes"])
            self.assertIn("internal grading rubric", rules["automated_substrings"])
            # Plain patterns are case-insensitive prefixes; `has:` patterns are case-insensitive substrings.
            self.assertTrue(server.is_automated_or_subagent_text("# task: label vendor policy for item 3"))
            self.assertTrue(server.is_automated_or_subagent_text("Please apply the Internal Grading Rubric now"))
            self.assertFalse(server.is_automated_or_subagent_text("Fix the login bug # Task: Label Vendor Policy"))
        with patch.dict(os.environ, {"MIXHUB_PREFERENCES_PATH": str(self.home / "Preferences.absent")}):
            self.assertEqual(self.hub.listing({})["host"], server.clean_hostname())
            self.assertFalse(server.is_automated_or_subagent_text("Please apply the Internal Grading Rubric now"))

    def test_send_types_into_the_tmux_pane_or_resumes_a_dormant_session(self):
        """The composers' send path (`POST /api/sessions/<tool>/<id>/send` -> `Hub.action(..., "send", ...)`)."""
        import mixhub.hub.send as send_mod

        ran, popened = [], []

        def fake_run(argv, **kw):
            ran.append(argv)
            out = "hub:0.1\t/dev/ttys013\n" if "list-panes" in argv and argv[:3] == ["tmux", "-L", "hubpool"] else ""
            return subprocess.CompletedProcess(argv, 0, out, "")

        def fake_popen(argv, **kw):
            popened.append((argv, kw.get("cwd")))
            return MagicMock()

        with patch.object(send_mod.subprocess, "run", fake_run), patch.object(send_mod.subprocess, "Popen", fake_popen):
            # A dormant Codex session is resumed headless, with the attached file paths in the prompt
            self.make_codex()
            res = self.hub.action("codex", X, "send", {"text": "Inspect this screenshot", "attachments": ["/tmp/170.jpg"]})
            self.assertEqual(res["delivery_mode"], "headless_resume")
            self.assertIn("/tmp/170.jpg", res["prompt"])
            self.assertEqual(popened[-1][0][:4], ["codex", "exec", "resume", X])

            # A dormant Claude session is only resumed when headless exec is allowed (the web composer does not)
            blocked = self.hub.action("claude", C, "send", {"text": "Hello", "allow_headless_exec": False})
            self.assertFalse(blocked["ok"])
            self.assertIn("not in a live tmux pane", blocked["message"])
            self.assertEqual(len(popened), 1)

            # A live session in a MixHub pane gets the text typed in, then Enter
            self.live_claude(pid=101, status="idle")
            dry = self.hub.action("claude", C, "send", {"text": ".", "allow_headless_exec": False, "dry_run": True})
            self.assertEqual((dry["delivery_mode"], dry["pane_target"]), ("tmux_pane", "hubpool:hub:0.1"))
            self.assertFalse([argv for argv in ran if "send-keys" in argv])
            sent = self.hub.action("claude", C, "send", {"text": "Check the test output", "allow_headless_exec": False})
            self.assertTrue(sent["ok"])
            keys = [argv for argv in ran if "send-keys" in argv]
            self.assertEqual(keys, [
                ["tmux", "-L", "hubpool", "send-keys", "-t", "hub:0.1", "-l", "--", "Check the test output"],
                ["tmux", "-L", "hubpool", "send-keys", "-t", "hub:0.1", "Enter"],
            ])
            self.assertEqual(len(popened), 1)

    def test_send_route_reaches_the_hub_action(self):
        httpd = server.http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        httpd.hub = self.hub
        thread = threading.Thread(target=httpd.serve_forever, daemon=True); thread.start()
        base = "http://127.0.0.1:" + str(httpd.server_port)
        try:
            request = urllib.request.Request(
                base + "/api/sessions/claude/" + C + "/send",
                data=json.dumps({"text": "hi", "allow_headless_exec": False, "dry_run": True}).encode(),
                headers={"Content-Type": "application/json"},
            )
            res = json.load(urllib.request.urlopen(request))
            self.assertFalse(res["ok"])
            self.assertEqual(res["session_key"], "claude:" + C)
            request = urllib.request.Request(
                base + "/api/sessions/claude/" + C + "/send", data=json.dumps({"text": 3}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with self.assertRaises(urllib.error.HTTPError) as error: urllib.request.urlopen(request)
            self.assertEqual(error.exception.code, 400)
        finally:
            httpd.shutdown(); httpd.server_close(); thread.join()


    def test_module_e_web_svg_icons_greetings_and_mixhub_packaging(self):
        repo_root = Path(__file__).resolve().parents[1]
        html = (repo_root / "mixhub" / "web" / "static" / "index.html").read_text(encoding="utf-8")
        app_js = (repo_root / "mixhub" / "web" / "static" / "app.js").read_text(encoding="utf-8")
        style_css = (repo_root / "mixhub" / "web" / "static" / "style.css").read_text(encoding="utf-8")

        # Verify MixHub branding, official SVG icons, split-color relay badges, and time-of-day greetings
        self.assertIn("MixHub", html)
        self.assertIn("<svg", html)
        self.assertIn("claude-svg", html)
        self.assertIn("agy-svg", html)
        self.assertIn("codex-svg", html)
        for greeting in ("Good morning", "Good afternoon", "Good evening", "Hello, night owl"):
            self.assertIn(greeting, html)
            self.assertIn(greeting, app_js)
        self.assertIn("badge-stack", app_js)
        self.assertIn(".badge-stack", style_css)

        # Verify pyproject.toml for MixHub package publishing
        self.assertTrue((repo_root / "pyproject.toml").is_file())
        self.assertIn('name = "mixhub-cli"', (repo_root / "pyproject.toml").read_text(encoding="utf-8"))

        # Verify new HTTP API endpoints over local server
        httpd = server.http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        httpd.hub = self.hub
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{httpd.server_port}"
        try:
            with urllib.request.urlopen(base + "/api/channels/status") as resp:
                st = json.load(resp)
                self.assertTrue(st["ok"])
                self.assertIn("telegram", st["channels"])
            req = urllib.request.Request(
                base + "/api/handoff",
                data=json.dumps({"id": C, "to": "codex"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req) as resp:
                h_res = json.load(resp)
                self.assertTrue(h_res["ok"])
                self.assertEqual(h_res["handoffChain"], ["claude", "codex"])
            with urllib.request.urlopen(base + "/api/handoff/" + C) as resp:
                g_res = json.load(resp)
                self.assertTrue(g_res["ok"])
                self.assertIn("## 1. Goal", g_res["bundle"]["markdown"])
            with urllib.request.urlopen(base + "/api/doctor") as resp:
                doc_res = json.load(resp)
                self.assertTrue(doc_res["ok"])
                self.assertIn("agents", doc_res)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join()

        # Verify HubTUI ephemeral toast 5s auto-expiry, keystroke auto-dismiss, and [3] permanent dismiss
        import tui

        class DummyScreen:
            def getmaxyx(self):
                return (30, 100)

        with patch.object(tui.HubTUI, "_init_colors", lambda self: None):
            ui = tui.HubTUI(DummyScreen(), hub_instance=self.hub, seen_path=str(self.home / "seen_toast.json"))
        toast_msg = tui.format_ephemeral_handoff_toast(84200, 1200, 2.40)
        self.assertTrue(ui.show_ephemeral_toast(toast_msg, ttl=5.0, now=100.0))
        self.assertEqual(ui.active_ephemeral_toast(now=103.0), toast_msg)
        # Auto-expires after 5.0 seconds
        self.assertEqual(ui.active_ephemeral_toast(now=105.1), "")
        # Typing any regular key dismisses immediately
        ui.show_ephemeral_toast(toast_msg, ttl=5.0, now=200.0)
        self.assertEqual(ui.handle_toast_key(ord("j"), now=201.0), "dismissed_on_key")
        self.assertEqual(ui.active_ephemeral_toast(now=201.0), "")
        # Pressing '3' permanently dismisses future toasts
        ui.show_ephemeral_toast(toast_msg, ttl=5.0, now=300.0)
        self.assertEqual(ui.handle_toast_key("3", now=301.0), "dismissed_permanently")
        self.assertFalse(ui.show_ephemeral_toast(toast_msg, ttl=5.0, now=302.0))

        # Verify `◧` collapse icon stays on y=0 in reader_only when zoomed (no 1-row jump to tmux status-left)
        import inspect

        workspace_src = (repo_root / "mixhub" / "tmux" / "workspace.py").read_text(encoding="utf-8")
        self.assertIn('["set-option", "-t", session_name, "status-left", ""]', workspace_src)
        self.assertIn('["set-option", "-s", "exit-unattached", "off"]', workspace_src)
        self.assertIn('["set-option", "-g", "destroy-unattached", "off"]', workspace_src)
        self.assertIn("show_collapse_icon = (not in_tmux) or self._is_reader_zoomed(w)",
                      inspect.getsource(tui.HubTUI._draw_inner))

    def test_handoff_effective_root_auto_inference_from_home_and_tmp(self):
        import handoff

        subproj = self.home / "projects" / "alpha-service"
        (subproj / "src").mkdir(parents=True, exist_ok=True)
        (subproj / ".git").mkdir(parents=True, exist_ok=True)
        (subproj / "CLAUDE.md").write_text("# Alpha Service Rules\n", encoding="utf-8")
        target_py = subproj / "src" / "handler.py"
        target_py.write_text("def handle():\n    return 200\n", encoding="utf-8")

        # Session declared cwd is `~` (self.home), but tool calls operate inside `subproj`
        self.write_rows(
            self.claude_path,
            [
                {
                    "type": "user",
                    "cwd": str(self.home),
                    "message": {"role": "user", "content": "Fix handler.py and run pytest in alpha-service"},
                },
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "tool_use", "name": "Read", "id": "r1", "input": {"file_path": str(target_py), "offset": 1, "limit": 20}},
                            {"type": "tool_use", "name": "Edit", "id": "e1", "input": {"file_path": str(target_py), "old_string": "200", "new_string": "201"}},
                            {"type": "tool_use", "name": "Bash", "id": "b1", "input": {"command": f"cd {subproj} && pytest -q"}},
                        ],
                    },
                },
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [{"type": "tool_result", "tool_use_id": "b1", "content": "Exit code: 0\n1 passed"}],
                    },
                },
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "Updated handler.py and verified tests pass."}],
                    },
                },
            ],
        )
        self.hub.discover(True)
        res = self.hub.handoff({"id": C[:8], "to": "codex", "dry_run": True})
        expected_root = os.path.abspath(str(subproj))
        self.assertTrue(res["ok"])
        self.assertEqual(res["effective_root"], expected_root)
        self.assertEqual(res["cwd"], expected_root)
        self.assertEqual(res["contract"]["effective_root"], expected_root)
        self.assertEqual(res["contract"]["repository"]["cwd"], expected_root)
        self.assertIn(f"cd {expected_root}", res["command"])
        self.assertIn(f"**Effective Root (`effective_root`)**: `{expected_root}`", res["markdown"])

        # Also verify direct infer_effective_root when started from /tmp with AGENTS.md marker
        tmp_proj = self.root / "tmp_workspace" / "nested_pkg"
        (tmp_proj / "pkg").mkdir(parents=True, exist_ok=True)
        (tmp_proj / "AGENTS.md").write_text("# Nested Agents\n", encoding="utf-8")
        inferred = handoff.infer_effective_root(
            "/tmp",
            [str(tmp_proj / "pkg" / "core.py"), str(tmp_proj / "pkg" / "utils.py")],
            home_dir=str(self.home),
        )
        self.assertEqual(inferred, os.path.abspath(str(tmp_proj)))

    def test_handoff_resume_state_4_classifications_and_immediate_action(self):
        import handoff

        def claude_asst(txt):
            return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": txt}]}}

        # 1. user_pending: last turn is a user question with no substantive assistant reply
        self.write_rows(
            self.claude_path,
            [
                claude_user("Initialize relay config"),
                claude_asst("Initialized config."),
                claude_user("Now please add retry backoff to the webhook dispatcher"),
            ],
        )
        self.hub.discover(True)
        r_pending = self.hub.handoff({"id": C[:8], "to": "codex", "dry_run": True})
        self.assertEqual(r_pending["resume_state"], "user_pending")
        self.assertIn("## 0 接续点 (Immediate Action)", r_pending["markdown"])
        self.assertIn("`user_pending`", r_pending["markdown"])
        self.assertIn(handoff.RESUME_STATE_ACTIONS["user_pending"], r_pending["markdown"])

        # 2. mid_task: predecessor interrupted during tool execution without closing assistant text
        self.write_rows(
            self.claude_path,
            [
                claude_user("Run full test suite and patch failing assertion"),
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "tool_use", "name": "Edit", "id": "tu_mid", "input": {"file_path": "/workspace/app.py"}},
                        ],
                    },
                },
            ],
        )
        self.hub.discover(True)
        r_mid = self.hub.handoff({"id": C[:8], "to": "codex", "dry_run": True})
        self.assertEqual(r_mid["resume_state"], "mid_task")
        self.assertIn("`mid_task`", r_mid["markdown"])
        self.assertIn(handoff.RESUME_STATE_ACTIONS["mid_task"], r_mid["markdown"])

        # 3. awaiting_user: assistant proposed options/plan and asked user to confirm
        self.write_rows(
            self.claude_path,
            [
                claude_user("We need rate limiting on the API gateway"),
                claude_asst("I have two options: Option A uses Redis sliding window; Option B uses in-memory token bucket. Which option would you like me to implement?"),
            ],
        )
        self.hub.discover(True)
        r_await = self.hub.handoff({"id": C[:8], "to": "codex", "dry_run": True})
        self.assertEqual(r_await["resume_state"], "awaiting_user")
        self.assertIn("`awaiting_user`", r_await["markdown"])
        self.assertIn(handoff.RESUME_STATE_ACTIONS["awaiting_user"], r_await["markdown"])

        # 4. completed_idle: assistant completed turn cleanly and passed verification
        self.write_rows(
            self.claude_path,
            [
                claude_user("Implement healthcheck endpoint"),
                claude_asst("Implemented `/healthz` endpoint and verified all unit tests pass."),
            ],
        )
        self.hub.discover(True)
        r_idle = self.hub.handoff({"id": C[:8], "to": "codex", "dry_run": True})
        self.assertEqual(r_idle["resume_state"], "completed_idle")
        self.assertIn("`completed_idle`", r_idle["markdown"])
        self.assertIn(handoff.RESUME_STATE_ACTIONS["completed_idle"], r_idle["markdown"])

    def test_handoff_recent_verbatim_turns_folded_tools_pointer_and_budget(self):
        import handoff

        def claude_asst(txt):
            return {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": txt}]}}

        events = []
        for idx in range(1, 7):
            u_text = (
                f"Turn {idx} user specification: please inspect tui.py around line {3400 + idx * 10} "
                f"and verify the bottom anchor behavior. "
                + (f"Detail block {idx}: " + ("verification_context_payload " * 28))
            )
            a_text = (
                f"Turn {idx} assistant summary: completed inspection of tui.py and verified scroll offset "
                f"stability for case {idx}. "
                + (f"Analysis detail {idx}: " + ("scroll_state_verified_ok " * 36))
            )
            events.append(claude_user(u_text))
            events.append(
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Read",
                                "id": f"rd_{idx}",
                                "input": {"file_path": "/workspace/tui.py", "offset": 3450, "limit": 100},
                            },
                            {
                                "type": "tool_use",
                                "name": "Edit",
                                "id": f"ed_{idx}",
                                "input": {
                                    "file_path": "/workspace/tui.py",
                                    "start_line": 3506,
                                    "old_string": "old_val",
                                    "new_string": "new_val",
                                },
                            },
                            {
                                "type": "tool_use",
                                "name": "Bash",
                                "id": f"bs_{idx}",
                                "input": {"command": "pytest tests/test_hub.py"},
                            },
                        ],
                    },
                }
            )
            events.append(
                {
                    "type": "user",
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": f"bs_{idx}",
                                "content": "Exit code: 0\n12 passed in 0.42s",
                            }
                        ],
                    },
                }
            )
            events.append(claude_asst(a_text))

        self.write_rows(self.claude_path, events)
        self.hub.discover(True)
        res = self.hub.handoff({"id": C[:8], "to": "codex", "mode": "delegate", "dry_run": True})
        md = res["markdown"]

        # Section 3 header and recent turns 2..6 preserved verbatim (5 turns) while Turn 1 is pointed to via #L1-L4
        self.assertIn("## 3 近期对话原文 (Recent Verbatim Turns)", md)
        self.assertIn("**Earlier Turns Pointer**:", md)
        self.assertIn("#L1-L4", md)
        self.assertIn("rg -n", md)
        for kept_idx in range(2, 7):
            self.assertIn(f"Turn {kept_idx} user specification:", md)
            self.assertIn(f"Turn {kept_idx} assistant summary:", md)

        # Middle tool calls folded into single-line `[Tools: Read tui.py:3450-3550, Edit tui.py:3506, Bash pytest ... -> exit 0]`
        self.assertIn("[Tools: Read tui.py:3450-3550, Edit tui.py:3506, Bash pytest tests/test_hub.py -> exit 0]", md)

        # Token budget scales for rich multi-turn sessions (well above old 1,450 cap, within <= 10,000 hard cap)
        toks = handoff.estimate_tokens(md)
        self.assertGreater(toks, 1500)
        self.assertLessEqual(toks, 10000)

    def test_handoff_key_files_modified_and_read_grep_line_ranges(self):
        self.write_rows(
            self.claude_path,
            [
                claude_user("Audit handoff.py and tui.py, then update handoff.py"),
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "name": "Read",
                                "id": "r_tui",
                                "input": {"file_path": "/workspace/tui.py", "offset": 3450, "limit": 100},
                            },
                            {
                                "type": "tool_use",
                                "name": "Grep",
                                "id": "g_srv",
                                "input": {"path": "/workspace/server.py", "pattern": "handoffChain"},
                            },
                            {
                                "type": "tool_use",
                                "name": "Edit",
                                "id": "e_ho",
                                "input": {"file_path": "/workspace/handoff.py", "start_line": 210, "end_line": 245},
                            },
                            {"type": "text", "text": "Audited tui.py:3450-3550 and server.py, and patched handoff.py."},
                        ],
                    },
                },
            ],
        )
        self.hub.discover(True)
        res = self.hub.handoff({"id": C[:8], "to": "codex", "dry_run": True})
        md = res["markdown"]
        self.assertIn("## 4 关键文件与锚点", md)
        self.assertIn("**Modified (`Write`/`Edit`)**: `/workspace/handoff.py`", md)
        self.assertIn("**Inspected (`Read`/`Grep`)**: `/workspace/tui.py:3450-3550", md)
        self.assertIn("**Inspected (`Read`/`Grep`)**: `/workspace/server.py", md)
        self.assertTrue(any("/workspace/tui.py:3450-3550" in rf for rf in res["contract"]["repository"]["read_files"]))

    def test_webui_composer_node_unit_tests(self):
        import shutil
        node_bin = shutil.which("node")
        if not node_bin:
            self.skipTest("node is not installed")
        repo_root = Path(__file__).resolve().parents[1]
        res = subprocess.run(
            [node_bin, "--test", str(repo_root / "tests" / "webui_composer.test.mjs")],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(res.returncode, 0, res.stdout + "\n" + res.stderr)

    def test_cross_session_lsof_read_never_hijacks_explicit_or_presence_bound_process(self):
        sid_a = A
        sid_b = "88888888-8888-4888-8888-888888888888"
        sid_c = "99999999-9999-4999-8999-999999999999"
        path_a = self.make_agy()
        db_path = self.home / ".gemini/antigravity-cli/conversation_summaries.db"
        db = sqlite3.connect(db_path)
        db.execute(
            "INSERT INTO conversation_summaries VALUES(?,?,?,?,?,?)",
            (sid_b, "Main newer session", "preview b", json.dumps(["file:///workspace"]), "", 10),
        )
        db.execute(
            "INSERT INTO conversation_summaries VALUES(?,?,?,?,?,?)",
            (sid_c, "Switched session", "preview c", json.dumps(["file:///workspace"]), "", 5),
        )
        db.commit()
        db.close()
        path_b = self.home / (".gemini/antigravity-cli/brain/" + sid_b + "/.system_generated/logs/transcript.jsonl")
        path_c = self.home / (".gemini/antigravity-cli/brain/" + sid_c + "/.system_generated/logs/transcript.jsonl")
        t_now = time.time()
        self.write_rows(
            path_b,
            [{"step_index": 0, "type": "USER_INPUT", "created_at": "2026-10-25T20:30:00Z", "content": "<USER_REQUEST>newer prompt in B</USER_REQUEST>"}],
        )
        self.write_rows(
            path_c,
            [{"step_index": 0, "type": "USER_INPUT", "created_at": "2026-10-25T20:10:00Z", "content": "<USER_REQUEST>prompt in C</USER_REQUEST>"}],
        )
        os.utime(path_a, (t_now - 600, t_now - 600))
        os.utime(path_c, (t_now - 300, t_now - 300))
        os.utime(path_b, (t_now, t_now))

        # Case 1: PID 101 started with `--conversation sid_a` reads `/brain/sid_b/...` (newer updatedAt),
        # while PID 102 holds `/presence/sid_b.lock`. PID 101 must stay bound to sid_a, never hijacked to sid_b.
        proc_a = {**self.process("agy", 101), "tty": "ttys036", "args": ["--conversation", sid_a]}
        proc_b = {**self.process("agy", 102), "tty": "ttys035", "args": []}
        self.hub.processes = lambda: [proc_a, proc_b]
        self.hub.open_files = lambda _: {
            101: {"paths": ["/brain/" + sid_a + "/x", "/brain/" + sid_b + "/notify.txt"], "cwd": "/workspace"},
            102: {"paths": ["/presence/" + sid_b + ".lock", "/brain/" + sid_b + "/transcript.jsonl"], "cwd": "/workspace"},
        }
        self.hub.scanned = 0
        s_a = self.hub.get("agy", sid_a, True)
        s_b = self.hub.get("agy", sid_b)
        self.assertTrue(s_a["isLive"])
        self.assertEqual([p["pid"] for p in s_a["processes"]], [101])
        self.assertEqual([p["pid"] for p in s_b["processes"]], [102])

        # Case 2: Priority 1 (`/presence/<uuid>.lock`) > Priority 2 (CLI `--conversation`) > Priority 3 (unclaimed `/brain/`).
        # PID 201 was started with `--conversation sid_a` but switched in-session to `sid_c` (`/presence/sid_c.lock`),
        # while PID 202 has no CLI args and opened both `/brain/sid_a` and `/brain/sid_c`.
        # Pass 1 binds PID 201 -> sid_c; Pass 2 excludes claimed sid_c and binds PID 202 -> sid_a.
        proc_201 = {**self.process("agy", 201), "tty": "ttys040", "args": ["--conversation", sid_a]}
        proc_202 = {**self.process("agy", 202), "tty": "ttys041", "args": []}
        self.hub.processes = lambda: [proc_201, proc_202]
        self.hub.open_files = lambda _: {
            201: {"paths": ["/presence/" + sid_c + ".lock", "/brain/" + sid_b + "/other.txt"], "cwd": "/workspace"},
            202: {"paths": ["/brain/" + sid_c + "/log.txt", "/brain/" + sid_a + "/log.txt"], "cwd": "/workspace"},
        }
        self.hub.scanned = 0
        s_c2 = self.hub.get("agy", sid_c, True)
        s_a2 = self.hub.get("agy", sid_a)
        self.assertEqual([p["pid"] for p in s_c2["processes"]], [201])
        self.assertEqual([p["pid"] for p in s_a2["processes"]], [202])

        # Case 2b: Two `agy` processes both launched without `--conversation` (`args: []`), where
        # lower-PID `proc_249` belongs to older `sid_a` (`/conversations/sid_a.db`) but watches newer
        # `/brain/sid_b` via kqueue, and higher-PID `proc_485` belongs to newer `sid_b` (`/conversations/sid_b.db-wal`).
        # Pass 1 must treat `/conversations/<uuid>.db*` as authoritative so `proc_249` never steals `sid_b`.
        proc_249 = {**self.process("agy", 249), "tty": "ttys048", "args": []}
        proc_485 = {**self.process("agy", 485), "tty": "ttys049", "args": []}
        self.hub.processes = lambda: [proc_249, proc_485]
        self.hub.open_files = lambda _: {
            249: {"paths": ["/conversations/" + sid_a + ".db", "/brain/" + sid_b, "/brain/" + sid_a], "cwd": "/workspace"},
            485: {"paths": ["/conversations/" + sid_b + ".db-wal", "/brain/" + sid_b, "/brain/" + sid_a], "cwd": "/workspace"},
        }
        self.hub.scanned = 0
        s_a3 = self.hub.get("agy", sid_a, True)
        s_b3 = self.hub.get("agy", sid_b)
        self.assertEqual([p["pid"] for p in s_a3["processes"]], [249])
        self.assertEqual([p["pid"] for p in s_b3["processes"]], [485])

    def test_new_agy_session_dir_is_found_when_the_brain_dir_mtime_does_not_move(self):
        # Linux/WSL timestamps can be coarse enough that a new session dir lands in the same mtime tick as the
        # last scan, and a directory's size doesn't grow for one more entry.
        brain = self.home / ".gemini/antigravity-cli/brain"
        def add(sid):
            t = brain / sid / ".system_generated/logs/transcript.jsonl"
            t.parent.mkdir(parents=True, exist_ok=True)
            t.write_text("{}\n")
        add("00000000-0000-4000-8000-0000000000a1")
        frozen = os.stat(brain)
        real_stat = os.stat

        def same_tick_stat(path, *args, **kwargs):
            st = real_stat(path, *args, **kwargs)
            if os.fspath(path) != str(brain):
                return st

            class SameTick:  # the real stat, with size/mtime/ctime as at the last scan (ext4 dirs stay 4096 bytes)
                def __getattr__(self, name):
                    return getattr(frozen if name in ("st_size", "st_mtime", "st_mtime_ns", "st_ctime", "st_ctime_ns") else st, name)
            return SameTick()

        with patch("mixhub.hub.util.os.stat", side_effect=same_tick_stat):
            self.assertEqual(len(self.hub._discover_agy_transcripts()), 1)
            add("00000000-0000-4000-8000-0000000000a2")
            self.assertEqual(len(self.hub._discover_agy_transcripts()), 2)

    def test_gc_idle_resident_sleep_blockers(self):
        now_ts = time.time()
        old_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ts - 7200))
        recent_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ts - 60))

        sid_aux = "00000000-0000-4000-8000-000000000901"
        sid_child = "00000000-0000-4000-8000-000000000902"
        sid_bg = "00000000-0000-4000-8000-000000000903"
        sid_done = "00000000-0000-4000-8000-000000000904"

        def _write_agy_session(sid: str, rows: list[dict], mtime_ts: float) -> Path:
            tpath = self.home / f".gemini/antigravity-cli/brain/{sid}/.system_generated/logs/transcript.jsonl"
            self.write_rows(tpath, rows)
            os.utime(tpath, (mtime_ts, mtime_ts))
            return tpath

        idle_rows = [
            {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": old_iso, "content": "hi"},
            {"step_index": 1, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": old_iso, "content": "done"},
        ]
        _write_agy_session(sid_aux, idle_rows, now_ts - 7200)
        _write_agy_session(sid_child, idle_rows, now_ts - 7200)

        # (1) Idle session with `chrome-devtools-mcp` (+ Chrome helper child) + `caffeinate` + LSP + `statusline.py`:
        # `sleep_blockers` must be empty and `gc_idle_resident` must hibernate it.
        proc_aux = {
            **self.process("agy", 901),
            "tty": "ttys091",
            "resident": True,
            "args": ["--conversation", sid_aux],
        }
        proc_table_aux = {
            900: {"pid": 900, "ppid": 1, "tty": "ttys091", "args": "python3 /Users/alice/.gemini/session-hub/agy_claude_filter.py agy"},
            901: {"pid": 901, "ppid": 900, "tty": "ttys091", "args": f"/Users/alice/.gemini/antigravity-cli/bin/agy --conversation {sid_aux}"},
            910: {"pid": 910, "ppid": 901, "tty": "ttys091", "args": "node /opt/homebrew/bin/chrome-devtools-mcp --headless"},
            911: {"pid": 911, "ppid": 910, "tty": "??", "args": "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome --user-data-dir=/Users/alice/.cache/agy-chrome"},
            912: {"pid": 912, "ppid": 901, "tty": "ttys091", "args": "caffeinate -i"},
            913: {"pid": 913, "ppid": 901, "tty": "ttys091", "args": "node /opt/homebrew/bin/typescript-language-server --stdio"},
            914: {"pid": 914, "ppid": 901, "tty": "ttys091", "args": "python3 /Users/alice/.claude/statusline.py"},
        }
        self.hub._last_proc_table = proc_table_aux
        self.hub.processes = lambda: [proc_aux]
        self.hub.open_files = lambda _: {}
        self.hub.scanned = 0
        s_aux = self.hub.get("agy", sid_aux, True)
        self.assertEqual(self.hub.sleep_blockers(s_aux, proc_table=proc_table_aux), [])
        gc_res_1 = self.hub.gc_idle_resident(max_idle_sec=3600, dry_run=True)
        self.assertEqual([r["id"] for r in gc_res_1], [sid_aux])

        # (2) Idle session with `python3 train.py`, `bash -c "sleep 300"`, or `ssh devbox` child/same-TTY process:
        # `sleep_blockers` must be non-empty and `gc_idle_resident` must refuse to hibernate.
        proc_child = {
            **self.process("agy", 902),
            "tty": "ttys092",
            "resident": True,
            "args": ["--conversation", sid_child],
        }
        for user_cmd, user_ppid in (
            ("python3 train.py --epochs 10", 902),
            ('bash -c "sleep 300"', 902),
            ("ssh devbox", 1),
        ):
            proc_table_child = {
                902: {"pid": 902, "ppid": 1, "tty": "ttys092", "args": f"agy --conversation {sid_child}"},
                920: {"pid": 920, "ppid": user_ppid, "tty": "ttys092", "args": user_cmd},
            }
            self.hub._last_proc_table = proc_table_child
            self.hub.processes = lambda: [proc_child]
            self.hub.scanned = 0
            s_child = self.hub.get("agy", sid_child, True)
            blk_child = self.hub.sleep_blockers(s_child, proc_table=proc_table_child)
            self.assertTrue(any(b.startswith("child_proc:920:") for b in blk_child), f"Expected child_proc blocker for {user_cmd}, got {blk_child}")
            self.assertEqual(self.hub.gc_idle_resident(max_idle_sec=3600, dry_run=True), [])

        # (3) `agy` session with unfinished background `run_command` task, unexpired `schedule` `DurationSeconds` timer,
        # or active `schedule` `CronExpression`: `sleep_blockers` must block and `gc_idle_resident` must refuse.
        proc_bg = {
            **self.process("agy", 903),
            "tty": "ttys093",
            "resident": True,
            "args": ["--conversation", sid_bg],
        }
        proc_table_clean_bg = {
            903: {"pid": 903, "ppid": 1, "tty": "ttys093", "args": f"agy --conversation {sid_bg}"},
        }
        self.hub._last_proc_table = proc_table_clean_bg
        self.hub.processes = lambda: [proc_bg]

        # 3a: Unfinished background command (`Tool is running as a background task with task id: ...`)
        _write_agy_session(
            sid_bg,
            [
                {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": old_iso, "content": "run job"},
                {
                    "step_index": 1,
                    "type": "PLANNER_RESPONSE",
                    "status": "DONE",
                    "created_at": old_iso,
                    "tool_calls": [{"id": "tc-1", "name": "run_command", "args": {"CommandLine": "python3 long_job.py"}}],
                },
                {
                    "step_index": 2,
                    "type": "GENERIC",
                    "status": "DONE",
                    "created_at": old_iso,
                    "content": "Tool is running as a background task with task id: 74a78e4e/task-2\nTask Description: long job",
                },
                {"step_index": 3, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": old_iso, "content": "Started in background."},
            ],
            now_ts - 7200,
        )
        self.hub.scanned = 0
        s_bg_cmd = self.hub.get("agy", sid_bg, True)
        blk_bg_cmd = self.hub.sleep_blockers(s_bg_cmd, proc_table=proc_table_clean_bg)
        self.assertIn("active_bg_task:74a78e4e/task-2", blk_bg_cmd)
        self.assertEqual(self.hub.gc_idle_resident(max_idle_sec=3600, dry_run=True), [])

        # 3b: Unexpired `schedule` `DurationSeconds` timer (`now < created_at + DurationSeconds + 60`)
        _write_agy_session(
            sid_bg,
            [
                {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": old_iso, "content": "wake in 3h"},
                {
                    "step_index": 1,
                    "type": "PLANNER_RESPONSE",
                    "status": "DONE",
                    "created_at": recent_iso,
                    "tool_calls": [{"id": "tc-sched-1", "name": "schedule", "args": {"DurationSeconds": 10800, "Prompt": "check status"}}],
                },
                {
                    "step_index": 2,
                    "type": "GENERIC",
                    "status": "DONE",
                    "created_at": recent_iso,
                    "content": f"Created At: {recent_iso}\nTool is running as a background task with task id: 74a78e4e/task-2\nTask Description: Timer: 3h, Prompt: check status",
                },
                {"step_index": 3, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": recent_iso, "content": "Timer set."},
            ],
            now_ts - 7200,
        )
        self.hub.scanned = 0
        s_bg_timer = self.hub.get("agy", sid_bg, True)
        blk_bg_timer = self.hub.sleep_blockers(s_bg_timer, proc_table=proc_table_clean_bg)
        self.assertIn("active_schedule_timer", blk_bg_timer)
        self.assertEqual(self.hub.gc_idle_resident(max_idle_sec=3600, dry_run=True), [])

        # 3c: Active `schedule` `CronExpression`
        _write_agy_session(
            sid_bg,
            [
                {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": old_iso, "content": "cron monitor"},
                {
                    "step_index": 1,
                    "type": "PLANNER_RESPONSE",
                    "status": "DONE",
                    "created_at": old_iso,
                    "tool_calls": [{"id": "tc-cron-1", "name": "schedule", "args": {"CronExpression": "*/15 * * * *", "Prompt": "poll"}}],
                },
                {
                    "step_index": 2,
                    "type": "GENERIC",
                    "status": "DONE",
                    "created_at": old_iso,
                    "content": f"Created At: {old_iso}\nTool is running as a background task with task id: 74a78e4e/task-2\nTask Description: Cron: */15 * * * *, Prompt: poll",
                },
                {"step_index": 3, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": old_iso, "content": "Cron scheduled."},
            ],
            now_ts - 7200,
        )
        self.hub.scanned = 0
        s_bg_cron = self.hub.get("agy", sid_bg, True)
        blk_bg_cron = self.hub.sleep_blockers(s_bg_cron, proc_table=proc_table_clean_bg)
        self.assertIn("active_schedule_cron", blk_bg_cron)
        self.assertEqual(self.hub.gc_idle_resident(max_idle_sec=3600, dry_run=True), [])

        # (4) `agy` session whose `schedule` timer has expired (`now >= created_at + DurationSeconds + 60`)
        # or finished (`Task id ... finished`) or whose `schedule` cron was killed (`manage_task` `Action="kill"`):
        # `sleep_blockers` must be empty and `gc_idle_resident` must hibernate it.
        proc_done = {
            **self.process("agy", 904),
            "tty": "ttys094",
            "resident": True,
            "args": ["--conversation", sid_done],
        }
        proc_table_clean_done = {
            904: {"pid": 904, "ppid": 1, "tty": "ttys094", "args": f"agy --conversation {sid_done}"},
        }
        self.hub._last_proc_table = proc_table_clean_done
        self.hub.processes = lambda: [proc_done]

        # 4a: Expired `schedule` timer (created 2h ago with `DurationSeconds: 60`), plus finished bg task, plus killed cron
        _write_agy_session(
            sid_done,
            [
                {"step_index": 0, "type": "USER_INPUT", "status": "DONE", "created_at": old_iso, "content": "run and timer"},
                {
                    "step_index": 1,
                    "type": "PLANNER_RESPONSE",
                    "status": "DONE",
                    "created_at": old_iso,
                    "tool_calls": [{"id": "tc-bg-done", "name": "run_command", "args": {"CommandLine": "echo hi"}}],
                },
                {
                    "step_index": 2,
                    "type": "GENERIC",
                    "status": "DONE",
                    "created_at": old_iso,
                    "content": "Tool is running as a background task with task id: 74a78e4e/task-2",
                },
                {
                    "step_index": 3,
                    "type": "GENERIC",
                    "status": "DONE",
                    "created_at": old_iso,
                    "content": 'Task id "74a78e4e/task-2" finished with status: DONE',
                },
                {
                    "step_index": 4,
                    "type": "PLANNER_RESPONSE",
                    "status": "DONE",
                    "created_at": old_iso,
                    "tool_calls": [{"id": "tc-cron-kill", "name": "schedule", "args": {"CronExpression": "*/5 * * * *", "Prompt": "ping"}}],
                },
                {
                    "step_index": 5,
                    "type": "GENERIC",
                    "status": "DONE",
                    "created_at": old_iso,
                    "content": "Tool is running as a background task with task id: 74a78e4e/task-5\nTask Description: Cron: */5 * * * *",
                },
                {
                    "step_index": 6,
                    "type": "PLANNER_RESPONSE",
                    "status": "DONE",
                    "created_at": old_iso,
                    "tool_calls": [{"id": "tc-kill", "name": "manage_task", "args": {"Action": "kill", "TaskId": "74a78e4e/task-5"}}],
                },
                {
                    "step_index": 7,
                    "type": "PLANNER_RESPONSE",
                    "status": "DONE",
                    "created_at": old_iso,
                    "tool_calls": [{"id": "tc-exp", "name": "schedule", "args": {"DurationSeconds": 60, "Prompt": "short wait"}}],
                },
                {
                    "step_index": 8,
                    "type": "GENERIC",
                    "status": "DONE",
                    "created_at": old_iso,
                    "content": f"Created At: {old_iso}\nTool is running as a background task with task id: 74a78e4e/task-8\nTask Description: Timer: 1m, Prompt: short wait",
                },
                {"step_index": 9, "type": "PLANNER_RESPONSE", "status": "DONE", "created_at": old_iso, "content": "All done."},
            ],
            now_ts - 7200,
        )
        self.hub.scanned = 0
        s_done = self.hub.get("agy", sid_done, True)
        self.assertEqual(self.hub.sleep_blockers(s_done, proc_table=proc_table_clean_done), [])
        gc_res_4 = self.hub.gc_idle_resident(max_idle_sec=3600, dry_run=True)
        self.assertEqual([r["id"] for r in gc_res_4], [sid_done])

        # (5) G5 Fail-closed: if `proc_table` cannot be loaded (`_last_proc_table = None`), `gc_idle_resident` skips hibernating.
        self.hub._last_proc_table = None
        self.assertEqual(self.hub.gc_idle_resident(max_idle_sec=3600, dry_run=True), [])

    def test_prompt_input_utf8_and_wch(self):
        import tui
        from unittest.mock import MagicMock, patch

        class FakeStdscr:
            def __init__(self, getch_seq=None, wch_seq=None):
                self._getch_seq = list(getch_seq or [])
                self._wch_seq = list(wch_seq or [])
                self.getstr = None
                if wch_seq is not None:
                    self.get_wch = self._pop_wch

            def getmaxyx(self):
                return (40, 32)

            def nodelay(self, *_a):
                pass

            def timeout(self, *_a):
                pass

            def move(self, *_a):
                pass

            def clrtoeol(self):
                pass

            def addstr(self, *_a):
                pass

            def refresh(self):
                pass

            def getch(self):
                return self._getch_seq.pop(0) if self._getch_seq else 10

            def _pop_wch(self):
                return self._wch_seq.pop(0) if self._wch_seq else "\n"

        with patch("tui.curses.color_pair", return_value=0), patch("tui.curses.noecho"), patch("tui.curses.curs_set"):
            app = tui.HubTUI.__new__(tui.HubTUI)
            app.reader_only = False
            app.sidebar_only = True
            app.header_only = False
            app._dirty = False

            # 1. Raw UTF-8 byte stream via getch() -> reconstructs Chinese string cleanly
            app.stdscr = FakeStdscr(getch_seq=list("邮件分类与日程提醒".encode("utf-8")) + [10])
            self.assertEqual(app.prompt_input("Rename: ", ""), "邮件分类与日程提醒")

            # 2. Wide-char get_wch() returning decoded str including Latin 'é' (U+00E9) + KEY_BACKSPACE
            app.stdscr = FakeStdscr(wch_seq=["c", "a", "f", "é", "X", tui.curses.KEY_BACKSPACE, "\n"])
            self.assertEqual(app.prompt_input("Rename: ", ""), "café")

    def test_cursor_flashing_and_incremental_repaint_guards(self):
        import agy_claude_filter
        import tui
        from unittest.mock import MagicMock, patch

        # 1. HubTUI._present() uses ncurses virtual-screen diffing on normal frames (zero redrawwin/touchwin)
        scr = MagicMock()
        scr.getmaxyx.return_value = (40, 32)
        with patch("tui.curses.color_pair", return_value=0), \
             patch.object(tui.HubTUI, "_init_colors"), \
             patch("tui.os.get_terminal_size", return_value=os.terminal_size((32, 40))):
            app = tui.HubTUI(
                scr,
                sidebar_only=True,
                hub_instance=self.hub,
                caf=MagicMock(),
                seen_path=str(self.home / "seen_repaint.json"),
            )
            scr.redrawwin.reset_mock()
            scr.touchwin.reset_mock()
            scr.clearok.reset_mock()
            scr.refresh.reset_mock()
            app.draw()
            self.assertEqual(scr.redrawwin.call_count, 0)
            self.assertEqual(scr.touchwin.call_count, 0)
            self.assertEqual(scr.clearok.call_count, 0)
            self.assertEqual(scr.refresh.call_count, 1)

            # Explicit _force_full_repaint triggers clearok(True) once and resets the flag
            app._force_full_repaint = True
            app.draw()
            scr.clearok.assert_called_once_with(True)
            self.assertFalse(app._force_full_repaint)

            # 2. Mouse motion in header_only / sidebar_only sets _last_mouse_was_click=False; click sets True
            app._handle_mouse(parsed_event=(35, 5, 0, False))
            self.assertFalse(app._last_mouse_was_click)
            app._handle_mouse(parsed_event=(0, 5, 0, False))
            self.assertTrue(app._last_mouse_was_click)

        # 3. _sync_outer_window_title sets pane titles with -T without appending a focus-stealing `select-pane -t right`
        import mixhub.tmux.outer

        mixhub.tmux.outer._LAST_WINDOW_TITLE = None
        tui._OUTER_PANE_SNAPSHOT["hub"] = (time.monotonic(), False, "%2", "%1")
        with patch.dict(os.environ, {"TMUX": "/private/tmp/tmux-501/default,999,0"}), \
             patch("tui.subprocess.Popen") as mock_popen:
            tui._sync_outer_window_title({"id": "s123", "tool": "agy", "title": "Test Title"}, "hub")
            self.assertEqual(mock_popen.call_count, 1)
            cmd = mock_popen.call_args.args[0]
            self.assertEqual(cmd.count("-T"), 2)
            self.assertEqual(cmd.count("select-pane"), 2)

        # 4. agy_claude_filter CursorVisibilityGuard: pre-frame ?25l + 3ms pause on first redraw,
        #    0ms pause & held ?25h across 80ms spinner frames, delayed ?25h flush after 120ms idle
        guard = agy_claude_filter.CursorVisibilityGuard(in_tmux=True, pre_hide_delay=0.003, idle_show_delay=0.120)
        frame1 = b"\x1b[?25l" + (b"\x1b[12;1H" + b"x" * 80) + b"\x1b[?25h"
        pre1, body1, sleep1 = guard.prepare_write(frame1, 10.0)
        self.assertEqual(pre1, b"\x1b[?25l")
        self.assertEqual(sleep1, 0.003)
        self.assertNotIn(b"\x1b[?25h", body1)
        self.assertTrue(guard.cursor_hidden_on_tty)
        self.assertTrue(guard.pending_cursor_show)

        # Next spinner frame 80ms later: cursor is already hidden -> no pre-write, 0.0s sleep, ?25h still held
        self.assertEqual(guard.flush_due(10.08), b"")
        pre2, body2, sleep2 = guard.prepare_write(frame1, 10.08)
        self.assertEqual(pre2, b"")
        self.assertEqual(sleep2, 0.0)
        self.assertNotIn(b"\x1b[?25h", body2)

        # Once idle for >= 120ms after the last spinner frame, ?25h is flushed once
        self.assertEqual(guard.flush_due(10.21), b"\x1b[?25h")
        self.assertFalse(guard.cursor_hidden_on_tty)
        self.assertFalse(guard.pending_cursor_show)

        # 5. coalesce_pty_output joins split PTY chunks until ?25h closes the frame
        chunks = [b"\x1b[?25l\x1b[10;1Hpart1", b"-part2\x1b[?25h"]
        select_calls = [[99], []]
        merged = agy_claude_filter.coalesce_pty_output(
            99,
            chunks.pop(0),
            select_fn=lambda *_a: (select_calls.pop(0) if select_calls else [], [], []),
            read_fn=lambda _fd, _n: chunks.pop(0) if chunks else b"",
            mono_fn=lambda: 1.0,
        )
        self.assertEqual(merged, b"\x1b[?25l\x1b[10;1Hpart1-part2\x1b[?25h")

        # 6. VScreen._decset only triggers on_mode when state changes, and MouseDebugLog.from_env defaults off
        mode_events = []
        vs = agy_claude_filter.VScreen(24, 80)
        vs.on_mode = lambda p, on, alt: mode_events.append((p, on, alt))
        vs.feed(b"\x1b[?2004h\x1b[?2004h\x1b[?2004h")
        self.assertEqual(mode_events, [(2004, True, False)])
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGY_MOUSE_DEBUG", None)
            os.environ.pop("HUB_MOUSE_DEBUG", None)
            self.assertIsNone(agy_claude_filter.MouseDebugLog.from_env())

    def test_terminal_dock_bell_and_perf_optimizations(self):
        import tui
        # 1. emit_outer_terminal_bell writes \x07 * count to outer client TTYs
        written_payloads = []
        with patch("mixhub.tmux.outer._list_outer_client_ttys", return_value=["/dev/ttys033"]), \
             patch("tui.os.open", return_value=77), \
             patch("tui.os.write", side_effect=lambda fd, data: written_payloads.append((fd, data)) or len(data)), \
             patch("tui.os.close"):
            self.assertEqual(tui.emit_outer_terminal_bell(1, "hub"), 1)
            self.assertEqual(tui.emit_outer_terminal_bell(2, "hub"), 1)
        self.assertEqual(written_payloads, [(77, b"\x07"), (77, b"\x07\x07")])

        # 2. format_outer_window_title preserves default title at attention_count=0 and prefixes count when >0
        s_demo = {"id": "12345678-1234", "tool": "agy", "title": "Fix Terminal Bell"}
        self.assertEqual(tui.format_outer_window_title(s_demo), "mixhub · Fix Terminal Bell (Gemini)")
        self.assertEqual(tui.format_outer_window_title(s_demo, attention_count=2), "● 2 · mixhub · Fix Terminal Bell (Gemini)")
        self.assertEqual(tui.format_outer_window_title(None, attention_count=1), "● 1 · mixhub")

        # 3. Single and multi-session completion bell + unfocused selected session unread handling
        class DummyScr:
            def getmaxyx(self):
                return (30, 32)
            def nodelay(self, *_a):
                pass
            def keypad(self, *_a):
                pass

        seen_file = os.path.join(self.root, "data", "seen_bell_test.json")
        with patch.object(tui.HubTUI, "_init_colors", lambda self: None):
            app = tui.HubTUI(DummyScr(), hub_instance=self.hub, seen_path=seen_file, sidebar_only=True)
            now = time.time()
            s1 = {
                "id": "sess-1", "tool": "agy", "title": "One", "isLive": True,
                "agentState": "done_idle", "updatedAt": now + 10.0, "attentionAt": now + 10.0,
                "hasAssistantReply": True, "tailStatus": "done_idle",
            }
            s2 = {
                "id": "sess-2", "tool": "claude", "title": "Two", "isLive": True,
                "agentState": "wait_input", "updatedAt": now + 11.0, "attentionAt": now + 11.0,
                "hasAssistantReply": True, "tailStatus": "wait_input",
            }
            app.sessions = [dict(s1), dict(s2)]
            app.selected_idx = 0  # s1 is selected (keep_id = "sess-1")
            app._prev_agent_states = {
                "agy:sess-1": ("working", now),
                "claude:sess-2": ("working", now),
            }
            app._seen_state = {"bootstrapAt": now - 50.0, "seen": {}, "attn": {}}
            bell_calls = []
            # When outer client is UNFOCUSED, both s1 (selected) and s2 (background) mark attn and ring 2 bells
            with patch.object(self.hub, "listing", return_value={"sessions": [dict(s1), dict(s2)], "counts": {}}), \
                 patch("mixhub.tmux.new_sessions._collect_live_new_hubpool_sessions", return_value=[]), \
                 patch.object(app, "_is_outer_client_focused", return_value=False), \
                 patch.object(app, "_emit_completion_bell", side_effect=lambda n: bell_calls.append(n) or n):
                app.refresh_data(fast_only=True)
                self.assertEqual(bell_calls, [2])
                self.assertIn("agy:sess-1", app._seen_state["attn"])
                self.assertIn("claude:sess-2", app._seen_state["attn"])
                # Repeating refresh_data with the same turn timestamps does NOT re-ring bells
                app.refresh_data(fast_only=True)
                self.assertEqual(bell_calls, [2])

            # When user returns focus to Terminal.app (_is_outer_client_focused -> True),
            # the selected done_idle session (s1) is automatically marked seen while background s2 stays in attn
            with patch.object(self.hub, "listing", return_value={"sessions": [dict(s1), dict(s2)], "counts": {}}), \
                 patch("mixhub.tmux.new_sessions._collect_live_new_hubpool_sessions", return_value=[]), \
                 patch.object(app, "_is_outer_client_focused", return_value=True), \
                 patch.object(app, "_emit_completion_bell", side_effect=lambda n: bell_calls.append(n) or n):
                app.refresh_data(fast_only=True)
                self.assertNotIn("agy:sess-1", app._seen_state["attn"])
                self.assertIn("claude:sess-2", app._seen_state["attn"])

        # 4. Incremental per-PID open_files cache skips lsof for unchanged PIDs with cached cwd/paths
        hub_inc = server.Hub(home=str(self.home), root=str(self.root))
        p_unchanged = {
            "pid": 4242, "ppid": 1, "tool": "claude", "tty": "ttys001", "rss_mb": 50.0,
            "etime": "01:00", "start_time": "Mon Oct 27 00:00:00 2026", "args": ["claude", "--resume", C],
        }
        hub_inc._files_cache = {4242: {"cwd": str(self.home), "paths": []}}
        hub_inc._pid_lsof_sig = {4242: (p_unchanged["start_time"], tuple(p_unchanged["args"]))}
        hub_inc._last_lsof_proc_key = ((4242, p_unchanged["start_time"], tuple(p_unchanged["args"])),)
        hub_inc._proc_scanned = 0
        with patch.object(server.Hub, "processes", return_value=[p_unchanged]), \
             patch.object(server.Hub, "open_files", side_effect=AssertionError("lsof must not be called for cached PID")):
            hub_inc.discover(force=True)

    def test_shutdown_signal_exit_preserves_active_working_set_and_fast_dual_pane_startup(self):
        """
        Verify:
          1. `_build_hubpool_cli_wrapper_cmd` traps HUP (129) and TERM (143) so OS shutdown / tmux teardown
             forwards a signal exit code to `--on-exit` even if the child CLI catches SIGHUP/SIGTERM and exits 0.
          2. `handle_cli_on_exit` with signal exit codes (rc=129 / rc=143 / rc=-15) keeps the session in
             `active_working_set.json` (Active) and clears `last_exit`, whereas normal `/exit` (rc=0) removes it
             from `active_working_set.json` and records `last_exit/<sid>.json` to demote to Recent.
          3. `server.Hub.discover()` keeps `inWorkingSet=True` when `last_exit/<sid>.json` has `rc >= 128`
             (or `signal_exit=True`) and demotes `inWorkingSet=False` when `rc == 0`.
          4. Isolated `HubTUI` (`self.hub.root != tui.ROOT`) writes `active_selection.json` under `self.hub.root/data`
             and never modifies live `tui.ACTIVE_SEL_PATH`.
          5. `HubTUI(..., reader_only=True)` adopts `active_selection.json` via `get_lightweight_session_meta`
             without triggering a cold `refresh_data(force=True)` scan.
          6. `bin/hub` `compute_window_title` reads `data/attention.json` without calling `server.Hub.listing`,
             and `apply_window_title_and_hooks` restores right-pane focus (`%1`) after updating sidebar title (`%2`).
        """
        import importlib.machinery
        import importlib.util
        import tui

        # 1. Wrapper command traps HUP and TERM
        wrap_cmd = tui._build_hubpool_cli_wrapper_cmd("claude", C, f"w-claude-{C[:12]}", "/tmp", "claude")
        self.assertIn("sig_rc=129", wrap_cmd)
        self.assertIn("sig_rc=143", wrap_cmd)
        self.assertIn("--on-exit", wrap_cmd)

        # 2. Signal exit vs normal /exit in handle_cli_on_exit
        wset_file = Path(self.root) / "active_working_set.json"
        last_exit_dir = Path(self.root) / "data" / "last_exit"
        last_exit_dir.mkdir(parents=True, exist_ok=True)
        last_exit_file = last_exit_dir / f"{C}.json"
        last_exit_file.write_text(json.dumps({"ts": time.time(), "rc": 0}), encoding="utf-8")

        with patch("mixhub.tui.paths.ROOT", str(self.root)):
            rc_sig = tui.handle_cli_on_exit("claude", C, f"w-claude-{C[:12]}", 143, str(wset_file))
            self.assertEqual(rc_sig, 0)
            wset_data = json.loads(wset_file.read_text(encoding="utf-8"))
            self.assertIn(f"claude:{C}", wset_data["sessions"])
            self.assertFalse(last_exit_file.exists(), "Signal exit must clear any stale last_exit marker")

            with patch("tui.subprocess.run"), patch("tui.subprocess.Popen"), patch("mixhub.tui.selection.write_active_selection"):
                rc_norm = tui.handle_cli_on_exit("claude", C, f"w-claude-{C[:12]}", 0, str(wset_file))
            self.assertEqual(rc_norm, 0)
            wset_data_after = json.loads(wset_file.read_text(encoding="utf-8"))
            self.assertNotIn(f"claude:{C}", wset_data_after["sessions"])
            self.assertTrue(last_exit_file.exists(), "Normal /exit must write last_exit marker")
            self.assertEqual(json.loads(last_exit_file.read_text(encoding="utf-8"))["rc"], 0)

        # 3. server.Hub.discover() respects signal_exit / rc >= 128 vs rc == 0
        self.hub._save_working_set({f"claude:{C}"})
        last_exit_file.write_text(
            json.dumps({"ts": time.time() + 3600.0, "rc": 129, "signal_exit": True}),
            encoding="utf-8",
        )
        self.hub.scanned = 0
        sessions_sig = self.hub.discover(force=True)
        self.assertTrue(sessions_sig[f"claude:{C}"]["inWorkingSet"], "Signal exit (rc=129) must preserve Active working set")

        last_exit_file.write_text(
            json.dumps({"ts": time.time() + 3600.0, "rc": 0, "signal_exit": False}),
            encoding="utf-8",
        )
        self.hub.scanned = 0
        sessions_norm = self.hub.discover(force=True)
        self.assertFalse(sessions_norm[f"claude:{C}"]["inWorkingSet"], "Normal exit (rc=0) must demote from Active to Recent")
        last_exit_file.unlink(missing_ok=True)
        self.hub._save_working_set({f"claude:{C}"})
        self.hub.scanned = 0

        # 4 & 5. Isolated state + fast reader_only startup via get_lightweight_session_meta
        class DummyScr:
            def getmaxyx(self):
                return (30, 80)
            def nodelay(self, *_a):
                pass
            def keypad(self, *_a):
                pass

        global_sel_before = Path(tui.ACTIVE_SEL_PATH).read_bytes() if Path(tui.ACTIVE_SEL_PATH).exists() else None
        with patch.object(tui.HubTUI, "_init_colors", lambda self: None):
            sb = tui.HubTUI(DummyScr(), hub_instance=self.hub, sidebar_only=True)
            iso_sel_path = Path(self.root) / "data" / "active_selection.json"
            self.assertEqual(Path(sb._active_selection_path()), iso_sel_path)
            self.assertTrue(iso_sel_path.exists())
            global_sel_after = Path(tui.ACTIVE_SEL_PATH).read_bytes() if Path(tui.ACTIVE_SEL_PATH).exists() else None
            self.assertEqual(global_sel_before, global_sel_after, "Isolated HubTUI must never overwrite live global active_selection.json")

            # Select session C in sidebar, then initialize reader_only and verify refresh_data is NOT called
            sb._write_active_selection({"id": C, "tool": "claude", "title": "Reboot Persistence", "cwd": str(self.home)})
            with patch.object(tui.HubTUI, "refresh_data", side_effect=AssertionError("reader_only __init__ must not run cold refresh_data")):
                rd = tui.HubTUI(DummyScr(), hub_instance=self.hub, reader_only=True)
            self.assertEqual(rd.selected_idx, 0)
            self.assertEqual(rd.sessions[0]["id"], C)

        # 6. bin/hub compute_window_title reads data/attention.json and apply_window_title_and_hooks preserves right-pane focus
        import mixhub.cli.workspace as bin_hub  # `bin/hub title` lives in mixhub.cli.workspace

        attn_file = Path(self.root) / "data" / "attention.json"
        attn_file.write_text(json.dumps({"keys": ["claude:1", "agy:2"], "updatedAt": time.time()}), encoding="utf-8")
        iso_sel_path.write_text(json.dumps({"id": C, "tool": "claude", "title": "Fast Startup"}), encoding="utf-8")

        tmux_calls = []

        def fake_tmux_run(cmd, *a, **kw):
            tmux_calls.append(cmd)
            if "list-windows" in cmd:
                return MagicMock(returncode=0, stdout=f"w-claude-{C[:12]}\n")
            if "list-panes" in cmd:
                return MagicMock(returncode=0, stdout="%2|0|0\n%1|33|0\n")
            if "display-message" in cmd:
                return MagicMock(returncode=0, stdout="%1\n")
            return MagicMock(returncode=0, stdout="")

        with patch.object(bin_hub, "STATE_ROOT", str(self.root)), \
             patch.object(bin_hub.subprocess, "run", side_effect=fake_tmux_run), \
             patch.object(server.Hub, "listing", side_effect=AssertionError("compute_window_title must not call Hub.listing")):
            title = bin_hub.apply_window_title_and_hooks("hub")
        self.assertIn("●2 mixhub · Fast Startup", title)
        select_pane_cmds = [c for c in tmux_calls if "select-pane" in c]
        self.assertEqual(select_pane_cmds[-1], ["tmux", "-L", "mixhub", "select-pane", "-t", "%1"])


class ClaudeQuotaMergeTest(unittest.TestCase):
    NOW = 1_800_000_000.0

    def metrics(self, kind, *, cfg_rem=6, bucket_rem=4, cfg_seen=None,
                bucket_seen=None, cfg_reset=None, bucket_reset=None, fetched=True):
        import tui

        cfg_seen = self.NOW - 60 if cfg_seen is None else cfg_seen
        bucket_seen = self.NOW - 120 if bucket_seen is None else bucket_seen
        util_key, bucket_key = ("seven_day", "claude-weekly") if kind == "wk" else ("five_hour", "claude-5h")
        with tempfile.TemporaryDirectory() as home:
            cfg = Path(home) / ".claude.json"
            cached = {"utilization": {util_key: {"utilization": 100 - cfg_rem, "resets_at": cfg_reset}}}
            if fetched:
                cached["fetchedAtMs"] = cfg_seen * 1000
            cfg.write_text(json.dumps({"cachedUsageUtilization": cached}))
            os.utime(cfg, (cfg_seen, cfg_seen))
            statusline = Path(home) / "statusline.json"
            statusline.write_text(json.dumps({"quota": {bucket_key: {
                "remaining_fraction": bucket_rem / 100,
                "reset_time": bucket_reset,
                "observed_at": bucket_seen,
            }}}))
            with patch("tui.time.time", return_value=self.NOW), \
                 patch.dict(os.environ, {"MIXHUB_QUOTA_TTL_SEC": ""}), \
                 patch.dict(tui._QUOTA_SUMMARY_CACHE, {}, clear=True):
                items = tui.get_cli_quota_metrics(statusline_path=str(statusline), home_dir=home)
            return next((item for item in items if item["key"] == "cc"), {})

    def test_weekly_48h_observation_wins_over_newer_94_percent_cache(self):
        import tui

        reset = self.NOW + 2 * 86400
        for fetched in (False, True):
            with self.subTest(fetched=fetched):
                item = self.metrics("wk", cfg_reset=reset, bucket_reset=reset,
                                    bucket_seen=self.NOW - 48 * 3600, fetched=fetched)
                self.assertEqual(item.get("rem_wk"), 4)
                self.assertEqual(item["state_wk"], "fresh")
                self.assertTrue(item["started_wk"])
                self.assertEqual(item["reset_wk"], tui.format_quota_reset_tag(reset, "wk"))

    def test_same_window_keeps_lower_remaining_in_either_source(self):
        import tui

        for kind in ("5h", "wk"):
            for cfg_rem, bucket_rem in ((6, 4), (4, 6), (4, 4)):
                for jitter in (-599, 0, 599):
                    with self.subTest(kind=kind, cfg_rem=cfg_rem, jitter=jitter):
                        reset = self.NOW + 3600
                        item = self.metrics(kind, cfg_rem=cfg_rem, bucket_rem=bucket_rem,
                                            cfg_reset=reset, bucket_reset=reset + jitter)
                        self.assertEqual(item.get(f"rem_{kind}"), 4)
                        self.assertEqual(item[f"state_{kind}"], "fresh")
                        winner_reset = reset + jitter if bucket_rem < cfg_rem else reset
                        self.assertEqual(item[f"reset_{kind}"], tui.format_quota_reset_tag(winner_reset, kind))

    def test_different_windows_choose_newer_reset_before_observation(self):
        for kind in ("5h", "wk"):
            for newer_bucket in (False, True):
                with self.subTest(kind=kind, newer_bucket=newer_bucket):
                    reset = self.NOW + 3600
                    item = self.metrics(
                        kind, cfg_rem=4 if newer_bucket else 80, bucket_rem=80 if newer_bucket else 4,
                        cfg_reset=reset if newer_bucket else reset + 600,
                        bucket_reset=reset + 600 if newer_bucket else reset,
                        cfg_seen=self.NOW - (60 if newer_bucket else 120),
                        bucket_seen=self.NOW - (120 if newer_bucket else 60),
                    )
                    self.assertEqual(item.get(f"rem_{kind}"), 80)

    def test_newer_bucket_cannot_raise_remaining_in_same_window(self):
        for kind in ("5h", "wk"):
            with self.subTest(kind=kind):
                item = self.metrics(kind, cfg_rem=4, bucket_rem=6,
                                    cfg_reset=self.NOW + 3600, bucket_reset=self.NOW + 3600,
                                    cfg_seen=self.NOW - 120, bucket_seen=self.NOW - 60)
                self.assertEqual(item.get(f"rem_{kind}"), 4)

    def test_missing_resets_use_newer_observation(self):
        for kind in ("5h", "wk"):
            for newer_bucket in (False, True):
                with self.subTest(kind=kind, newer_bucket=newer_bucket):
                    item = self.metrics(kind, cfg_seen=self.NOW - (120 if newer_bucket else 60),
                                        bucket_seen=self.NOW - (60 if newer_bucket else 120))
                    self.assertEqual(item.get(f"rem_{kind}"), 4 if newer_bucket else 6)

    def test_weekly_authority_does_not_revive_invalid_observations(self):
        for reset, seen in (
            (None, self.NOW - 48 * 3600),
            (self.NOW, self.NOW - 48 * 3600),
            (self.NOW + 2 * 86400, self.NOW - 6 * 86400),
        ):
            with self.subTest(reset=reset, seen=seen):
                item = self.metrics("wk", cfg_reset=self.NOW + 2 * 86400,
                                    bucket_reset=reset, bucket_seen=seen)
                self.assertEqual(item.get("rem_wk"), 6)


if __name__ == "__main__":
    unittest.main()


class ClaudeLimitHitTest(unittest.TestCase):
    """Claude Code writes a synthetic `rate_limit` entry when the limit is hit, then the statusline stops updating."""

    NOW = 1_800_000_000.0

    def metrics(self, hit_text, hit_at, *, rem=2, seen=None, reset=None, bucket="claude-5h"):
        import tui

        seen = self.NOW - 300 if seen is None else seen
        reset = self.NOW + 3600 if reset is None else reset
        with tempfile.TemporaryDirectory() as home:
            statusline = Path(home) / "statusline.json"
            statusline.write_text(json.dumps({"quota": {bucket: {
                "remaining_fraction": rem / 100, "reset_time": reset, "observed_at": seen, "started": True}}}))
            tx = Path(home) / ".claude/projects/p/11111111-1111-4111-8111-111111111111.jsonl"
            tx.parent.mkdir(parents=True)
            rows = [{"type": "user", "message": {"role": "user", "content": "go"}, "timestamp": "2027-01-15T07:00:00Z"}]
            if hit_text:
                rows.append({"type": "assistant", "isApiErrorMessage": True, "error": "rate_limit",
                             "timestamp": datetime.datetime.fromtimestamp(hit_at, datetime.timezone.utc).isoformat(),
                             "message": {"model": "<synthetic>", "role": "assistant",
                                         "content": [{"type": "text", "text": hit_text}]}})
            tx.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
            with patch("tui.time.time", return_value=self.NOW), \
                 patch.dict(os.environ, {"MIXHUB_QUOTA_TTL_SEC": ""}), \
                 patch.dict(tui._QUOTA_SUMMARY_CACHE, {}, clear=True):
                items = tui.get_cli_quota_metrics(statusline_path=str(statusline), home_dir=home)
            return next((item for item in items if item["key"] == "cc"), {})

    def test_session_limit_message_after_last_statusline_reads_as_full(self):
        self.assertEqual(self.metrics(None, 0)["rem_5h"], 2)
        hit = self.metrics("You've hit your session limit · resets 10:50pm (Asia/Tokyo)", self.NOW - 60)
        self.assertEqual(hit["rem_5h"], 0)

    def test_weekly_limit_message_marks_the_weekly_bucket(self):
        hit = self.metrics("You've hit your weekly limit · resets 5am (Asia/Tokyo)", self.NOW - 60,
                           bucket="claude-weekly", reset=self.NOW + 86400)
        self.assertEqual(hit["rem_wk"], 0)

    def test_bucket_reads_as_fresh_once_its_reset_time_passes(self):
        # Full at 04:50, reset at 05:00, no session has redrawn its status line since
        after_reset = self.metrics(None, 0, rem=0, seen=self.NOW - 4 * 3600, reset=self.NOW - 3600,
                                   bucket="claude-weekly")
        self.assertEqual(after_reset["rem_wk"], 100)

    def test_limit_message_older_than_the_statusline_or_the_window_is_ignored(self):
        older_than_statusline = self.metrics("You've hit your session limit · resets 1am", self.NOW - 900)
        self.assertEqual(older_than_statusline["rem_5h"], 2)
        previous_window = self.metrics("You've hit your session limit · resets 1am", self.NOW - 60,
                                       seen=self.NOW - 30000, reset=self.NOW + 3600)
        self.assertEqual(previous_window["rem_5h"], 0)
        before_this_window = self.metrics("You've hit your session limit · resets 1am", self.NOW - 20000,
                                          seen=self.NOW - 30000, reset=self.NOW + 3600)
        self.assertNotEqual(before_this_window.get("rem_5h"), 0)


class ColorPairIdsTest(unittest.TestCase):
    def test_no_two_color_pairs_share_a_literal_id(self):
        # Aliases (`P_ACT_PRI_HOV = P_BTN_HOT`) are fine; two literal `= 56` lines overwrite each other's colors
        root = Path(__file__).resolve().parents[1]
        for rel in ("mixhub/tui/core/style.py",):  # tui.py re-exports the pair ids from here
            by_id = {}
            for m in re.finditer(r"^(P_[A-Z0-9_]+) = (\d+)\b", (root / rel).read_text(), re.M):
                by_id.setdefault(int(m.group(2)), []).append(m.group(1))
            self.assertEqual({k: v for k, v in by_id.items() if len(v) > 1}, {}, rel)


class OuterWindowMarkerTest(unittest.TestCase):
    """Terminal restores a crashed window with the same TERM_SESSION_ID; the zsh hook reattaches only then."""

    def test_attaching_records_the_terminal_window_and_outer_session(self):
        import tui

        with patch.dict(os.environ, {"TERM_SESSION_ID": "ABC-123"}), \
             patch("mixhub.tmux.workspace.allocate_workspace_session_names", return_value=("hub-2", "pool-view-2")), \
             patch("mixhub.tmux.workspace.ensure_outer_dual_pane_layout"), patch("tui.subprocess.run"):
            os.environ.pop("TMUX", None)
            tui.launch_tmux_workspace()
        self.assertEqual(Path(tui.OUTER_WINDOW_MARKER).read_text(), "ABC-123 hub-2")
        with patch.dict(os.environ, {"TERM_SESSION_ID": "XYZ", "TMUX": "/tmp/x,1,0"}):
            tui.record_outer_window("hub")  # inside tmux there is no Terminal window to restore
        self.assertEqual(Path(tui.OUTER_WINDOW_MARKER).read_text(), "ABC-123 hub-2")

    @unittest.skipUnless(shutil.which("zsh"), "the shell hook is a zsh snippet")
    def test_shell_hook_reattaches_only_the_restored_window_with_a_clientless_session(self):
        import tui
        from mixhub.tmux.outer import shell_hook

        # The printed hook reads the marker this install writes (`<state root>/data/outer_window`)
        self.assertIn(shlex.quote(tui.OUTER_WINDOW_MARKER), shell_hook())
        self.assertNotIn(".gemini/session-hub", shell_hook())
        with tempfile.TemporaryDirectory() as home:
            mark = Path(home) / "state dir" / "data" / "outer_window"
            mark.parent.mkdir(parents=True)
            mark.write_text("ABC-123 hub")
            bin_dir = Path(home) / "bin"
            bin_dir.mkdir()
            (bin_dir / "tmux").write_text('#!/bin/sh\n[ "$1 $2" = "-L mixhub" ] || exit 1\nshift 2\n[ "$1" = has-session ] && exit 0\n[ "$1" = list-clients ] && echo "$CLIENTS"\nexit 0\n')
            (bin_dir / "mixhub").write_text("#!/bin/sh\necho REATTACH\n")
            for f in bin_dir.iterdir():
                f.chmod(0o755)
            script = Path(home) / "hook.zsh"
            script.write_text(shell_hook(str(mark)))

            def run(term_id, clients=""):
                env = {"HOME": home, "PATH": f"{bin_dir}:/usr/bin:/bin", "TERM_SESSION_ID": term_id, "CLIENTS": clients}
                return subprocess.run(["zsh", "-f", "-i", "-c", f"source {script}"], capture_output=True,
                                      text=True, env=env).stdout

            self.assertIn("REATTACH", run("ABC-123"))
            self.assertNotIn("REATTACH", run("OTHER-WINDOW"))
            self.assertNotIn("REATTACH", run("ABC-123", clients="/dev/ttys001"))


@unittest.skipUnless(shutil.which("tmux"), "needs tmux")
class OpenCodeTabWindowTest(unittest.TestCase):
    """The MixHub window holding an OpenCode TUI follows the tab on screen, freeing the other tab's window name."""

    def setUp(self):
        self.sock = f"mh-octab-{os.getpid()}"
        self.env = {k: v for k, v in os.environ.items() if k != "TMUX"}
        self.tmux("new-session", "-d", "-s", "pool", "-x", "120", "-y", "40", "-n", "reader", "sleep 600")
        self.tmux("new-window", "-d", "-t", "pool:", "-n", "w-opencode-ses_aaaaaaaa", "sleep 600 # --header")
        self.tmux("split-window", "-d", "-v", "-t", "pool:w-opencode-ses_aaaaaaaa", "sleep 600 # opencode")

    def tearDown(self):
        self.tmux("kill-server")

    def tmux(self, *args):
        return subprocess.run(["tmux", "-L", self.sock, *args], capture_output=True, text=True, env=self.env)

    def windows(self):
        return self.tmux("list-windows", "-t", "pool", "-F", "#{window_name}").stdout.split()

    def test_window_and_header_follow_the_tab_on_screen(self):
        import tui

        tab = {"tool": "opencode", "id": "ses_bbbbbbbbbbbb", "isLive": True, "hubpoolWindow": "w-opencode-ses_aaaaaaaa"}
        with patch("mixhub.tmux.commands._header_cmd", lambda tool, sid: f"sleep 600 # --header {tool} {sid}"):
            self.assertEqual(tui.follow_opencode_tab(tab, socket=self.sock), "w-opencode-ses_bbbbbbbb")
            self.assertEqual(self.windows(), ["reader", "w-opencode-ses_bbbbbbbb"])
            top = self.tmux("display-message", "-p", "-t", "pool:w-opencode-ses_bbbbbbbb.{top}",
                            "#{pane_start_command}").stdout
            self.assertIn("--header opencode ses_bbbbbbbbbbbb", top)
            # already named after the tab, or another session's window in the way: leave it alone
            tab["hubpoolWindow"] = "w-opencode-ses_bbbbbbbb"
            self.assertIsNone(tui.follow_opencode_tab(tab, socket=self.sock))


class NewSessionHeaderCwdTest(unittest.TestCase):
    """A new session's header shows the folder it runs in before the first message, not `~`."""

    def test_header_pane_starts_in_the_cli_folder(self):
        from mixhub.tmux import headers

        tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            out = f"%5|0|40|zsh -c claude|{tmp}\n" if "list-panes" in cmd else ""
            return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

        with patch("mixhub.tmux.headers.subprocess.run", side_effect=fake_run):
            headers.ensure_hubpool_window_header("w-claude-new-1", "claude", "new-1", env={})
        split = next(c for c in calls if "split-window" in c)
        self.assertEqual(split[split.index("-c") + 1], tmp)

    def test_unmatched_new_session_header_shows_its_folder(self):
        import tui

        app = tui.HubTUI.__new__(tui.HubTUI)
        app.header_only, app.header_target, app.sessions, app.selected_idx = True, ("claude", "new-1"), [], -1
        proj = os.path.join(os.path.expanduser("~"), "proj")
        with patch("os.getcwd", return_value=proj):
            s = app.current_session()
        self.assertEqual((s["cwd"], s["shortCwd"]), (proj, "~/proj"))
        # a row handed over through the active selection has an empty folder
        app.sessions = [{"tool": "claude", "id": "new-1", "title": "", "path": "", "cwd": "", "shortCwd": ""}]
        with patch("os.getcwd", return_value=proj):
            s = app.current_session()
        self.assertEqual((s["cwd"], s["shortCwd"]), (proj, "~/proj"))
        # the row a header builds from the window name alone only knows `~`
        app.sessions = [tui._make_new_hubpool_session_dict("claude", "new-1", "w-claude-new-1")]
        with patch("os.getcwd", return_value=proj):
            s = app.current_session()
        self.assertEqual((s["cwd"], s["shortCwd"]), (proj, "~/proj"))

    def test_discovered_new_window_carries_its_folder(self):
        # Every process (sidebar, reader, each header) finds `w-<tool>-new-N` windows on its own
        from mixhub.tmux import new_sessions

        proj = os.path.join(os.path.expanduser("~"), "proj")

        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 0, stdout=f"w-claude-new-7|2|1|{int(time.time())}||{proj}\n")

        with patch.dict(os.environ, {"TMUX": "test-pool"}), \
             patch.dict(new_sessions._NEW_HUBPOOL_SESSIONS, {}, clear=True), \
             patch.dict(new_sessions._HUBPOOL_WINS_CACHE, {"ts": 0.0}, clear=True), \
             patch("mixhub.tmux.new_sessions.subprocess.run", new=fake_run), \
             patch("mixhub.tmux.new_sessions._inspect_new_hubpool_window_draft", return_value=""):
            rows = new_sessions._collect_live_new_hubpool_sessions(allow_probe=True)
        self.assertEqual([(r["id"], r["cwd"], r["shortCwd"]) for r in rows], [("new-7", proj, "~/proj")])


class HomeShellTest(unittest.TestCase):
    """Home keeps a plain shell under the reader; leaving Home parks it in a hidden window, still running."""

    def setUp(self):
        self.sock = f"mh-homeshell-{os.getpid()}-{uuid.uuid4().hex[:8]}"  # a server per test: kill-server is async
        self.env = {k: v for k, v in os.environ.items() if k != "TMUX"}
        self.tmux("new-session", "-d", "-s", "pool", "-x", "120", "-y", "40", "-n", "reader", "sleep 600")
        self.reader = self.tmux("display-message", "-p", "-t", "pool:reader", "#{pane_id}").stdout.strip()

    def tearDown(self):
        self.tmux("kill-server")

    def tmux(self, *args):
        return subprocess.run(["tmux", "-L", self.sock, *args], capture_output=True, text=True, env=self.env)

    def reader_panes(self):
        return self.tmux("list-panes", "-t", "pool:reader", "-F", "#{pane_id}").stdout.split()

    def test_shell_joins_on_home_and_survives_a_trip_to_a_session(self):
        import tui

        tui.sync_home_shell(True, self.reader, socket=self.sock)
        panes = self.reader_panes()
        self.assertEqual(len(panes), 2)
        self.assertEqual(panes[0], self.reader)  # reader stays on top, so `pool:reader.{top}` still means the reader
        shell = panes[1]
        self.assertEqual(self.tmux("display-message", "-p", "-t", "pool:reader", "#{pane_id}").stdout.strip(), shell)
        tui.sync_home_shell(False, self.reader, socket=self.sock)
        self.assertEqual(self.reader_panes(), [self.reader])
        tui.sync_home_shell(True, self.reader, socket=self.sock)
        self.assertEqual(self.reader_panes(), [self.reader, shell])  # same shell, history kept
        top = self.tmux("display-message", "-p", "-t", "pool:reader.{top}", "#{pane_id}").stdout.strip()
        self.assertEqual(top, self.reader)
        # Home opens ready to type: the prompt pane has focus (the reader above is click-only anyway)
        active = self.tmux("display-message", "-p", "-t", "pool:reader", "#{pane_id}").stdout.strip()
        self.assertEqual(active, shell)

    def test_header_pin_hook_leaves_the_reader_alone(self):
        # The hook pinning each session window's `--header` to 2 rows must not squash the reader above the shell
        import tui

        for cmd in tui._hubpool_header_hook_cmds():
            self.tmux(*cmd[3:])
        self.tmux("new-window", "-d", "-t", "pool:", "-n", "w-claude-abcdefabcdef", "sleep 600")
        self.tmux("split-window", "-d", "-v", "-t", "pool:w-claude-abcdefabcdef", "sleep 600")
        tui.sync_home_shell(True, self.reader, socket=self.sock)
        self.tmux("select-window", "-t", "pool:w-claude-abcdefabcdef")
        self.tmux("select-window", "-t", "pool:reader")
        self.tmux("resize-window", "-t", "pool:reader", "-y", "42")
        heights = self.tmux("list-panes", "-t", "pool:reader", "-F", "#{pane_height}").stdout.split()
        self.assertGreater(int(heights[0]), 10)
        hdr = self.tmux("list-panes", "-t", "pool:w-claude-abcdefabcdef", "-F", "#{pane_height}").stdout.split()
        self.assertEqual(hdr[0], str(tui.HUB_HDR_ROWS))

    def test_foreground_agent_in_the_shell(self):
        from mixhub.tmux.home_shell import foreground_agent

        ps = lambda args, fg=True: f"  501   {501 if fg else 700} {args}\n"
        self.assertIsNone(foreground_agent(ps("-zsh")))
        self.assertEqual(foreground_agent(ps("-zsh", fg=False) + ps("claude --resume abc")), "claude")
        self.assertEqual(foreground_agent(ps("node /opt/homebrew/bin/codex")), "codex")
        self.assertEqual(foreground_agent(ps("/opt/opencode/bin/opencode")), "opencode")
        self.assertEqual(foreground_agent(ps("agy")), "agy")
        # one-shot runs and agents left in the background stay where they are
        self.assertIsNone(foreground_agent(ps("claude -p hi")))
        self.assertIsNone(foreground_agent(ps("codex exec fix it")))
        self.assertIsNone(foreground_agent(ps("claude", fg=False) + ps("vim")))

    def test_agent_started_in_the_shell_moves_out_and_home_gets_a_fresh_shell(self):
        import tui
        from mixhub.tmux.home_shell import adopt_home_shell_pane, finished_adopted_windows, home_shell_agent

        def wait_for(fn, timeout=15.0):
            end = time.time() + timeout
            while time.time() < end:
                got = fn()
                if got:
                    return got
                time.sleep(0.2)
            return fn()

        tmp = os.path.realpath(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        os.makedirs(os.path.join(tmp, "bin"))
        os.makedirs(os.path.join(tmp, "proj"))
        fake = os.path.join(tmp, "bin", "claude")
        with open(fake, "w") as f:
            f.write("#!/bin/sh\nsleep 600\n")
        os.chmod(fake, 0o755)

        tui.sync_home_shell(True, self.reader, socket=self.sock)
        shell = self.reader_panes()[1]
        self.assertIsNone(home_shell_agent(self.sock))
        self.tmux("send-keys", "-t", shell, f"cd {tmp}/proj && {fake} --resume abc", "Enter")
        found = wait_for(lambda: home_shell_agent(self.sock))
        self.assertEqual(found, (shell, "claude", os.path.join(tmp, "proj")))

        self.assertTrue(adopt_home_shell_pane(shell, "w-claude-new-123", "claude", "new-123", socket=self.sock))
        moved = self.tmux("display-message", "-p", "-t", shell, "#{window_name}").stdout.strip()
        self.assertEqual(moved, "w-claude-new-123")
        self.assertEqual(self.reader_panes(), [self.reader])
        self.assertIsNone(home_shell_agent(self.sock))  # the moved shell is no longer Home's
        self.assertEqual(finished_adopted_windows(self.sock), [])  # its agent still runs

        tui.sync_home_shell(False, self.reader, socket=self.sock)
        tui.sync_home_shell(True, self.reader, socket=self.sock)
        panes = self.reader_panes()
        self.assertEqual(len(panes), 2)
        self.assertNotEqual(panes[1], shell)  # Home gets a fresh, free shell

        self.tmux("send-keys", "-t", shell, "C-c")
        done = wait_for(lambda: finished_adopted_windows(self.sock))
        self.assertEqual(done, [("w-claude-new-123", "claude", "new-123")])

    def test_shell_takes_half_and_keeps_the_height_it_was_dragged_to(self):
        import tui

        tui.sync_home_shell(True, self.reader, socket=self.sock)
        shell = self.reader_panes()[1]
        height = lambda: int(self.tmux("display-message", "-p", "-t", shell, "#{pane_height}").stdout)
        self.assertIn(height(), (19, 20))  # half of 40 rows, less the divider
        self.tmux("resize-pane", "-t", shell, "-y", "16")  # what dragging the divider does
        tui.sync_home_shell(False, self.reader, socket=self.sock)
        tui.sync_home_shell(True, self.reader, socket=self.sock)
        self.assertEqual(height(), 16)
