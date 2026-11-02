#!/usr/bin/env python3
"""
`mixhub.core.prefs`: Single source of truth for reading and atomically writing `Preferences` JSON.
"""
from __future__ import annotations

import json
import os

from mixhub.core.paths import PREFERENCES_PATH

USER_CONFIG_PREFERENCES_PATH = os.path.join("~", ".config", "mixhub", "Preferences")


def preferences_path() -> str:
    """Where `Preferences` lives: `$MIXHUB_PREFERENCES_PATH`, else `~/.config/mixhub/Preferences` when that file
    exists, else the state directory. Every reader and writer goes through this."""
    explicit = os.environ.get("MIXHUB_PREFERENCES_PATH", "").strip()
    if explicit:
        return os.path.abspath(os.path.expanduser(explicit))
    user = os.path.abspath(os.path.expanduser(USER_CONFIG_PREFERENCES_PATH))
    return user if os.path.isfile(user) else PREFERENCES_PATH


def load_preferences(path: str | None = None) -> dict:
    """Load `Preferences` JSON dictionary (returns `{}` if missing or malformed)."""
    target = os.path.abspath(os.path.expanduser(str(path or preferences_path())))
    try:
        if os.path.isfile(target):
            with open(target, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return dict(data)
    except (OSError, ValueError, TypeError):
        pass
    return {}


def save_preferences(updates_or_full: dict, path: str | None = None, *, merge: bool = True) -> dict:
    """Atomically write or merge `updates_or_full` into `Preferences` JSON."""
    target = os.path.abspath(os.path.expanduser(str(path or preferences_path())))
    data: dict = load_preferences(target) if merge else {}
    if isinstance(updates_or_full, dict):
        data.update(updates_or_full)
    try:
        os.makedirs(os.path.dirname(target) or ".", exist_ok=True)
        tmp = f"{target}.tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, target)
    except OSError:
        pass
    return data


__all__ = [
    "USER_CONFIG_PREFERENCES_PATH",
    "load_preferences",
    "preferences_path",
    "save_preferences",
]
