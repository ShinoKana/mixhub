#!/usr/bin/env python3
"""
`mixhub.core`: 5-State Machine (`WORKING / AWAITING / UNREAD / IDLE / HIBERNATED`),
Text Display Width (`textwidth`), Preferences (`prefs`), Paths (`paths`), and `hubpool` Daemon Lifecycle.

Pure stdlib and import-light: lightweight consumers (like the `agy` PTY filter importing `mixhub.core.textwidth`)
never import the Hub at startup. The `Hub` class and its helpers live in `mixhub.hub`.
"""
from __future__ import annotations

from mixhub.core.paths import (
    ACTIVE_SEL_PATH,
    AGY_FILTER,
    DATA_DIR,
    DEFAULT_WEB_PORT,
    HIBERNATING_MARKER_DIR,
    LAST_EXIT_DIR,
    PREFERENCES_PATH,
    ROOT,
    SEEN_PATH,
    UNSENT_DRAFTS_PATH,
)
from mixhub.core.prefs import (
    load_preferences,
    save_preferences,
)
from mixhub.core.state import (
    ALL_SESSION_STATES,
    AWAITING,
    HIBERNATED,
    IDLE,
    SessionState,
    UNREAD,
    WORKING,
    classify_session_state,
)
from mixhub.core.textwidth import (
    SINGLE_COL_SYMBOLS,
    char_width,
    clean_single_line,
    disp_pad,
    disp_trunc,
    disp_width,
    disp_wrap,
    rel_time,
)


__all__ = [
    "ACTIVE_SEL_PATH",
    "AGY_FILTER",
    "ALL_SESSION_STATES",
    "AWAITING",
    "DATA_DIR",
    "DEFAULT_WEB_PORT",
    "HIBERNATED",
    "HIBERNATING_MARKER_DIR",
    "IDLE",
    "LAST_EXIT_DIR",
    "PREFERENCES_PATH",
    "ROOT",
    "SEEN_PATH",
    "SINGLE_COL_SYMBOLS",
    "SessionState",
    "UNREAD",
    "UNSENT_DRAFTS_PATH",
    "WORKING",
    "char_width",
    "classify_session_state",
    "clean_single_line",
    "disp_pad",
    "disp_trunc",
    "disp_width",
    "disp_wrap",
    "load_preferences",
    "rel_time",
    "save_preferences",
]
