#!/usr/bin/env python3
"""
`mixhub.tui.core.input`: `InputDecoder` translating `stdscr.getch()` streams into structured `Event`s.

Decodes:
  - SGR mouse sequences `\\x1b[<btn;col;rowM` / `m` (converting 1-based `col;row` into 0-based `x, y`)
  - `?1004h` focus sequences `\\x1b[O` (`FocusOut`) and `\\x1b[I` (`FocusIn`)
  - Normal-mode CSI / SS3 arrow keys `\\x1b[A..D` and `\\x1bOA..D`
  - X10 mouse fallback `\\x1b[M...`
  - Dragged file/image path bursts (`/Users/...`, `~/...`)
  - Bare `Esc` (`Key(27)`)
"""
from __future__ import annotations

import re
import time
from typing import Any, Callable

try:
    import curses
except ImportError:  # pragma: no cover
    curses = None  # type: ignore[assignment]

from mixhub.tui.core.events import (
    DraggedPath,
    Event,
    FocusIn,
    FocusOut,
    Key,
    Mouse,
    MouseKind,
    Resize,
)

_SGR_BODY_RE = re.compile(r"^(\d+);(\d+);(\d+)([Mm])$")

KEY_UP = getattr(curses, "KEY_UP", 259)
KEY_DOWN = getattr(curses, "KEY_DOWN", 258)
KEY_RIGHT = getattr(curses, "KEY_RIGHT", 261)
KEY_LEFT = getattr(curses, "KEY_LEFT", 260)
KEY_RESIZE = getattr(curses, "KEY_RESIZE", 410)
KEY_MOUSE = getattr(curses, "KEY_MOUSE", 409)

_ARROW_MAP: dict[int, int] = {
    ord("A"): KEY_UP,
    ord("B"): KEY_DOWN,
    ord("C"): KEY_RIGHT,
    ord("D"): KEY_LEFT,
}


def classify_sgr_mouse(btn: int, mx: int, my: int, is_release: bool, is_double: bool = False) -> Mouse:
    """Convert raw SGR `(btn, mx, my, is_release)` tuple into a typed `Mouse` event."""
    if btn == 64:
        kind: MouseKind = "wheel_up"
    elif btn == 65:
        kind = "wheel_down"
    elif is_release:
        kind = "release"
    elif btn & 32:
        kind = "drag" if (btn & 3) == 0 else "motion"
    elif (btn & 3) == 0:
        kind = "double" if is_double else "click"
    else:
        kind = "motion"
    return Mouse(kind=kind, x=mx, y=my, button=btn)


