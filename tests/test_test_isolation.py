"""The suite must never write into the repo's live data/ (the running MixHub reads it)."""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
import tui

LIVE_DATA = str(Path(__file__).resolve().parents[1] / "data")


def _outside_live_data(path):
    return not os.path.abspath(path).startswith(LIVE_DATA + os.sep)


def test_default_state_paths_are_redirected_during_tests():
    assert _outside_live_data(tui.UNSENT_DRAFTS_PATH)
    assert _outside_live_data(tui._effective_tui_diag_log_path())
    assert _outside_live_data(tui.PROMOTED_NEW_MAP_PATH)
    assert _outside_live_data(tui.HIBERNATING_MARKER_DIR)
    assert _outside_live_data(tui.LAST_EXIT_DIR)
    assert _outside_live_data(server.Hub().disk_cache_path)
    scr = MagicMock()
    scr.getmaxyx.return_value = (30, 32)
    with patch.object(tui.HubTUI, "_init_colors"):
        app = tui.HubTUI(scr, caf=MagicMock())
    assert _outside_live_data(app.seen_path)
    assert _outside_live_data(app.unsent_drafts_path)
    assert _outside_live_data(app.attention_path)


def test_draft_capture_logs_session_window_and_length_not_text():
    session = {"id": "s-1", "tool": "claude", "lastPrompt": ""}
    pane = "────\n❯ secret unsent words\n────\n"
    with patch("tui.subprocess.run", return_value=MagicMock(stdout=pane)), patch("mixhub.tui.diag.log_tui_diag") as diag:
        assert tui.capture_and_save_hubpool_draft(session) == "secret unsent words"
    event, fields = diag.call_args.args[0], diag.call_args.kwargs
    assert event == "draft_saved"
    assert fields == {"session_id": "s-1", "wname": "w-claude-s-1", "chars": 19}
