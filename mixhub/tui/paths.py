#!/usr/bin/env python3
"""`mixhub.tui.paths`: the state root and every `data/` state file the TUI reads or writes (plus the legacy `tui.py`
entry script path of a source checkout).

This module is the single owner of the patchable paths: other modules read them as `_paths.NAME` so a test that
redirects `mixhub.tui.paths.SEEN_PATH` (see `tests/conftest.py`) reaches every reader. `ROOT` is the state root
(`mixhub.core.paths.STATE_ROOT`), not the code location.
"""
from __future__ import annotations

import os
import re

from mixhub.core import paths as _core_paths

ROOT = _core_paths.STATE_ROOT
AGY_FILTER = _core_paths.AGY_FILTER
ACTIVE_SEL_PATH = os.path.join(ROOT, "data", "active_selection.json")
TUI_DIAG_LOG_PATH = os.path.join(os.path.dirname(ACTIVE_SEL_PATH), "tui_diag.log")
_DEFAULT_TUI_DIAG_LOG_PATH = TUI_DIAG_LOG_PATH
SEEN_PATH = os.path.join(ROOT, "data", "seen.json")
# `<TERM_SESSION_ID> <outer session>` of the Terminal window MixHub was last attached in. Terminal restores a
# window after a crash with the same TERM_SESSION_ID; the zsh hook (`mixhub shell-hook`) then reattaches when
# that outer session still exists with no client.
OUTER_WINDOW_MARKER = os.path.join(ROOT, "data", "outer_window")
HOME_SHELL_HEIGHT_PATH = os.path.join(ROOT, "data", "home_shell_height")  # rows it was last dragged to
PREFERENCES_PATH = os.path.join(ROOT, "Preferences")


def _effective_tui_diag_log_path() -> str:
    if TUI_DIAG_LOG_PATH != _DEFAULT_TUI_DIAG_LOG_PATH:
        return TUI_DIAG_LOG_PATH
    if ACTIVE_SEL_PATH != globals().get("_DEFAULT_ACTIVE_SEL_PATH", ACTIVE_SEL_PATH):
        return os.path.join(os.path.dirname(ACTIVE_SEL_PATH), "tui_diag.log")
    return TUI_DIAG_LOG_PATH


HIBERNATING_MARKER_DIR = os.path.join(ROOT, "data", "hibernating")
LAST_EXIT_DIR = os.path.join(ROOT, "data", "last_exit")
PROMOTED_NEW_MAP_PATH = os.path.join(ROOT, "data", "promoted_new.json")


_DEFAULT_ACTIVE_SEL_PATH = ACTIVE_SEL_PATH


def _effective_active_sel_path(root: str | None = None) -> str:
    if root is not None and os.path.abspath(root) != os.path.abspath(ROOT):
        base = os.path.join(root, "data", "active_selection.json")
    else:
        base = ACTIVE_SEL_PATH
    if ACTIVE_SEL_PATH != _DEFAULT_ACTIVE_SEL_PATH:
        return base
    outer = os.environ.get("HUB_OUTER_SESSION", "").strip()
    if outer and outer != "hub":
        safe = re.sub(r"[^a-zA-Z0-9_-]", "_", outer)
        return os.path.join(os.path.dirname(base), f"active_selection_{safe}.json")
    return base


UNSENT_DRAFTS_PATH = os.path.join(ROOT, "data", "unsent_drafts.json")
