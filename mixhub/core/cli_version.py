#!/usr/bin/env python3
"""`mixhub.core.cli_version`: Installed Claude Code version against the version a live session started with.

Only a hint: a session on an older build keeps running it until it is restarted, and MixHub never restarts one
itself (a restart drops Remote Control, and Claude Code refuses `/restart` after a `cd`).
"""
from __future__ import annotations

import os
import re
import shutil
import time

_VERSION_RE = re.compile(r"^\d+(\.\d+)+$")
_CACHE_SEC = 30.0
_installed_cache: dict[str, tuple[float, str]] = {}


def installed_claude_version() -> str:
    """Version of the `claude` on PATH, read from the native installer's `versions/<x.y.z>` symlink target.
    Empty when it can't be told without running the binary (npm installs, wrappers)."""
    now = time.monotonic()
    hit = _installed_cache.get("claude")
    if hit and now - hit[0] < _CACHE_SEC:
        return hit[1]
    ver = ""
    path = shutil.which("claude")
    if path:
        name = os.path.basename(os.path.realpath(path))
        if _VERSION_RE.match(name):
            ver = name
    _installed_cache["claude"] = (now, ver)
    return ver


def _as_tuple(ver: str) -> tuple[int, ...]:
    return tuple(int(x) for x in ver.split("."))


def old_version_hint(s: dict) -> str:
    """`old 2.1.316 · 2.1.318 installed` for a Claude session started on an older build, else empty."""
    if not s or s.get("tool") != "claude":
        return ""
    running = str(s.get("cliVersion") or "")
    if not _VERSION_RE.match(running):
        return ""
    installed = installed_claude_version()
    if not installed or _as_tuple(running) >= _as_tuple(installed):
        return ""
    return f"old {running} · {installed} installed"
