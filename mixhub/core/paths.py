#!/usr/bin/env python3
"""
`mixhub.core.paths`: where the code lives (`ROOT` / `CODE_ROOT`) and where the state lives (`STATE_ROOT`), plus the
canonical data directories, port and marker file paths. Pure stdlib, zero external dependencies.

Code and state are separate locations:
  - `ROOT` (alias `CODE_ROOT`) is the directory that contains the `mixhub` package: the repository root in a source
    checkout, `site-packages` in an installed wheel. Never write there.
  - `STATE_ROOT` holds every state file (`data/`, `Preferences`, `hub_state.json`, `active_working_set.json`,
    `.hub.pid`, `hub.log`, `.hub.lock`). `resolve_state_root()` picks it, first match wins:
      1. `$MIXHUB_HOME`;
      2. the repository root when the code runs from a source checkout (`.git` and `pyproject.toml` next to the
         `mixhub` package), so an existing checkout keeps reading and writing the same files in place;
      3. `$XDG_DATA_HOME/mixhub` (absolute paths only), else `~/.local/share/mixhub`.
"""
from __future__ import annotations

import os
from collections.abc import Mapping

PACKAGE_DIR: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CODE_ROOT: str = os.path.dirname(PACKAGE_DIR)
ROOT: str = CODE_ROOT


def is_source_checkout(code_root: str = CODE_ROOT) -> bool:
    """True when `code_root` is a MixHub source tree (`.git` dir or worktree file + `pyproject.toml` + `mixhub/`)."""
    return (
        os.path.exists(os.path.join(code_root, ".git"))
        and os.path.isfile(os.path.join(code_root, "pyproject.toml"))
        and os.path.isdir(os.path.join(code_root, "mixhub"))
    )


def resolve_state_root(environ: Mapping[str, str] | None = None, code_root: str = CODE_ROOT) -> str:
    """Return the state directory: `$MIXHUB_HOME` > source checkout root > `$XDG_DATA_HOME/mixhub` > `~/.local/share/mixhub`."""
    env = os.environ if environ is None else environ
    explicit = str(env.get("MIXHUB_HOME") or "").strip()
    if explicit:
        return os.path.abspath(os.path.expanduser(explicit))
    if is_source_checkout(code_root):
        return code_root
    xdg = os.path.expanduser(str(env.get("XDG_DATA_HOME") or "").strip())
    if xdg and os.path.isabs(xdg):
        return os.path.join(xdg, "mixhub")
    home = str(env.get("HOME") or "").strip() or os.path.expanduser("~")
    return os.path.join(home, ".local", "share", "mixhub")


STATE_ROOT: str = resolve_state_root()


def state_path(*parts: str) -> str:
    """Join `parts` onto the current `STATE_ROOT` (read at call time, so a patched `STATE_ROOT` is honored)."""
    return os.path.join(STATE_ROOT, *parts)


def ensure_state_root() -> str:
    """Create `STATE_ROOT` (owner-only when newly made) and return it."""
    os.makedirs(STATE_ROOT, mode=0o700, exist_ok=True)
    return STATE_ROOT


DATA_DIR: str = os.path.join(STATE_ROOT, "data")
# The `agy` filter package (launched as `-m mixhub.filters.agy_claude`); its debug log names this path.
AGY_FILTER: str = os.path.join(PACKAGE_DIR, "filters", "agy_claude")
ACTIVE_SEL_PATH: str = os.path.join(DATA_DIR, "active_selection.json")
SEEN_PATH: str = os.path.join(DATA_DIR, "seen.json")
PREFERENCES_PATH: str = os.path.join(STATE_ROOT, "Preferences")
UNSENT_DRAFTS_PATH: str = os.path.join(DATA_DIR, "unsent_drafts.json")
HIBERNATING_MARKER_DIR: str = os.path.join(DATA_DIR, "hibernating")
LAST_EXIT_DIR: str = os.path.join(DATA_DIR, "last_exit")
OUTER_WINDOW_MARKER: str = os.path.join(DATA_DIR, "outer_window")
WEB_STATIC_DIR: str = os.path.join(PACKAGE_DIR, "web", "static")
DEFAULT_WEB_PORT: int = 8765


__all__ = [
    "ACTIVE_SEL_PATH",
    "AGY_FILTER",
    "CODE_ROOT",
    "DATA_DIR",
    "DEFAULT_WEB_PORT",
    "HIBERNATING_MARKER_DIR",
    "LAST_EXIT_DIR",
    "OUTER_WINDOW_MARKER",
    "PACKAGE_DIR",
    "PREFERENCES_PATH",
    "ROOT",
    "SEEN_PATH",
    "STATE_ROOT",
    "UNSENT_DRAFTS_PATH",
    "WEB_STATIC_DIR",
    "ensure_state_root",
    "is_source_checkout",
    "resolve_state_root",
    "state_path",
]