class InputDecoder:
    """
    Stateful decoder turning `stdscr.getch()` integers (or raw escape sequences) into `Event` objects.
    Also provides `consume_escape_sequence(stdscr)` and `consume_dragged_path_burst(stdscr, first_ch)`
    for direct compatibility with `HubTUI`.
    """

    def __init__(
        self,
        double_click_window: float = 0.40,
        ungetch_fn: Callable[[int], Any] | None = None,
    ):
        self.double_click_window = double_click_window
        self._ungetch_fn = ungetch_fn
        self._last_click_pos: tuple[int, int] = (-1, -1)
        self._last_click_ts: float = 0.0

    def _ungetch(self, ch: int) -> None:
        if self._ungetch_fn is not None:
            try:
                self._ungetch_fn(ch)
            except Exception:
                pass
        elif curses is not None and hasattr(curses, "ungetch"):
            try:
                curses.ungetch(ch)
            except Exception:
                pass

    def decode_escape_after_27(
        self,
        stdscr: Any,
        *,
        restore_blocking: bool = False,
    ) -> tuple[str, tuple[int, int, int, bool] | int | None]:
        """
        Inspect bytes immediately following `ch == 27` (ESC) on `stdscr`.
        Returns `(kind, payload)`:
          - `("bare_esc", None)`      : lone ESC key
          - `("focus_out", None)`     : `\\x1b[O` tmux/terminal FocusOut
          - `("focus_in", None)`      : `\\x1b[I` tmux/terminal FocusIn
          - `("arrow", key_code)`     : `\\x1b[A..D` or `\\x1bOA..D` cursor key
          - `("sgr_mouse", (btn, mx, my, is_rel))` : SGR mouse report (0-based `mx, my`)
          - `("ignored_seq", None)`   : drained unknown CSI / X10 sequence
        """
        if hasattr(stdscr, "nodelay"):
            try:
                stdscr.nodelay(True)
            except Exception:
                pass
        buf: list[str] = []
        try:
            c1 = stdscr.getch()
            if c1 == -1:
                return ("bare_esc", None)
            if c1 == ord("O"):
                c2 = stdscr.getch()
                if c2 in _ARROW_MAP:
                    return ("arrow", _ARROW_MAP[c2])
                if c2 != -1:
                    self._ungetch(c2)
                self._ungetch(c1)
                return ("bare_esc", None)
            if c1 != ord("["):
                self._ungetch(c1)
                return ("bare_esc", None)
            c2 = stdscr.getch()
            if c2 == ord("O"):
                return ("focus_out", None)
            if c2 == ord("I"):
                return ("focus_in", None)
            if c2 in _ARROW_MAP:
                return ("arrow", _ARROW_MAP[c2])
            if c2 == ord("M"):
                for _ in range(3):
                    if stdscr.getch() == -1:
                        break
                return ("ignored_seq", None)
            if c2 != ord("<"):
                if c2 == -1:
                    return ("bare_esc", None)
                if 0x20 <= c2 <= 0x3F:
                    for _ in range(32):
                        ch = stdscr.getch()
                        if ch == -1 or (0x40 <= ch <= 0x7E):
                            break
                return ("ignored_seq", None)
            while len(buf) < 32:
                ch = stdscr.getch()
                if ch == -1:
                    break
                ch_str = chr(ch) if 0 <= ch < 256 else ""
                buf.append(ch_str)
                if ch_str in ("M", "m"):
                    break
        finally:
            if restore_blocking and hasattr(stdscr, "timeout"):
                try:
                    stdscr.timeout(-1)
                except Exception:
                    pass

        seq = "".join(buf)
        m = _SGR_BODY_RE.match(seq)
        if not m:
            return ("bare_esc", None)
        btn = int(m.group(1))
        mx = max(0, int(m.group(2)) - 1)
        my = max(0, int(m.group(3)) - 1)
        is_rel = m.group(4) == "m"
        return ("sgr_mouse", (btn, mx, my, is_rel))

    def consume_dragged_path_burst(
        self,
        stdscr: Any,
        first_ch: int,
        *,
        restore_blocking: bool = False,
    ) -> str | None:
        """Consume a rapid pasted file path burst starting with `/`, `~`, `'`, or `"`."""
        if first_ch not in (ord("/"), ord("~"), ord("'"), ord('"')):
            return None
        chars = [chr(first_ch)]
        try:
            if hasattr(stdscr, "nodelay"):
                stdscr.nodelay(True)
            deadline = time.monotonic() + 0.012
            while time.monotonic() < deadline and len(chars) < 1024:
                nxt = stdscr.getch()
                if nxt == -1:
                    if len(chars) == 1:
                        time.sleep(0.002)
                        nxt = stdscr.getch()
                        if nxt == -1:
                            break
                    else:
                        break
                if nxt in (10, 13):
                    break
                if 32 <= nxt <= 0x10FFFF:
                    try:
                        chars.append(chr(nxt))
                        deadline = time.monotonic() + 0.006
                    except ValueError:
                        break
                else:
                    break
        except Exception:
            pass
        finally:
            if restore_blocking and hasattr(stdscr, "timeout"):
                try:
                    stdscr.timeout(-1)
                except Exception:
                    pass
        if len(chars) >= 3:
            return "".join(chars)
        if len(chars) == 2:
            self._ungetch(ord(chars[1]))
        return None

    def feed_char(
        self,
        stdscr: Any,
        ch: int,
        *,
        restore_blocking: bool = False,
        now_mono: float | None = None,
    ) -> list[Event]:
        """Decode a single initial `ch = stdscr.getch()` reading any follow-up sequence from `stdscr`."""
        if ch == -1:
            return []
        if ch == KEY_RESIZE:
            return [Resize()]
        if ch == 27:
            kind, payload = self.decode_escape_after_27(stdscr, restore_blocking=restore_blocking)
            if kind == "bare_esc":
                return [Key(27)]
            if kind == "focus_out":
                return [FocusOut()]
            if kind == "focus_in":
                return [FocusIn()]
            if kind == "arrow" and isinstance(payload, int):
                return [Key(payload)]
            if kind == "sgr_mouse" and isinstance(payload, tuple):
                btn, mx, my, is_rel = payload
                now = time.monotonic() if now_mono is None else now_mono
                is_dbl = False
                if not is_rel and (btn & 3) == 0 and not (btn & 32):
                    if (mx, my) == self._last_click_pos and (now - self._last_click_ts) <= self.double_click_window:
                        is_dbl = True
                    self._last_click_pos = (mx, my)
                    self._last_click_ts = now
                return [classify_sgr_mouse(btn, mx, my, is_rel, is_double=is_dbl)]
            return []
        if ch in (ord("/"), ord("~"), ord("'"), ord('"')):
            burst = self.consume_dragged_path_burst(stdscr, ch, restore_blocking=restore_blocking)
            if burst is not None:
                return [DraggedPath(burst)]
        return [Key(ch)]

    def feed(self, stdscr: Any, *, restore_blocking: bool = False) -> list[Event]:
        """Read one event (or sequence) from `stdscr`."""
        ch = stdscr.getch()
        return self.feed_char(stdscr, ch, restore_blocking=restore_blocking)


__all__ = [
    "InputDecoder",
    "KEY_DOWN",
    "KEY_LEFT",
    "KEY_MOUSE",
    "KEY_RESIZE",
    "KEY_RIGHT",
    "KEY_UP",
    "classify_sgr_mouse",
]
