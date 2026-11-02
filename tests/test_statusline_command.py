"""`mixhub statusline claude|agy`: a statusline command that records quota for the footer, no `/tmp` scripts needed."""
import io
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from mixhub.cli import main as cli_main
from mixhub.tui import quota as tui_quota


class StatuslineCommandTest(unittest.TestCase):
    def run_statusline(self, tool, payload):
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["mixhub", "statusline", tool]), \
                mock.patch("sys.stdin", io.StringIO(json.dumps(payload))), mock.patch("sys.stdout", out):
            code = cli_main.entry()
        return code, out.getvalue()

    def test_claude_rate_limits_reach_the_footer(self):
        now = time.time()
        code, line = self.run_statusline("claude", {
            "model": {"display_name": "Opus"},
            "rate_limits": {"five_hour": {"used_percentage": 17, "resets_at": int(now + 3600)},
                            "seven_day": {"used_percentage": 40, "resets_at": int(now + 3 * 86400)}},
        })
        self.assertEqual(code, 0)
        self.assertIn("Opus", line)
        self.assertIn("5h 83%", line)
        tui_quota._QUOTA_SUMMARY_CACHE.clear()
        with tempfile.TemporaryDirectory() as home:
            items = tui_quota.get_cli_quota_metrics(home_dir=home)
        cc = next(it for it in items if it["key"] == "cc")
        self.assertEqual(cc["rem_5h"], 83)
        self.assertEqual(cc["rem_wk"], 60)

    def test_payload_without_rate_limits_still_prints_a_line(self):
        code, line = self.run_statusline("claude", {"model": {"display_name": "Sonnet"}})
        self.assertEqual(code, 0)
        self.assertIn("Sonnet", line)

    def test_unknown_tool_is_a_usage_error(self):
        err = io.StringIO()
        with mock.patch.object(sys, "argv", ["mixhub", "statusline", "nope"]), mock.patch("sys.stderr", err), \
                mock.patch("sys.stdin", io.StringIO("{}")):
            self.assertEqual(cli_main.entry(), 2)
        self.assertIn("statusline", err.getvalue())


if __name__ == "__main__":
    unittest.main()
