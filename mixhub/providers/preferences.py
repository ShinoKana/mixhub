"""Provider-side `Preferences` lookup (the shared one in `mixhub.core.prefs`)."""
from __future__ import annotations

from mixhub.core import paths
from mixhub.core.prefs import load_preferences


ROOT = paths.ROOT


def load_provider_preferences(preferences_path: str | None = None) -> dict:
    """MixHub runtime `Preferences` JSON (`{}` if missing or malformed): an explicit path, else the shared lookup."""
    return load_preferences(preferences_path)