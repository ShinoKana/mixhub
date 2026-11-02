#!/usr/bin/env python3
"""`mixhub.filters.agy_claude.prefs`: real `agy` binary resolution and the mouse-shim switch (env, flags, `Preferences`)."""

import os

from mixhub.core.paths import STATE_ROOT
from mixhub.core.prefs import load_preferences as _core_load_preferences


def _resolve_real_agy() -> str:
    env_bin = os.environ.get("AGY_REAL_BIN")
    if env_bin and os.path.exists(env_bin):
        return env_bin
    bin_candidate = os.path.expanduser("~/.local/bin/agy-bin")
    if os.path.exists(bin_candidate):
        return bin_candidate
    return os.path.expanduser("~/.local/bin/agy")


PREFERENCES_PATH = os.path.join(STATE_ROOT, "Preferences")


def load_filter_preferences(preferences_path: str | None = None) -> dict:
    """Load MixHub runtime `Preferences` JSON (returns `{}` if missing or malformed)."""
    return _core_load_preferences(preferences_path)


def is_mouse_shim_enabled(
    args: list[str] | None = None,
    preferences_path: str | None = None,
) -> bool:
    """
    Determine whether the AGY prompt click-to-move-cursor & drag-delete shim is active:
      - `MIXHUB_NO_AGY_MOUSE=1` or `--no-agy-mouse` -> `False`
      - `Preferences["agy_mouse_shim"]` -> `False` when `false`/`"off"`/`"disabled"`,
        `True` by default (`"auto"` or `true`).
    """
    if os.environ.get("MIXHUB_NO_AGY_MOUSE", "").strip().lower() in ("1", "true", "yes", "on"):
        return False
    argv = list(args or [])
    if "--no-agy-mouse" in argv:
        return False
    prefs = load_filter_preferences(preferences_path)
    shim_pref = prefs.get("agy_mouse_shim", "auto")
    if shim_pref is False or str(shim_pref).strip().lower() in ("false", "0", "off", "no", "disabled"):
        return False
    return True
