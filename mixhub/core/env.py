"""Settings from the environment: `MIXHUB_<NAME>`, with the older `HUB_<NAME>` still honored.

`MIXHUB_PORT`, `MIXHUB_NO_OPEN`, `MIXHUB_PYTHON`, ... `MIXHUB_AGENT_HOME` is the home directory whose agent
sessions are listed (default: your home); its old name was `HUB_HOME`.
"""
from __future__ import annotations

import os

_OLD_NAMES = {"AGENT_HOME": "HUB_HOME"}


def env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get("MIXHUB_" + name)
    if value is None:
        value = os.environ.get(_OLD_NAMES.get(name, "HUB_" + name))
    return default if value is None else value
