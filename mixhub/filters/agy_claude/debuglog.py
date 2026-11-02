#!/usr/bin/env python3
"""`mixhub.filters.agy_claude.debuglog`: the mouse diagnostics ring buffer (`MouseDebugLog`, `AGY_MOUSE_DEBUG` / `AGY_MOUSE_DEBUG_PATH`)."""

import os
import time
from collections import deque

from mixhub.core.env import env


MOUSE_DEBUG_PATH = os.environ.get("AGY_MOUSE_DEBUG_PATH", "/tmp/agy-mouse-debug.log")


class MouseDebugLog:
    """
    In-memory ring of mouse / screen-mode events for the current `agy` session, dumped (mode 0600) to
    `/tmp/agy-mouse-debug.log` shortly after each click, on SIGUSR1 and at exit. Prompt text is only
    recorded at AGY_MOUSE_DEBUG=2; level 1 keeps lengths and coordinates.
    """
    def __init__(self, path: str, level: int = 1, maxlen: int = 600):
        self.path = path
        self.level = level
        self.ring: deque[str] = deque(maxlen=maxlen)
        self.header: list[str] = []
        self.t0 = time.monotonic()
        self.dirty = False
        self._last_flush = 0.0

    @classmethod
    def from_env(cls) -> "MouseDebugLog | None":
        raw = os.environ.get("AGY_MOUSE_DEBUG") or env("MOUSE_DEBUG") or "0"
        lvl = raw.strip().lower()
        if lvl in ("", "0", "off", "false", "no"):
            return None
        return cls(MOUSE_DEBUG_PATH, 2 if lvl == "2" else 1)

    def log(self, kind: str, **kw) -> None:
        fields = " ".join(f"{k}={v!r}" if isinstance(v, (str, bytes)) else f"{k}={v}" for k, v in kw.items())
        self.ring.append(f"{time.monotonic() - self.t0:9.3f} {kind:<9} {fields}")
        self.dirty = True

    def flush(self, reason: str = "") -> None:
        tmp = f"{self.path}.{os.getpid()}.tmp"
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
            with os.fdopen(fd, "w", encoding="utf-8", errors="replace") as fh:
                fh.write("\n".join(self.header) + "\n")
                fh.write(f"# dump: {reason} at +{time.monotonic() - self.t0:.3f}s, {len(self.ring)} events (ring {self.ring.maxlen})\n")
                fh.write("\n".join(self.ring) + "\n")
            os.replace(tmp, self.path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        self.dirty = False
        self._last_flush = time.monotonic()

    def maybe_flush(self) -> None:
        if self.dirty and time.monotonic() - self._last_flush >= 0.25:
            self.flush("auto")
