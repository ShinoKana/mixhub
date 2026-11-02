#!/usr/bin/env python3
"""
Live & regression tests for Claude 5h/7d quota real-time refresh (`providers.py` + `tui.py`):
  - Distinguishes unstarted/expired 5h windows (`started_5h=False` -> renders `  · `) from
    started 5h windows with `<1%` usage (`rem_5h=100, started_5h=True` -> renders `  0% HH:MM`).
  - Post-reset `.jsonl` assistant usage fallback (`infer_claude_5h_reset`, 10-minute floor + 5h)
    when statusline/config still holds an expired `reset_time`.
  - Monotonic cross-session quota merging (`merge_quota_obs`).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import providers
import tui


class TestClaudeLiveQuotaRefresh(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        tui._QUOTA_SUMMARY_CACHE.clear()
        providers._CLAUDE_USAGE_TAIL_CACHE.clear()

    def tearDown(self):
        self._tmp.cleanup()

    def test_confirmed_active_window_with_zero_pct_shows_0pct_and_reset_time(self):
        """
        Observed 17:10 -> 17:14 scenario:
          - New 5h window is active (`reset_time` in the future, e.g. 22:10) with `remaining_fraction == 1.0`
            (`used_percentage == 0` due to Prompt Cache hit / <0.05% output tokens).
          - Must keep `rem_5h == 100` (`u5 == 0%`, never artificially bumped to `1%`) AND set `started_5h = True`
            so `_draw_footer_quota` renders `  0% 22:10` instead of hiding it as `  · `.
        """
        now = time.time()
        rst_5h = int(now + 4 * 3600 + 2700)
        rst_wk = int(now + 3 * 86400)
        st_file = self.home / "agy-statusline.json"
        st_file.write_text(
            json.dumps({
                "quota": {
                    "claude-5h": {
                        "remaining_fraction": 1.0,
                        "reset_time": rst_5h,
                        "observed_at": now - 10,
                    },
                    "claude-weekly": {
                        "remaining_fraction": 0.06,
                        "reset_time": rst_wk,
                        "observed_at": now - 10,
                    },
                }
            }),
            encoding="utf-8",
        )
        items = tui.get_cli_quota_metrics(statusline_path=str(st_file), home_dir=str(self.home))
        cc = next(it for it in items if it["key"] == "cc")
        self.assertEqual(cc["rem_5h"], 100)
        self.assertTrue(cc["started_5h"])
        self.assertEqual(cc["reset_5h"], tui.format_quota_reset_tag(rst_5h, "5h"))
        self.assertEqual(cc["rem_wk"], 6)
        self.assertTrue(cc["started_wk"])

    def test_expired_window_shows_dot_until_post_reset_jsonl_activity(self):
        """
        1) When 5h `reset_time` is in the past (expired at 17:10) and no post-reset `.jsonl` activity exists,
           `rem_5h` is `None` and `started_5h` is `False` (renders `  · `).
        2) As soon as an interactive Claude `.jsonl` transcript appends an assistant reply (`output_tokens > 0`)
           after the expired `reset_time`, `_claude_quota_sources` busts the cache and `_resolve_claude_quota`
           infers the new 5h window (`floor10 + 5h`), setting `rem_5h = 100`, `started_5h = True`, `est_5h = True`.
        """
        now = time.time()
        old_reset = int(now - 300)  # expired 5 minutes ago
        rst_wk = int(now + 3 * 86400)
        st_file = self.home / "agy-statusline.json"
        st_file.write_text(
            json.dumps({
                "quota": {
                    "claude-5h": {
                        "remaining_fraction": 0.40,
                        "reset_time": old_reset,
                        "observed_at": old_reset - 180,
                    },
                    "claude-weekly": {
                        "remaining_fraction": 0.06,
                        "reset_time": rst_wk,
                        "observed_at": old_reset - 180,
                    },
                }
            }),
            encoding="utf-8",
        )
        items1 = tui.get_cli_quota_metrics(statusline_path=str(st_file), home_dir=str(self.home))
        cc1 = next(it for it in items1 if it["key"] == "cc")
        self.assertIsNone(cc1["rem_5h"])
        self.assertFalse(cc1["started_5h"])
        self.assertEqual(cc1["rem_wk"], 6)

        # Now append a post-reset assistant turn to `~/.claude/projects/proj/sess.jsonl`
        proj_dir = self.home / ".claude" / "projects" / "-Users-alice"
        proj_dir.mkdir(parents=True, exist_ok=True)
        jsonl_path = proj_dir / "e68798d1-f1ae-43fb-96f3-93778b809d08.jsonl"
        msg_ts = now - 20
        iso_ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(msg_ts))
        with open(jsonl_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "type": "user",
                "timestamp": iso_ts,
                "message": {"role": "user", "content": "ping"},
            }) + "\n")
            fh.write(json.dumps({
                "type": "assistant",
                "timestamp": iso_ts,
                "message": {
                    "model": "claude-opus-4-6",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "pong"}],
                    "usage": {"input_tokens": 10, "output_tokens": 1611, "cache_read_input_tokens": 264884},
                },
            }) + "\n")

        # Without clearing _QUOTA_SUMMARY_CACHE, _claude_quota_sources must bust the 3s cache immediately
        items2 = tui.get_cli_quota_metrics(statusline_path=str(st_file), home_dir=str(self.home))
        cc2 = next(it for it in items2 if it["key"] == "cc")
        expected_reset = msg_ts - (msg_ts % 600) + 5 * 3600
        self.assertEqual(cc2["rem_5h"], 100)
        self.assertTrue(cc2["started_5h"])
        self.assertTrue(cc2["est_5h"])
        self.assertEqual(cc2["reset_5h"], tui.format_quota_reset_tag(expected_reset, "5h"))

    def test_merge_quota_obs_monotonic_and_window_rollover(self):
        r_old = 1792915800.0  # 17:10
        r_new = 1792933800.0  # 22:10
        fresh = providers.QuotaObs(1.0, r_new, r_old + 293, True)
        stale_idle = providers.QuotaObs(0.40, r_old, r_old + 350, True)
        self.assertEqual(providers.merge_quota_obs(fresh, stale_idle), fresh)
        self.assertEqual(providers.merge_quota_obs(stale_idle, fresh), fresh)

        # Within same window (±90s), smaller remaining_fraction (higher used%) wins
        a = providers.QuotaObs(0.97, r_new, r_old + 400, True)
        b = providers.QuotaObs(0.99, r_new + 2, r_old + 430, True)
        merged = providers.merge_quota_obs(a, b)
        self.assertIsNotNone(merged)
        self.assertAlmostEqual(merged.rem, 0.97)
        self.assertEqual(merged.reset, r_new + 2)

    def test_scan_claude_usage_lines_filters_synthetic_and_sdk_cli(self):
        buf = "\n".join([
            json.dumps({
                "type": "assistant",
                "timestamp": "2026-10-25T08:14:52.832Z",
                "message": {"model": "claude-sonnet-4-6", "usage": {"output_tokens": 1611}},
            }),
            json.dumps({
                "type": "assistant",
                "timestamp": "2026-10-25T08:15:00.000Z",
                "message": {"model": "<synthetic>", "usage": {"output_tokens": 0}},
            }),
            json.dumps({
                "type": "assistant",
                "entrypoint": "sdk-cli",
                "timestamp": "2026-10-25T08:15:10.000Z",
                "message": {"model": "claude-opus-4-6", "usage": {"output_tokens": 500}},
            }),
            json.dumps({
                "type": "assistant",
                "timestamp": "2026-10-25T08:15:20.000Z",
                "message": {"model": "deepseek-chat", "usage": {"output_tokens": 500}},
            }),
        ])
        got = providers.scan_claude_usage_lines(buf, since=0.0)
        self.assertEqual(len(got), 1)


if __name__ == "__main__":
    unittest.main()
