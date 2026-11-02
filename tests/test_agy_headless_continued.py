"""Regression: `agy -p "…" --conversation <cid>` continuations must stay hidden like the first `agy -p` run.

The continuation sets `last_user_input_step_index >= 0`, so the DB row alone looks like a human session. The TUI
writes every prompt it sends to `history.jsonl` (with `conversationId` from the second prompt on); `agy -p` writes none.
"""
import datetime
import json
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server

HEADLESS = "33333333-3333-4333-8333-333333333333"
HUMAN = "44444444-4444-4444-8444-444444444444"
ONE_PROMPT = "77777777-7777-4777-8777-777777777777"
OTHER = "88888888-8888-4888-8888-888888888888"
WS = "/workspace"


def iso(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f+00:00")


class AgyHeadlessContinuedTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        root = self.home / "hub"
        root.mkdir()
        self.hub = server.Hub(str(self.home), str(root))
        self.hub.processes = lambda: []
        self.hub.open_files = lambda _: {}
        self.agy = self.home / ".gemini/antigravity-cli"
        self.agy.mkdir(parents=True)
        db = sqlite3.connect(self.agy / "conversation_summaries.db")
        db.execute("CREATE TABLE conversation_summaries(conversation_id TEXT, title TEXT, preview TEXT, workspace_uris TEXT,"
                   " parent_conversation_id TEXT, step_count INTEGER, last_modified_time INTEGER,"
                   " last_user_input_time TEXT, last_user_input_step_index INTEGER)")
        db.commit()
        db.close()
        self.now = time.time()

    def tearDown(self):
        self.temp.cleanup()

    def add_row(self, sid, title, ui_idx, input_at):
        db = sqlite3.connect(self.agy / "conversation_summaries.db")
        db.execute("INSERT INTO conversation_summaries VALUES(?,?,?,?,?,?,?,?,?)",
                   (sid, title, "", json.dumps(["file://" + WS]), "", ui_idx + 2, int(input_at), iso(input_at), ui_idx))
        db.commit()
        db.close()
        path = self.agy / "brain" / sid / ".system_generated/logs/transcript.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"step_index": 0, "type": "USER_INPUT",
                                    "content": "<USER_REQUEST>" + title + "</USER_REQUEST>"}) + "\n")

    def write_history(self, entries):
        with (self.agy / "history.jsonl").open("w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")

    def prompt(self, ts, cid=None, workspace=WS, text="typed"):
        e = {"display": text, "timestamp": int(ts * 1000), "workspace": workspace}
        if cid:
            e["conversationId"] = cid
        return e

    def scan(self):
        self.hub.scanned = 0
        return self.hub.discover(True)

    def test_headless_continuation_stays_automated(self):
        self.add_row(HEADLESS, '{"action": "note", "text": "x"}', 2, self.now - 60)
        self.add_row(HUMAN, "real question", 5, self.now - 300)
        self.write_history([self.prompt(self.now - 3600), self.prompt(self.now - 300, HUMAN)])
        sessions = self.scan()
        s = sessions["agy:" + HEADLESS]
        self.assertTrue(s.get("automated"))
        self.assertTrue(s.get("isTemp"))
        self.assertFalse(sessions["agy:" + HUMAN].get("automated"))
        self.assertNotIn("agy:" + HEADLESS, {k for k, v in sessions.items() if not v.get("automated")})

    def test_single_prompt_tui_session_matched_by_time_is_human(self):
        # The TUI's first prompt is logged before the conversation id exists, so only time + workspace link it.
        self.add_row(ONE_PROMPT, "one question", 0, self.now - 120)
        self.write_history([self.prompt(self.now - 3600, OTHER), self.prompt(self.now - 120.4, text="one question")])
        self.assertFalse(self.scan()["agy:" + ONE_PROMPT].get("automated"))

    def test_prompt_at_same_time_in_another_workspace_does_not_count(self):
        self.add_row(HEADLESS, "headless", 0, self.now - 120)
        self.write_history([self.prompt(self.now - 3600, OTHER), self.prompt(self.now - 120.4, workspace="/elsewhere")])
        self.assertTrue(self.scan()["agy:" + HEADLESS].get("automated"))

    def test_without_history_file_rows_stay_visible(self):
        self.add_row(HEADLESS, "unknown origin", 2, self.now - 60)
        self.assertFalse(self.scan()["agy:" + HEADLESS].get("automated"))

    def test_input_older_than_history_window_stays_visible(self):
        # history.jsonl may be trimmed; a conversation last used before its oldest entry gets the benefit of the doubt.
        self.add_row(HUMAN, "old human session", 4, self.now - 86400)
        self.write_history([self.prompt(self.now - 3600, OTHER)])
        self.assertFalse(self.scan()["agy:" + HUMAN].get("automated"))

    def test_history_appearing_later_rechecks_cached_row(self):
        self.add_row(ONE_PROMPT, "one question", 0, self.now - 120)
        self.write_history([self.prompt(self.now - 3600, OTHER)])
        self.assertTrue(self.scan()["agy:" + ONE_PROMPT].get("automated"))
        self.write_history([self.prompt(self.now - 3600, OTHER), self.prompt(self.now - 30, ONE_PROMPT)])
        self.assertFalse(self.scan()["agy:" + ONE_PROMPT].get("automated"))


    def test_tui_session_cut_off_in_its_first_turn_is_human(self):
        # The TUI leaves last_user_input_step_index at -1 until the first turn completes; a window closed mid-reply
        # leaves it there. A TUI prompt at the same time and place still marks it as the user's own.
        self.add_row(ONE_PROMPT, "first question", -1, self.now - 120)
        self.write_history([self.prompt(self.now - 3600, OTHER), self.prompt(self.now - 120.4, text="first  question")])
        self.assertFalse(self.scan()["agy:" + ONE_PROMPT].get("automated"))

    def test_agy_p_first_run_without_a_tui_prompt_stays_automated(self):
        self.add_row(HEADLESS, "headless first run", -1, self.now - 120)
        self.write_history([self.prompt(self.now - 3600, OTHER)])
        self.assertTrue(self.scan()["agy:" + HEADLESS].get("automated"))

    def test_tui_prompt_at_the_same_moment_with_other_text_does_not_count(self):
        # An `agy -p` run in the same folder within the match window must not borrow a TUI prompt typed elsewhere.
        self.add_row(HEADLESS, "headless task", -1, self.now - 120)
        self.write_history([self.prompt(self.now - 3600, OTHER), self.prompt(self.now - 119, text="my own question")])
        self.assertTrue(self.scan()["agy:" + HEADLESS].get("automated"))

    def test_text_check_falls_back_to_time_when_the_transcript_is_missing(self):
        self.add_row(ONE_PROMPT, "first question", -1, self.now - 120)
        (self.agy / "brain" / ONE_PROMPT / ".system_generated/logs/transcript.jsonl").unlink()
        self.write_history([self.prompt(self.now - 3600, OTHER), self.prompt(self.now - 120.4, text="anything")])
        self.assertFalse(self.scan()["agy:" + ONE_PROMPT].get("automated"))

    def test_prompts_relayed_by_another_agent_are_not_the_users(self):
        # Another session typing a note into an agy TUI writes history.jsonl like a person would.
        rules = {"automated_prefixes": [], "automated_substrings": [], "relayed_prefixes": ["[relay from "]}
        self.add_row(HEADLESS, "[relay from tg] note", 1, self.now - 120)
        self.add_row(HUMAN, "my session", 4, self.now - 60)
        self.write_history([self.prompt(self.now - 3600, OTHER),
                            self.prompt(self.now - 120, HEADLESS, text="[relay from tg] note"),
                            self.prompt(self.now - 900, HUMAN, text="my own question"),
                            self.prompt(self.now - 60, HUMAN, text="[relay from tg] fyi")])
        with patch("mixhub.hub.session_filters._load_local_rules", return_value=rules):
            sessions = self.scan()
        self.assertTrue(sessions["agy:" + HEADLESS].get("automated"))
        self.assertFalse(sessions["agy:" + HUMAN].get("automated"))  # a relayed note doesn't make it someone else's

if __name__ == "__main__":
    unittest.main()
