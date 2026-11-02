#!/usr/bin/env python3
"""
MixHub (Agent Session Hub) — Terminal Multiplexer & Archive Workspace for AI Coding Agents

Key capabilities:
  - Sub-5ms session switching via ASCII fast-path width calculation, LRU render cache,
    and progressive tail-first JSONL windowing (128KB -> 512KB -> 4MB -> full).
  - Brand-colored single-column badges: pinned ★, live ◇, relay ◈, dormant ·.
      Unread titles and timestamps use bold white; working rows animate at col 30.
  - Dual-pane in-place workspace (`hub` / `hub workspace`):
      Left 32-col sidebar + Right 80% pane backed by `tmux -L hubpool`.
      Dormant sessions render in a zero-RAM archive reader; pressing Enter or Right Arrow
      releases any stale external lock and attaches the live CLI in-place inside the right pane.
  - Event-driven main loop (kqueue + blocking input) with 0.0% idle CPU usage and flicker-free
    mouse text selection (cross-platform clipboard integration).

MixHub launches panes as `python -m mixhub.tui --sidebar | --reader | --header <tool> <sid> | --on-exit ...`; `tui.py`
stays runnable with the same flags (panes started earlier run it) and is a compatibility module: the code lives in `mixhub.tui` (`HubTUI` in `mixhub.tui.app`, its mixins in
`mixhub.tui.hubtui`) and `mixhub.tmux`. Names that tests patch or that hold mutable state are served live from their
owning module (`_RELOCATED_STATE`); patch them there, not here.
"""
from __future__ import annotations

try:
    import curses
except ImportError:  # Windows without windows-curses installed
    curses = None
import locale
import os
import select
import shutil
import socket
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server

from mixhub.core.textwidth import char_width, disp_pad, disp_trunc, disp_width, disp_wrap
from mixhub.tmux.commands import _DEEPSEEK_ENV_SOURCE, hubpool_win_name, interactive_resume_cmd
from mixhub.tmux.headers import HUB_HDR_ROWS, _enforce_hubpool_header_height, _hubpool_header_hook_cmds
from mixhub.tmux.home_shell import sync_home_shell
from mixhub.tmux.new_sessions import _NEW_HUBPOOL_SESSIONS, _NEW_WIN_RE, _make_new_hubpool_session_dict
from mixhub.tmux.onexit import handle_cli_on_exit
from mixhub.tmux.outer import (
    _OUTER_PANE_SNAPSHOT,
    SHELL_HOOK,
    current_outer_session_name,
    emit_outer_terminal_bell,
    format_outer_window_title,
    record_outer_window,
    toggle_outer_sidebar_zoom,
)
from mixhub.tmux.pool import (
    _HUBPOOL_WINS_CACHE,
    _is_external_tab_session,
    _session_preview_mode_and_state,
    follow_opencode_tab,
    takeover_external_session_in_hubpool,
)
from mixhub.tmux.session_state import _hibernating_marker_path, _record_promoted_new_session, _update_working_set_file
from mixhub.tmux.workspace import (
    _sanitize_tmux_terminal_overrides,
    clipboard_copy_cmd,
    format_exit_keepalive_hint,
    launch_tmux_workspace,
)
from mixhub.tui.app import HubTUI, main, parse_header_args
from mixhub.tui.badges import (
    STATE_AGE_COLOR,
    WORKING_DIAMOND_FRAMES,
    WORKING_DIAMOND_PERIOD,
    _agent_badge,
    agent_badge_and_pair,
    status_icon_and_pair,
)
from mixhub.tui.core.style import (
    P_ACT_PRI,
    P_AGENT_AGY,
    P_AGENT_AGY_HOV,
    P_AGENT_AGY_SEL,
    P_AGENT_CLAUDE,
    P_AGENT_CLAUDE_HOV,
    P_AGENT_CLAUDE_SEL,
    P_AGENT_CODEX,
    P_AGENT_CODEX_HOV,
    P_AGENT_CODEX_SEL,
    P_AGENT_OPENCODE,
    P_AGENT_OPENCODE_SEL,
    P_BAND_T4,
    P_BORDER,
    P_BTN_HOT,
    P_BTN_SEC,
    P_DIM,
    P_DONE,
    P_DONE_HOV,
    P_ERR,
    P_HOVER,
    P_HOVER_DIM,
    P_LIVE,
    P_META,
    P_STAT_LBL,
    P_STAT_T1,
    P_STAT_T2,
    P_STAT_T3,
    P_STAT_T4,
    P_UNSEL,
    P_USER_BOX,
    P_WAIT,
    P_WARN,
    P_WHITE,
)
from mixhub.tui.drafts import (
    clear_unsent_draft,
    extract_unsent_draft_from_pane_text,
    load_unsent_drafts,
    save_unsent_draft,
)
from mixhub.tui.paths import _effective_tui_diag_log_path
from mixhub.tui.quota import _QUOTA_SUMMARY_CACHE, format_quota_reset_tag, judge_quota_window
from mixhub.tui.sidebar_rows import layout_sidebar_row_32, rel_time, render_sidebar_row_cells, sidebar_filter_tab_ranges
from mixhub.tui.web import WEB_LINK_LABEL, _ensure_web_and_open, web_archive_url
from mixhub.tui.widgets.header_bar import RELAY_TOGGLE_CLOSED, RELAY_TOGGLE_OPEN, relay_layout
from mixhub.tui.widgets.preview_bar import LBL_GOTO_CHIP, LBL_HISTORY, LBL_RESUME_CHIP, MSG_MOVE_RACE, ST_DORMANT
from mixhub.tui.widgets.quota_table import _FOOTER_AGENTS, _morandi_quota_pair, pick_relay_target
from mixhub.tui.widgets.toast import format_ephemeral_handoff_toast

