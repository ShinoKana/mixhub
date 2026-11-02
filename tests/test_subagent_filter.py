"""Codex ghost rows: state_*.sqlite / session_index.jsonl entries whose rollout `.jsonl` was deleted."""
import json
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server

GHOST = "01a0aaaa-1111-7111-8111-111111111111"
REAL = "01a0bbbb-2222-7222-8222-222222222222"
ARCH = "01a0cccc-3333-7333-8333-333333333333"
ORPHAN = "01a0dddd-4444-7444-8444-444444444444"
NOCOL = "01a0eeee-5555-7555-8555-555555555555"


class CodexGhostFilterTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.root = self.home / "hub"
        self.root.mkdir()
        self.hub = server.Hub(str(self.home), str(self.root))
        self.hub.processes = lambda: []
        self.hub.open_files = lambda _: {}
        self.hub._live_tmux_window_names = lambda: set()

    def tearDown(self):
        self.temp.cleanup()

    def rollout(self, sid, base="sessions/2026/07/01"):
        return self.home / ".codex" / base / ("rollout-2026-07-01T10-00-00-" + sid + ".jsonl")

    def write_rollout(self, path, sid, prompt):
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = [
            {"type": "session_meta", "payload": {"id": sid, "cwd": "/w", "source": "cli", "timestamp": "2026-07-01T10:00:00Z"}},
            {"type": "response_item", "timestamp": "2026-07-01T10:00:01Z",
             "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": prompt}]}},
            {"type": "response_item", "timestamp": "2026-07-01T10:00:05Z",
             "payload": {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "answer"}]}},
        ]
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        return path

    def make_db(self, rows):
        path = self.home / ".codex/state_5.sqlite"
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE threads(id TEXT, title TEXT, cwd TEXT, first_user_message TEXT, updated_at INTEGER,"
                   " rollout_path TEXT, source TEXT, archived INTEGER)")
        for sid, title, rollout_path in rows:
            db.execute("INSERT INTO threads VALUES(?,?,?,?,?,?,?,?)",
                       (sid, title, "/w", "q " + title, int(time.time()), rollout_path, "cli", 0))
        db.commit()
        db.close()

    def write_index(self, entries):
        path = self.home / ".codex/session_index.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps({"id": sid, "thread_name": name, "updated_at": "2026-07-01T10:00:00Z"},
                                           ensure_ascii=False) + "\n" for sid, name in entries), encoding="utf-8")

    def rescan(self):
        self.hub.scanned = 0
        return self.hub.discover(True)

    def listed_ids(self, **query):
        self.hub.scanned = 0
        return [s["id"] for s in self.hub.listing({k: [v] for k, v in query.items()})["sessions"]]

    def test_deleted_rollout_is_dropped_from_discover_and_listing(self):
        self.make_db([(GHOST, "七月的旧会话", str(self.rollout(GHOST)))])
        self.write_index([(GHOST, "七月的旧会话 index 标题")])
        self.assertNotIn("codex:" + GHOST, self.rescan())
        self.assertNotIn(GHOST, self.listed_ids())
        self.assertNotIn(GHOST, self.listed_ids(show_temp="1"))

    def test_session_index_orphan_without_rollout_is_dropped(self):
        self.make_db([])
        self.write_index([(ORPHAN, "只剩索引行的会话")])
        self.assertNotIn("codex:" + ORPHAN, self.rescan())
        self.assertNotIn(ORPHAN, self.listed_ids())

    def test_rollout_in_sessions_is_kept(self):
        path = self.write_rollout(self.rollout(REAL), REAL, "real codex question")
        self.make_db([(REAL, "真实会话", str(path))])
        s = self.rescan()["codex:" + REAL]
        self.assertTrue(s["hasTranscript"])
        self.assertFalse(s["isTemp"])
        self.assertIn(REAL, self.listed_ids())

    def test_rollout_moved_to_archived_sessions_is_kept(self):
        # state DB still points at sessions/..., but Codex moved the transcript to archived_sessions/.
        archived = self.write_rollout(self.rollout(ARCH, "archived_sessions/2026/07"), ARCH, "archived codex question")
        self.make_db([(ARCH, "已归档会话", str(self.rollout(ARCH)))])
        s = self.rescan()["codex:" + ARCH]
        self.assertEqual(s["path"], str(archived))
        self.assertTrue(s["hasTranscript"])
        self.assertIn(ARCH, self.listed_ids())

    def test_deleted_rollout_alive_in_tmux_window_is_kept(self):
        self.make_db([(GHOST, "窗口里还活着", str(self.rollout(GHOST)))])
        for name in ("w-codex-" + GHOST[:12], "w-codex-" + GHOST[:8], "hub-codex-" + GHOST[:12]):
            with self.subTest(window=name):
                self.hub._live_tmux_window_names = lambda name=name: {"reader", name}
                s = self.rescan().get("codex:" + GHOST)
                self.assertIsNotNone(s)
                self.assertTrue(s["isLive"])
                self.assertFalse(s["isTemp"])
                self.assertIn(GHOST, self.listed_ids())

    def test_deleted_rollout_with_live_codex_process_is_kept(self):
        self.make_db([(GHOST, "进程还活着", str(self.rollout(GHOST)))])
        self.hub.processes = lambda: [{"pid": 4242, "ppid": 1, "tty": "ttys009", "rss_mb": 10.0, "etime": "00:10",
                                       "tool": "codex", "args": ["resume", GHOST], "wrapper": False}]
        s = self.rescan().get("codex:" + GHOST)
        self.assertIsNotNone(s)
        self.assertTrue(s["isLive"])
        self.assertIn(GHOST, self.listed_ids())

    def test_deleted_rollout_hibernated_by_hub_is_kept(self):
        self.make_db([(GHOST, "后台休眠", str(self.rollout(GHOST)))])
        (self.root / "hub_state.json").write_text(json.dumps({"sessions": {"codex:" + GHOST: {
            "cwd": "/w", "hibernatedAt": time.time(), "hibernatedByHub": True}}}), encoding="utf-8")
        self.assertIn("codex:" + GHOST, self.rescan())

    def test_row_without_rollout_column_stays_in_discover_but_is_temp(self):
        # Synthetic / older schema without `rollout_path`: no evidence of deletion, so keep it in
        # discover(); with no transcript and no live process it is still hidden from listing.
        db_path = self.home / ".codex/state_5.sqlite"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(db_path)
        db.execute("CREATE TABLE threads(id TEXT, title TEXT, cwd TEXT, first_user_message TEXT, updated_at INTEGER)")
        db.execute("INSERT INTO threads VALUES(?,?,?,?,?)", (NOCOL, "无 rollout 列", "/w", "q", int(time.time())))
        db.commit()
        db.close()
        s = self.rescan()["codex:" + NOCOL]
        self.assertTrue(s["isTemp"])
        self.assertNotIn(NOCOL, self.listed_ids())

    def test_is_temp_session_codex_without_transcript(self):
        base = {"tool": "codex", "id": GHOST, "title": "某个真实标题", "path": str(self.rollout(GHOST))}
        self.assertTrue(server.is_temp_session(base))
        self.assertFalse(server.is_temp_session(dict(base, isLive=True)))
        self.assertFalse(server.is_temp_session(dict(base, hibernatedAt=time.time())))
        self.assertFalse(server.is_temp_session(dict(base, hibernatedByHub=True)))
        real = self.write_rollout(self.rollout(REAL), REAL, "real codex question")
        self.assertFalse(server.is_temp_session(dict(base, path=str(real))))


if __name__ == "__main__":
    unittest.main()
