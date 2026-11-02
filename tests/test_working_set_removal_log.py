"""Every way a session leaves `Active` leaves a line in the diag log saying why, so a session that "suddenly went to
Recent" can be traced afterwards."""
import json
import os
import tempfile
import unittest
from unittest import mock

from mixhub.tmux import session_state


class RemovalLogTest(unittest.TestCase):
    def test_removal_is_logged_with_its_reason_and_adding_is_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = os.path.join(tmp, "active_working_set.json")
            log = os.path.join(tmp, "tui_diag.log")
            with open(ws, "w") as f:
                json.dump({"sessions": ["claude:a", "codex:b"]}, f)
            with mock.patch("mixhub.tui.diag._effective_tui_diag_log_path", return_value=log):
                session_state._update_working_set_file("claude", "c", ws, keep=True)
                session_state._remove_from_working_set_file("claude", "a", ws, reason="exit rc=0")
                session_state._remove_from_working_set_file("claude", "a", ws, reason="exit rc=0")  # already gone
            with open(log) as f:
                rows = [json.loads(line) for line in f]
            with open(ws) as f:
                self.assertEqual(json.load(f)["sessions"], ["codex:b", "claude:c"])
        self.assertEqual([(r["event"], r["session_id"], r["reason"]) for r in rows],
                         [("working_set_removed", "a", "exit rc=0")])


if __name__ == "__main__":
    unittest.main()
