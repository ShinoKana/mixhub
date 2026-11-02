#!/usr/bin/env python3
"""
agy_claude_filter.py — Zero-latency PTY ANSI stream filter around `agy`:
  1. Subagent live context badge: `Agent(self: ...)` -> `Agent(Argon: ...) · ctx XXXk`
  2. Mouse click-to-move-cursor in prompt input box (`> ...`): left-clicking any character
     in the bottom prompt line moves the input cursor directly to that character.
     In no-flicker (altscreen) mode the prompt is located on a replayed screen grid (`VScreen`);
     clicks elsewhere, drags, wheel and modified clicks keep going to agy.

Set AGY_RAW_COLORS=1 to bypass filtering at any time.
Non-interactive invocations (`-p` / `--print` / piped stdout) bypass the PTY completely.
Mouse diagnostics: ring buffer dumped to /tmp/agy-mouse-debug.log on clicks and at exit
(AGY_MOUSE_DEBUG=0 off, =2 also records prompt row text; `kill -USR1 <filter pid>` dumps now).
"""
#
# Compatibility shim + script entry point: the implementation lives in `mixhub.filters.agy_claude`
# (prefs, patterns, vscreen, debuglog, cursor, mouse, stream, main). Patch / assign the owning module
# (e.g. `mixhub.filters.agy_claude.stream.SUB_LIVE_PATH`), not this one. The runtime-rebound globals
# `REAL_AGY` (main), `CURRENT_CID` and `_IN_PTY_MAIN` (stream) are not re-exported.
import os
import sys

_FILTER_ROOT = os.path.dirname(os.path.abspath(__file__))
if _FILTER_ROOT not in sys.path:
    sys.path.insert(0, _FILTER_ROOT)

from mixhub.core.textwidth import char_width, disp_width  # noqa: E402,F401
from mixhub.filters.agy_claude.cursor import (  # noqa: E402,F401
    PromptCursorTracker,
)
from mixhub.filters.agy_claude.debuglog import (  # noqa: E402,F401
    MOUSE_DEBUG_PATH,
    MouseDebugLog,
)
from mixhub.filters.agy_claude.main import (  # noqa: E402,F401
    _debug_header,
    main,
)
from mixhub.filters.agy_claude.mouse import (  # noqa: E402,F401
    _ALT_MODES,
    _DECSET_RE,
    filter_stdin_mouse,
    _MOUSE_MODES,
    _SEL_CLEAR_BOTTOM_GUARD,
    _SEL_CLEAR_DELAY,
    _SEL_CLEAR_SAME_CELL_DELAY,
    _SelClear,
    _SGR_MOUSE_RE,
    sync_winsize,
)
from mixhub.filters.agy_claude.patterns import (  # noqa: E402,F401
    ASCII_BS_RE,
    ASCII_FIRST_RE,
    ASCII_INC_RE,
    CURSOR_STEP_RE,
    DSR_REPLY_RE,
    FOOTER_CURSOR_RE,
    FOOTER_END_RE,
    INCREMENTAL_WRAP_RE,
    INPUT_MOUSE_PREFIX_RE,
    MORE_LINES_RE,
    MOUSE_EVENT_RE,
    MOUSE_RESET,
    PASTE_SPLIT_RE,
    PROMPT_CONT_RE,
    PROMPT_HEAD_RE,
    SGR_MOUSE_RE,
    UP_ESCAPE_RE,
)
from mixhub.filters.agy_claude.prefs import (  # noqa: E402,F401
    is_mouse_shim_enabled,
    load_filter_preferences,
    PREFERENCES_PATH,
    _resolve_real_agy,
)
from mixhub.filters.agy_claude.stream import (  # noqa: E402,F401
    ACTIVE_EXT_PATH,
    ACTIVE_EXT_SL_PATH,
    AGENT_ROLE_RE,
    AGENT_TIMER_RE,
    coalesce_pty_output,
    CursorVisibilityGuard,
    exit_restore_bytes,
    _get_subagent_badge,
    _has_unclosed_redraw,
    MAX_PAT_LEN,
    MOUSE_REPLACEMENTS,
    SUB_LIVE_CACHE,
    SUB_LIVE_PATH,
    transform_chunk,
)
from mixhub.filters.agy_claude.vscreen import (  # noqa: E402,F401
    _cell_width,
    _char_disp_width,
    char_idx_to_col,
    col_to_char_idx,
    VScreen,
)

if __name__ == "__main__":
    sys.exit(main())
