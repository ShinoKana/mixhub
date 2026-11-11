"""Regression tests: `agy -p` 0~7s startup window, blank-TUI promotion, and Hub-vs-user hibernation state."""
import fcntl
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server

A = "22222222-2222-4222-8222-222222222222"
B = "55555555-5555-4555-8555-555555555555"
NEW = "66666666-6666-4666-8666-666666666666"


class AgyHeadlessAndHibernateStateTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.root = self.home / "hub"
        self.root.mkdir()
        self.hub = server.Hub(str(self.home), str(self.root))
        self.hub.processes = lambda: []
        self.hub.open_files = lambda _: {}
        self.db_path = self.home / ".gemini/antigravity-cli/conversation_summaries.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_fds = []

    def tearDown(self):
        for fd in self._lock_fds:
            os.close(fd)
        self.temp.cleanup()

    # ---- helpers -------------------------------------------------------
    def make_db(self, with_ui_col=True):
        db = sqlite3.connect(self.db_path)
        cols = "conversation_id TEXT, title TEXT, preview TEXT, workspace_uris TEXT, parent_conversation_id TEXT, step_count INTEGER, last_modified_time INTEGER"
        if with_ui_col:
            cols += ", last_user_input_step_index INTEGER"
        db.execute(f"CREATE TABLE conversation_summaries({cols})")
        db.commit()
        db.close()

    def upsert_row(self, sid, step_count, ui_idx, title="", preview=""):
        db = sqlite3.connect(self.db_path)
        db.execute("DELETE FROM conversation_summaries WHERE conversation_id=?", (sid,))
        db.execute(
            "INSERT INTO conversation_summaries(conversation_id, title, preview, workspace_uris, parent_conversation_id, step_count, last_modified_time, last_user_input_step_index) VALUES(?,?,?,?,?,?,?,?)",
            (sid, title, preview, json.dumps(["file:///workspace"]), "", step_count, int(time.time()), ui_idx),
        )
        db.commit()
        db.close()

    def write_transcript(self, sid, rows):
        path = self.home / (".gemini/antigravity-cli/brain/" + sid + "/.system_generated/logs/transcript.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return path

    def hold_presence_lock(self, sid):
        lock_dir = self.home / ".gemini/antigravity-cli/presence"
        lock_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lock_dir / (sid + ".lock")), os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self._lock_fds.append(fd)

    def rescan(self):
        self.hub.scanned = 0
        return self.hub.discover(True)

    def working_set_keys(self):
        data = server.read_json(self.hub.working_set_path, {"sessions": []})
        return set(data.get("sessions") or [])

    # ---- 1. agy -p startup window -------------------------------------
    def test_agy_p_step0_with_presence_lock_is_automated_not_live(self):
        self.make_db()
        self.upsert_row(A, step_count=0, ui_idx=-1)
        self.write_transcript(A, [])
        self.hold_presence_lock(A)
        sessions = self.rescan()
        s = sessions["agy:" + A]
        self.assertTrue(s["automated"])
        self.assertTrue(s["isTemp"])
        self.assertFalse(s["isLive"])
        self.hub.listing({})
        self.assertNotIn("agy:" + A, self.working_set_keys())

    def test_agy_p_run_past_its_first_prompt_stays_out_of_active(self):
        """A one-shot `agy -p` another agent started looks like a human session once its prompt lands, but it holds only
        a presence lock, no interactive process: it must not flash into `Active`, only show up in `Recent`."""
        self.make_db()
        self.upsert_row(A, step_count=2, ui_idx=0, title="Summarise the brief")
        self.write_transcript(A, [
            {"step_index": 0, "type": "USER_INPUT", "content": "<USER_REQUEST>Summarise the brief</USER_REQUEST>"},
            {"step_index": 1, "type": "PLANNER_RESPONSE", "content": "working"},
        ])
        self.hold_presence_lock(A)
        self.rescan()
        self.hub.listing({})
        self.assertNotIn("agy:" + A, self.working_set_keys())

    def test_blank_human_tui_flips_back_after_first_prompt(self):
        self.make_db()
        self.upsert_row(A, step_count=0, ui_idx=-1)
        self.write_transcript(A, [])
        self.hold_presence_lock(A)
        self.assertTrue(self.rescan()["agy:" + A]["automated"])
        # Human sends first message -> ui_idx >= 0.
        self.upsert_row(A, step_count=2, ui_idx=0, title="real human question")
        self.write_transcript(A, [
            {"step_index": 0, "type": "USER_INPUT", "content": "<USER_REQUEST>real human question</USER_REQUEST>"},
            {"step_index": 1, "type": "PLANNER_RESPONSE", "content": "answer"},
        ])
        s = self.rescan()["agy:" + A]
        self.assertFalse(s.get("automated"))
        self.assertFalse(s["isTemp"])
        self.assertTrue(s["isLive"])
        self.assertFalse(self.hub._session_db_meta["agy:" + A]["automated"])

    def test_transcript_before_db_row_is_transient_headless(self):
        self.make_db()
        self.upsert_row(B, step_count=2, ui_idx=0, title="other human session")
        self.write_transcript(NEW, [{"step_index": 0, "type": "USER_INPUT", "content": "<USER_REQUEST>headless prompt</USER_REQUEST>"}])
        s = self.rescan()["agy:" + NEW]
        self.assertTrue(s["automated"])
        self.assertTrue(s["isTemp"])
        self.assertNotIn("automated", self.hub._session_db_meta.get("agy:" + NEW, {}))
        # Once the DB row lands with real user input, it is a normal session.
        self.upsert_row(NEW, step_count=2, ui_idx=0, title="headless prompt")
        self.assertFalse(self.rescan()["agy:" + NEW].get("automated"))

    def test_old_schema_without_ui_col_does_not_mark_transcript_only_sessions(self):
        self.make_db(with_ui_col=False)
        self.write_transcript(NEW, [{"step_index": 0, "type": "USER_INPUT", "content": "<USER_REQUEST>human prompt</USER_REQUEST>"}])
        self.assertFalse(self.rescan()["agy:" + NEW].get("automated"))

    def test_index_cache_schema_bumped(self):
        self.assertGreaterEqual(server.INDEX_CACHE_SCHEMA_VERSION, 14)

    # ---- 2. hibernation state machine ---------------------------------
    def _session(self):
        return {"tool": "agy", "id": A, "cwd": "/workspace"}

    def _procs(self):
        return [{"pid": 999999, "tty": "ttys099", "boot_id": "bt:1", "start_time": "Sat Oct 31 00:00:00 2026"}]

    def test_hub_hibernate_persists_flag_before_signalling(self):
        key = "agy:" + A
        seen_at_kill = {}

        def fake_kill(ident, sig, kill_fn):
            seen_at_kill["state"] = json.loads(Path(self.hub.state_path).read_text())["sessions"][key]
            return False

        with patch("mixhub.hub.lifecycle.safe_kill_process", fake_kill), patch("server.subprocess.run"):
            self.hub.hibernate(self._session(), self._procs(), dry=False, keep_working_set=True)
        self.assertTrue(seen_at_kill["state"]["hibernatedByHub"])
        self.assertIn("hibernatedAt", seen_at_kill["state"])

    def test_user_hibernate_and_close_session_mark_not_by_hub(self):
        with patch("server.os.kill", side_effect=ProcessLookupError), patch("server.subprocess.run"):
            self.hub.hibernate(self._session(), self._procs(), dry=False, keep_working_set=False)
        state = json.loads(Path(self.hub.state_path).read_text())["sessions"]["agy:" + A]
        self.assertFalse(state["hibernatedByHub"])
        self.hub.save("agy:" + A, {"hibernatedByHub": True})
        with patch.object(self.hub, "get", return_value={"tool": "agy", "id": A, "cwd": "/workspace", "processes": []}), patch("server.subprocess.run"):
            self.hub.close_session("agy", A)
        state = json.loads(Path(self.hub.state_path).read_text())["sessions"]["agy:" + A]
        self.assertFalse(state["hibernatedByHub"])

    def test_discover_exposes_hibernated_by_hub_only_while_not_live(self):
        self.make_db()
        self.upsert_row(A, step_count=2, ui_idx=0, title="human session")
        self.write_transcript(A, [{"step_index": 0, "type": "USER_INPUT", "content": "<USER_REQUEST>human session</USER_REQUEST>"}])
        self.hub.save("agy:" + A, {"hibernatedAt": time.time(), "hibernatedByHub": True, "cwd": "/workspace"})
        self.assertTrue(self.rescan()["agy:" + A]["hibernatedByHub"])
        # Woken up again: a live process claims it -> flag clears automatically.
        self.hub.processes = lambda: [{"pid": 4242, "ppid": 1, "tty": "ttys013", "rss_mb": 10.0, "etime": "00:01", "tool": "agy", "args": ["--conversation", A], "wrapper": False}]
        self.hub.open_files = lambda _: {4242: {"paths": ["/brain/" + A], "cwd": "/workspace"}}
        s = self.rescan()["agy:" + A]
        self.assertTrue(s["processes"])
        self.assertFalse(s["hibernatedByHub"])


if __name__ == "__main__":
    unittest.main()
