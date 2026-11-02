#!/usr/bin/env python3
"""`mixhub.filters.agy_claude.mouse`: stdin mouse translation (`filter_stdin_mouse`), window-size sync and the selection clearer (`_SelClear`)."""

try:
    import fcntl
except ImportError:
    fcntl = None
import re
import struct

try:
    import termios
except ImportError:
    termios = None
import time

from mixhub.filters.agy_claude.cursor import PromptCursorTracker
from mixhub.filters.agy_claude.patterns import DSR_REPLY_RE, MOUSE_EVENT_RE


def filter_stdin_mouse(in_bytes: bytes, tracker: PromptCursorTracker) -> bytes:
    """
    Intercept SGR (`\x1b[<btn;col;rowM/m`) and X10 (`\x1b[M<cb><cx><cy>`) mouse sequences
    and DSR replies (`\x1b[row;colR`), swallowing non-left-press mouse events so zero mouse
    bytes ever leak into `agy`.
    """
    if tracker.disabled:
        return in_bytes
    tracker._apply_pending_size()
    if tracker.vs.alt_active:
        return tracker.filter_alt_input(in_bytes)

    if b"\x1b[" in in_bytes and b"R" in in_bytes:
        for dm in DSR_REPLY_RE.finditer(in_bytes):
            dsr_row = int(dm.group(1))
            if 1 <= dsr_row <= tracker.term_rows:
                tracker.last_prompt_row = max(1, dsr_row - tracker.cur_line_idx)
        if tracker._dsr_inflight > 0:
            # Only swallow replies to the filter's own `\x1b[6n` (Shift+F3 is also `\x1b[1;2R`)
            def _take(dm: "re.Match[bytes]") -> bytes:
                if tracker._dsr_inflight > 0:
                    tracker._dsr_inflight -= 1
                    tracker._log("dsr", reply=dm.group(0), action="stripped")
                    return b""
                return dm.group(0)
            in_bytes = DSR_REPLY_RE.sub(_take, in_bytes)
        if not in_bytes:
            return b""

    if b"\x1b[<" not in in_bytes and b"\x1b[M" not in in_bytes:
        return tracker.filter_inline_keys(in_bytes)

    out = bytearray()
    last_end = 0
    for m in MOUSE_EVENT_RE.finditer(in_bytes):
        seg = in_bytes[last_end:m.start()]
        if seg:
            out.extend(tracker.filter_inline_keys(seg))
        last_end = m.end()
        if tracker._in_paste:
            out.extend(m.group(0))
            continue
        if m.group(1) is not None:
            btn = int(m.group(1))
            col = int(m.group(2))
            row = int(m.group(3))
            act = m.group(4)
            is_left_press = (btn == 0 and act == b"M")
            is_left_release = (btn == 0 and act == b"m")
        else:
            btn = m.group(5)[0] - 32
            col = m.group(6)[0] - 32
            row = m.group(7)[0] - 32
            is_left_press = (btn == 0)
            is_left_release = (btn == 3)
        if is_left_press:
            arrows = tracker.arrows_for_click(col, row)
            if arrows:
                out.extend(arrows)
        elif is_left_release:
            if tracker._inline_drag_start is not None:
                idx0 = tracker._inline_drag_start
                tracker._inline_drag_start = None
                arrows = tracker.finish_inline_drag(idx0, col, row)
                if arrows:
                    out.extend(arrows)
        elif btn != 32:
            tracker._inline_drag_start = None
            tracker.clear_prompt_sel()
    tail_seg = in_bytes[last_end:]
    if tail_seg:
        out.extend(tracker.filter_inline_keys(tail_seg))
    return bytes(out)


def sync_winsize(src_fd: int, dst_fd: int, tracker: PromptCursorTracker | None = None) -> None:
    try:
        ws = fcntl.ioctl(src_fd, termios.TIOCGWINSZ, b"\x00" * 8)
        rows, cols, _, _ = struct.unpack("HHHH", ws)
        if tracker is not None and rows > 0 and cols > 0:
            tracker.term_rows = rows
            tracker.term_cols = cols
            tracker._pending_size = (rows, cols)  # applied by the main loop, never inside the signal handler
        fcntl.ioctl(dst_fd, termios.TIOCSWINSZ, ws)
    except OSError:
        pass