from mixhub.tmux import commands as _commands
from mixhub.tmux import headers as _headers
from mixhub.tmux import new_sessions as _new_sessions
from mixhub.tmux import outer as _outer
from mixhub.tmux import pool as _pool
from mixhub.tmux import session_state as _session_state
from mixhub.tmux import workspace as _workspace
from mixhub.tui import caffeinate as _caffeinate
from mixhub.tui import diag as _diag
from mixhub.tui import drafts as _drafts
from mixhub.tui import paths as _paths
from mixhub.tui import quota as _quota
from mixhub.tui import selection as _selection
from mixhub.tui import web as _web

# Patchable / mutable names: owned by one module, read live through `tui.<name>`.
_RELOCATED_STATE = {
    "_LAST_WINDOW_TITLE": _outer,
    "ACTIVE_SEL_PATH": _paths,
    "HIBERNATING_MARKER_DIR": _paths,
    "HOME_SHELL_HEIGHT_PATH": _paths,
    "LAST_EXIT_DIR": _paths,
    "OUTER_WINDOW_MARKER": _paths,
    "PROMOTED_NEW_MAP_PATH": _paths,
    "ROOT": _paths,
    "SEEN_PATH": _paths,
    "TUI_DIAG_LOG_PATH": _paths,
    "UNSENT_DRAFTS_PATH": _paths,
    "CaffeinateController": _caffeinate,
    "_build_hubpool_cli_wrapper_cmd": _commands,
    "_codex_quota_sources": _quota,
    "_collect_live_new_hubpool_sessions": _new_sessions,
    "_effective_active_sel_path": _paths,
    "_header_cmd": _commands,
    "_inspect_new_hubpool_window_draft": _new_sessions,
    "_list_outer_client_ttys": _outer,
    "_quota_file_signature": _quota,
    "_record_hubpool_window": _pool,
    "_sync_outer_window_title": _outer,
    "_web_port_listening": _web,
    "activate_session_in_hubpool": _pool,
    "allocate_workspace_session_names": _workspace,
    "capture_and_save_hubpool_draft": _drafts,
    "configure_tmux_mouse_and_copy": _workspace,
    "ensure_hubpool_window_header": _headers,
    "ensure_outer_dual_pane_layout": _workspace,
    "find_hubpool_window_for_session": _pool,
    "focus_outer_left_pane": _outer,
    "focus_outer_right_pane": _outer,
    "get_cli_quota_metrics": _quota,
    "get_outer_hub_pane_ids": _outer,
    "is_outer_hub_zoomed": _outer,
    "log_tui_diag": _diag,
    "read_active_selection": _selection,
    "set_outer_client_autowrap": _outer,
    "write_active_selection": _selection,
}


def __getattr__(name):
    if name in _RELOCATED_STATE:
        return getattr(_RELOCATED_STATE[name], name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


locale.setlocale(locale.LC_ALL, "")


if __name__ == "__main__":
    main()
