"""OpenCode (v2) support: sessions come from its own `opencode.db`, read-only."""

import json
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server

WORK = "/work/proj"
NOW = time.time()


def ms(ts):
    return int(ts * 1000)


def lstart(ts):
    return time.strftime("%a %b %d %H:%M:%S %Y", time.localtime(ts))


class OpenCodeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        (self.home / "hub").mkdir()
        self.hub = server.Hub(str(self.home), str(self.home / "hub"))
        self.hub.processes = lambda: []
        self.hub.open_files = lambda _: {}
        db = self.home / ".local/share/opencode/opencode.db"
        db.parent.mkdir(parents=True)
        self.db = sqlite3.connect(db)
        self.db.executescript(
            """
            CREATE TABLE session_v2 (id text PRIMARY KEY, project_id text NOT NULL, parent_id text, slug text NOT NULL,
              directory text NOT NULL, title text, version text NOT NULL, agent text, model text,
              time_created integer NOT NULL, time_updated integer NOT NULL, time_idle integer, time_viewed integer,
              time_archived integer);
            CREATE TABLE session_message (id text PRIMARY KEY, session_id text NOT NULL, type text NOT NULL,
              seq integer NOT NULL, time_created integer NOT NULL, time_updated integer NOT NULL, data text NOT NULL);
            """
        )
        self.seq = 0

    def tearDown(self):
        self.db.close()
        self.temp.cleanup()

    def session(self, sid, created, *, idle=True, viewed=True, parent=None, archived=None, title="t", cwd=WORK,
                agent="build"):
        updated = created + 5
        self.db.execute(
            "INSERT INTO session_v2 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (sid, "p", parent, "slug", cwd, title, "2", agent, None, ms(created), ms(updated),
             ms(updated) if idle else None,
             (ms(viewed) if not isinstance(viewed, bool) else ms(updated)) if viewed else None, archived),
        )
        self.message(sid, "user", {"text": "first prompt " + sid}, created)
        self.message(sid, "assistant", {"content": [
            {"type": "reasoning", "text": "thinking it over"},
            {"type": "tool", "id": "c1", "name": "shell",
             "state": {"status": "completed", "input": {"command": "ls"}, "content": [{"type": "text", "text": "a.txt"}]}},
            {"type": "text", "text": "one file"},
        ]}, created + 1)
        self.message(sid, "user", {"text": "last prompt " + sid}, created + 2)
        if idle:
            self.message(sid, "assistant", {"content": [{"type": "text", "text": "done"}]}, created + 3)
            self.message(sid, "idle", {"outcome": "succeeded"}, created + 4)
        self.db.commit()
        self.hub.scanned = 0

    def message(self, sid, kind, data, ts):
        self.seq += 1
        data = dict(data, time={"created": ms(ts)})
        self.db.execute("INSERT INTO session_message VALUES (?,?,?,?,?,?,?)",
                        (f"m{self.seq}", sid, kind, self.seq, ms(ts), ms(ts), json.dumps(data)))

    def listed(self, **query):
        return {s["id"]: s for s in self.hub.listing({k: [str(v)] for k, v in query.items()})["sessions"]}

    def oc_process(self, pid=500, args=(), started=NOW - 60):
        return {"pid": pid, "ppid": 1, "tty": "ttys020", "rss_mb": 80.0, "etime": "01:00", "tool": "opencode",
                "args": list(args), "wrapper": False, "start_time": lstart(started)}

    def test_lists_interactive_top_level_sessions_only(self):
        self.session("ses_main", NOW - 3600, title="Fix the parser")
        self.session("ses_headless", NOW - 3000, viewed=False, agent=None)  # `opencode run`: no agent, never viewed
        self.session("ses_child", NOW - 2000, parent="ses_main")  # subagent task
        self.session("ses_gone", NOW - 1000, archived=ms(NOW))
        rows = self.listed()
        self.assertEqual([k for k in rows if k.startswith("ses_")], ["ses_main"])
        s = rows["ses_main"]
        self.assertEqual(s["tool"], "opencode")
        self.assertEqual(s["cwd"], WORK)
        self.assertEqual(s["title"], "Fix the parser")
        self.assertEqual(s["firstPrompt"], "first prompt ses_main")
        self.assertEqual(s["lastPrompt"], "last prompt ses_main")
        self.assertGreaterEqual(s["updatedAt"], NOW - 3600 + 4)
        self.assertEqual(s["resumeCommand"], f"cd {WORK} && opencode -s ses_main")
        self.assertIn("ses_headless", self.listed(show_temp=1))

    def test_process_binds_to_session_started_after_it_and_reads_working_state(self):
        self.session("ses_old", NOW - 7200)
        self.session("ses_new", NOW - 30, idle=False)
        self.hub.processes = lambda: [self.oc_process(started=NOW - 60)]
        self.hub.open_files = lambda _: {500: {"paths": [], "cwd": WORK}}
        rows = self.listed()
        self.assertTrue(rows["ses_new"]["isLive"])
        self.assertEqual(rows["ses_new"]["agentState"], "working")
        self.assertFalse(rows["ses_old"]["isLive"])

    def test_fresh_process_without_a_new_session_does_not_claim_an_old_one(self):
        self.session("ses_old", NOW - 7200)
        self.hub.processes = lambda: [self.oc_process(started=NOW - 60)]
        self.hub.open_files = lambda _: {500: {"paths": [], "cwd": WORK}}
        rows = self.listed()
        self.assertFalse(rows["ses_old"]["isLive"])
        self.assertIn("live-opencode-500", self.listed(show_temp=1))  # a placeholder until the first prompt

    def test_process_follows_the_tab_viewed_last(self):
        # One TUI, two tabs: `b` had the newest reply, but `a` is the tab switched to afterwards
        self.session("ses_a", NOW - 40, viewed=NOW - 5)
        self.session("ses_b", NOW - 30)
        self.hub.processes = lambda: [self.oc_process(started=NOW - 60)]
        self.hub.open_files = lambda _: {500: {"paths": [], "cwd": WORK}}
        rows = self.listed()
        self.assertTrue(rows["ses_a"]["isLive"])
        self.assertFalse(rows["ses_b"]["isLive"])

    def test_a_new_tab_is_listed_though_opencode_never_marks_it_viewed(self):
        self.session("ses_tab", NOW - 30, viewed=False)  # TUI tab: has an agent, `time_viewed` stays empty
        self.assertIn("ses_tab", self.listed())

    def test_explicit_session_flag_yields_to_a_tab_used_after_it(self):
        # `opencode -s ses_a` started by MixHub, then a new tab `ses_tab` was opened and used
        self.session("ses_a", NOW - 7200)
        self.session("ses_tab", NOW - 20, viewed=False)
        self.hub.processes = lambda: [self.oc_process(500, ["-s", "ses_a"], started=NOW - 60)]
        self.hub.open_files = lambda _: {500: {"paths": [], "cwd": WORK}}
        self.hub._hubpool_tty_windows = {"ttys020": "w-opencode-ses_a"}  # the MixHub window it was opened in
        rows = self.listed()
        self.assertTrue(rows["ses_tab"]["isLive"])
        self.assertFalse(rows["ses_a"]["isLive"])
        # clicking the tab must go to that same window, not start a second OpenCode
        self.assertEqual(rows["ses_tab"]["hubpoolWindow"], "w-opencode-ses_a")

    def test_explicit_session_flag_and_continue_bind_to_old_sessions(self):
        self.session("ses_a", NOW - 7200)
        self.session("ses_b", NOW - 5000)
        self.hub.processes = lambda: [self.oc_process(500, ["-s", "ses_a"]), self.oc_process(501, ["-c"])]
        self.hub.open_files = lambda _: {500: {"paths": [], "cwd": WORK}, 501: {"paths": [], "cwd": WORK}}
        rows = self.listed()
        self.assertEqual(rows["ses_a"]["pid"], 500)
        self.assertEqual(rows["ses_b"]["pid"], 501)
        self.assertEqual(rows["ses_b"]["agentState"], "done_idle")

    def test_process_parser_keeps_tui_and_skips_service_and_headless(self):
        data = "\n".join([
            "PID PPID TTY RSS ELAPSED COMMAND",
            "1 0 ttys001 102400 01:00 opencode",
            "2 0 ttys002 102400 01:00 /opt/homebrew/Cellar/opencode/2.2.0/bin/opencode -s ses_x",
            "3 0 ttys003 102400 01:00 opencode run hello",
            "4 0 ttys004 102400 01:00 opencode serve --service",
            "5 0 ttys005 102400 01:00 opencode session list",
            "6 0 ttys006 102400 01:00 node /usr/lib/node_modules/opencode-ai/bin/opencode",
        ])
        self.assertEqual([(p["pid"], p["tool"]) for p in server.parse_processes(data, proc_table={})],
                         [(1, "opencode"), (2, "opencode"), (6, "opencode")])

    def test_transcript_reads_messages_from_the_database(self):
        self.session("ses_main", NOW - 3600)
        s = self.listed()["ses_main"]
        got = [(e["role"], e["kind"], e.get("name", ""), e["text"]) for e in self.hub.transcript(s)]
        self.assertEqual(got[0], ("user", "message", "", "first prompt ses_main"))
        self.assertIn(("thinking", "thinking", "", "thinking it over"), got)
        self.assertIn(("tool", "tool_call", "shell", server.printable({"command": "ls"})), got)
        self.assertIn(("tool", "tool_result", "shell", "a.txt"), got)
        self.assertEqual(got[-1], ("assistant", "message", "", "done"))
        window, truncated = self.hub.transcript_window(s)
        self.assertEqual(len(window), len(got))
        self.assertFalse(truncated)


if __name__ == "__main__":
    unittest.main()