_SEL_CLEAR_DELAY = 0.10
_SEL_CLEAR_SAME_CELL_DELAY = 0.60
_SEL_CLEAR_BOTTOM_GUARD = 8
_DECSET_RE = re.compile(rb"\x1b\[\?([0-9;]+)([hl])")
_SGR_MOUSE_RE = re.compile(rb"\x1b\[<(\d+);(\d+);(\d+)([Mm])")
_ALT_MODES = {47, 1047, 1049}
_MOUSE_MODES = {1000, 1002, 1003}


class _SelClear:
    """Auto-clear agy altScreen light-blue selection highlight 100ms after drag-to-copy release."""

    def __init__(self) -> None:
        self.modes: set[int] = set()
        self._carry = b""
        self._down: tuple[int, int] | None = None
        self._dragged = False
        self._due: float | None = None
        self._pos: tuple[int, int] | None = None

    def on_output(self, data: bytes) -> None:
        buf = self._carry + data
        for m in _DECSET_RE.finditer(buf):
            on = m.group(2) == b"h"
            for p in m.group(1).split(b";"):
                if p:
                    (self.modes.add if on else self.modes.discard)(int(p))
        if b"\x1bc" in buf:
            self.modes.clear()
        i = buf.rfind(b"\x1b")
        self._carry = buf[i:] if i >= 0 and len(buf) - i < 32 else b""

    def _mouse_ok(self) -> bool:
        return bool(1006 in self.modes and (self.modes & _MOUSE_MODES) and (self.modes & _ALT_MODES))

    def on_stdin_bytes(self, data: bytes, rows: int, write_fn, prompt_sel_active: bool = False) -> None:
        if not self._mouse_ok():
            return
        pos = 0
        for m in _SGR_MOUSE_RE.finditer(data):
            if m.start() > pos:
                self.on_key(write_fn)
            pos = m.end()
            b = int(m.group(1))
            x = int(m.group(2))
            y = int(m.group(3))
            release = m.group(4) == b"m"
            self.on_mouse(b, x, y, release, rows, prompt_sel_active=prompt_sel_active)
        if pos < len(data):
            self.on_key(write_fn)

    def on_mouse(self, b: int, x: int, y: int, release: bool, rows: int, prompt_sel_active: bool = False) -> None:
        if b & 64:
            return
        if not release and b == 0:
            self._due = None
            self._down, self._dragged = (x, y), False
        elif not release and b == 32:
            if self._down and (x, y) != self._down:
                self._dragged = True
        elif release and b == 0 and self._down:
            if self._dragged and not prompt_sel_active and y <= rows - _SEL_CLEAR_BOTTOM_GUARD:
                delay = _SEL_CLEAR_SAME_CELL_DELAY if (x, y) == self._down else _SEL_CLEAR_DELAY
                self._pos, self._due = (x, y), time.monotonic() + delay
            self._down, self._dragged = None, False
        else:
            self._due = None
            self._down, self._dragged = None, False

    def on_key(self, write_fn) -> None:
        if self._due is not None:
            self._fire(write_fn)

    def on_winch(self) -> None:
        self._due = None
        self._down, self._dragged = None, False

    def next_timeout(self) -> float | None:
        return None if self._due is None else max(0.0, self._due - time.monotonic())

    def on_timeout(self, write_fn) -> None:
        if self._due is not None and time.monotonic() >= self._due:
            self._fire(write_fn)

    def _fire(self, write_fn) -> None:
        self._due = None
        if not self._mouse_ok() or self._pos is None:
            return
        x, y = self._pos
        write_fn(b"\x1b[<0;%d;%dM\x1b[<0;%d;%dm" % (x, y, x, y))
