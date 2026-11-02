import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
import tui

ROOT = Path(__file__).resolve().parents[1]

# Real `tmux list-panes -t hub` output while the outer window is zoomed: the zoomed right pane `%1`
# reports pane_left=0 just like the sidebar `%2`, and `"%1" < "%2"` used to win the sort tie.
ZOOMED_PANES = "%2|0|1|sidebar\n%1|0|1|\n"
ZOOMED_PANES_NO_ROLE = "%2|0|1\n%1|0|1\n"


def _load_bin_hub():
    # `bin/hub` is a thin entry point; its tmux workspace helpers live in mixhub.cli.workspace
    import mixhub.cli.workspace as mod
    return mod


class OuterPaneIdsWhenZoomedTest(unittest.TestCase):
    def setUp(self):
        tui._OUTER_PANE_SNAPSHOT.clear()

    def tearDown(self):
        tui._OUTER_PANE_SNAPSHOT.clear()

    def _run(self, stdout):
        return patch("tui.subprocess.run", return_value=MagicMock(stdout=stdout, returncode=0))

    def test_zoomed_window_keeps_sidebar_as_left(self):
        for raw in (ZOOMED_PANES, ZOOMED_PANES_NO_ROLE):
            with self.subTest(raw=raw):
                tui._OUTER_PANE_SNAPSHOT.clear()
                with self._run(raw):
                    self.assertEqual(tui.get_outer_hub_pane_ids("hub"), ("%2", "%1"))

    def test_unzoomed_legacy_format_still_sorts_by_pane_left(self):
        with self._run("%1|0|0\n%2|33|0"):
            self.assertEqual(tui.get_outer_hub_pane_ids("hub"), ("%1", "%2"))

    def test_role_wins_over_geometry_but_duplicate_roles_are_ignored(self):
        with self._run("%5|0|0|\n%7|33|0|sidebar\n"):
            self.assertEqual(tui.get_outer_hub_pane_ids("hub"), ("%7", "%5"))
        tui._OUTER_PANE_SNAPSHOT.clear()
        with self._run("%2|0|1|sidebar\n%1|0|1|sidebar\n"):
            self.assertEqual(tui.get_outer_hub_pane_ids("hub"), ("%2", "%1"))

    def test_unzoom_restores_sidebar_width_not_agent_pane_width(self):
        calls = []

        def fake_run(cmd, *a, **kw):
            calls.append(cmd)
            return MagicMock(stdout=ZOOMED_PANES if "list-panes" in cmd else "1\n", returncode=0)

        with patch("tui.subprocess.run", side_effect=fake_run):
            self.assertEqual(tui.toggle_outer_sidebar_zoom("hub"), "expanded")
        unzoom = calls[-1]
        self.assertIn("-Z", unzoom)
        i = unzoom.index("-x")
        self.assertEqual(unzoom[i - 1], "%2", unzoom)
        self.assertNotIn(["resize-pane", "-t", "%1", "-x", "32"], [unzoom[j:j + 5] for j in range(len(unzoom))])

    def test_bin_hub_sidebar_pane_id_when_zoomed(self):
        hub = _load_bin_hub()
        with patch.object(hub.subprocess, "run", return_value=MagicMock(stdout=ZOOMED_PANES_NO_ROLE, returncode=0)):
            self.assertEqual(hub._sidebar_pane_id("hub"), ("%2", 2))


def _sess(sid, state, tool="claude", **kw):
    now = time.time()
    d = {"id": sid, "tool": tool, "agentState": state, "isLive": state == "working", "updatedAt": now - 5,
         "title": sid, "inWorkingSet": True, "hasAssistantReply": True}
    d.update(kw)
    return d


class CollapsedBadgeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        home = Path(self.temp.name)
        (home / "hub" / "data").mkdir(parents=True)
        self.hub = server.Hub(str(home), str(home / "hub"))
        self.hub.processes = lambda: []
        self.hub.open_files = lambda _: {}
        self.seen_path = str(home / "hub" / "data" / "seen.json")
        self._orig_tmux = os.environ.pop("TMUX", None)
        self._p = patch("mixhub.tmux.new_sessions._collect_live_new_hubpool_sessions", return_value=[])
        self._p.start()

    def tearDown(self):
        self._p.stop()
        if self._orig_tmux is not None:
            os.environ["TMUX"] = self._orig_tmux
        self.temp.cleanup()

    def _app(self, **kw):
        scr = MagicMock()
        scr.getmaxyx.return_value = (2, 100)
        with patch.object(tui.HubTUI, "_init_colors"), patch("tui.curses.color_pair", side_effect=lambda p: p << 8):
            return tui.HubTUI(scr, hub_instance=self.hub, caf=MagicMock(), seen_path=self.seen_path, **kw)

    def test_badge_counts_only_other_sessions_that_need_you(self):
        app = self._app(header_only=True)
        old = time.time() - 3600
        app._seen_state["bootstrapAt"] = old
        app.sessions = [
            _sess("own", "working"),          # the session this header belongs to
            _sess("busy", "working"),         # working is not "needs you"
            _sess("new-abc", "wait_input"),   # unsaved new session
            _sess("ask", "wait_input"),       # awaiting approval -> counts
            _sess("done", "done_idle", attentionAt=time.time() - 2),  # finished, unseen -> counts
        ]
        app.selected_idx = 0
        self.assertEqual(app._count_awaiting_or_unread_sessions(), 2)
        app.sessions[0]["agentState"] = "wait_input"   # own session waiting is on screen already
        self.assertEqual(app._count_awaiting_or_unread_sessions(), 2)

    def test_header_follows_sidebar_published_attention_without_rescanning(self):
        hdr = self._app(header_only=True)
        hdr.sessions = [_sess("own", "working"), _sess("ask", "working"), _sess("x", "working")]  # frozen at spawn
        hdr.selected_idx = 0
        self.assertEqual(hdr._count_awaiting_or_unread_sessions(), 0)
        sb = self._app(sidebar_only=True)
        sb._seen_state["bootstrapAt"] = time.time() - 3600
        sb.sessions = [_sess("own", "working"), _sess("ask", "wait_input")]
        sb.selected_idx = 0
        sb._publish_attention()
        self.assertTrue(hdr._poll_published_attention())
        self.assertEqual(hdr._count_awaiting_or_unread_sessions(), 1)
        self.assertFalse(hdr._poll_published_attention())  # no change -> no redraw, no rescan
        # Sidebar later sees "ask" approved and "x" finishing unseen; header picks it up on next count
        sb.sessions = [_sess("own", "working"), _sess("ask", "working"),
                       _sess("x", "done_idle", attentionAt=time.time() - 1), _sess("y", "wait_input")]
        time.sleep(0.01)
        sb._publish_attention()
        self.assertEqual(hdr._count_awaiting_or_unread_sessions(), 2)

    def test_home_catches_up_on_attention_published_while_a_session_was_open(self):
        # A session finished while the reader showed another one; returning Home must not keep saying "all clear".
        rd = self._app(reader_only=True)
        sb = self._app(sidebar_only=True)
        sb._seen_state["bootstrapAt"] = time.time() - 3600
        sb.sessions = [_sess("x", "done_idle", attentionAt=time.time() - 1)]
        sb.selected_idx = -1
        rd.sessions = [_sess("x", "working")]
        rd.selected_idx = 0  # reading a session
        with patch.object(type(rd), "refresh_data") as refresh:
            sb._publish_attention()
            rd._poll_published_attention()
            refresh.assert_not_called()
            rd.selected_idx = -1  # back to Home, nothing published since
            self.assertTrue(rd._poll_published_attention())
            refresh.assert_called_once_with(force=True)
            self.assertFalse(rd._poll_published_attention())  # caught up: no rescan on every wake
            refresh.assert_called_once()

    def test_home_needs_you_follows_what_the_sidebar_published(self):
        # Claude asking for approval only shows on its screen. The sidebar's probe saw it; the reader's own rescan ran
        # before its probe did, so Home said "all clear" next to a sidebar row that was waiting.
        rd = self._app(reader_only=True)
        sb = self._app(sidebar_only=True)
        sb._seen_state["bootstrapAt"] = time.time() - 3600
        sb.sessions = [_sess("ask", "wait_input"), _sess("done", "done_idle", attentionAt=time.time() - 1),
                       _sess("busy", "working")]
        rd.sessions = [_sess("ask", "working"), _sess("done", "done_idle"), _sess("busy", "working")]
        rd.selected_idx = -1
        rd._sessions_snapshot_ts = 0.0
        sb._publish_attention()
        rd.stdscr.getmaxyx.return_value = (30, 120)
        with patch("tui.curses.color_pair", side_effect=lambda p: p << 8):
            rd._draw_welcome_pane(30, 100, 0, 30)
        texts = [str(c.args[2]) for c in rd.stdscr.addstr.call_args_list]
        self.assertIn("1 waiting", texts)
        self.assertIn("1 unread", texts)
        listed = {rd.sessions[idx]["id"]: kind for idx, _, kind in
                  [(i, s, k) for i, s, k in rd._welcome_triage_items()]}
        self.assertEqual(listed, {"ask": "awaiting", "done": "unread"})

    def test_secondary_pane_never_clobbers_sidebar_seen_marks(self):
        sb = self._app(sidebar_only=True)
        rd = self._app(reader_only=True)   # loaded before the sidebar writes
        sb._mark_session_seen(_sess("A", "done_idle"))
        mtime_after_sb = os.stat(self.seen_path).st_mtime_ns
        self._app(header_only=True)        # a new window header starting up must not rewrite seen.json
        self.assertEqual(os.stat(self.seen_path).st_mtime_ns, mtime_after_sb)
        rd._mark_session_seen(_sess("B", "done_idle"))
        with open(self.seen_path, encoding="utf-8") as f:
            seen = json.load(f)["seen"]
        self.assertIn("claude:A", seen)
        self.assertIn("claude:B", seen)
        # and the sidebar adopts the reader's mark instead of overwriting it on its next save
        sb._mark_session_seen(_sess("C", "done_idle"))
        with open(self.seen_path, encoding="utf-8") as f:
            seen = json.load(f)["seen"]
        self.assertTrue({"claude:A", "claude:B", "claude:C"} <= set(seen))


if __name__ == "__main__":
    unittest.main()
