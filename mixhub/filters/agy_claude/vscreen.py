#!/usr/bin/env python3
"""`mixhub.filters.agy_claude.vscreen`: display-width / cursor-column math and the VT100 virtual screen (`VScreen`) replaying `agy` output."""

import codecs
import functools
import re
import unicodedata
from mixhub.core.textwidth import char_width


def _char_disp_width(ch: str) -> int:
    """Delegate character display width to `mixhub.core.textwidth.char_width`."""
    return char_width(ch)


def col_to_char_idx(prompt_text: str, target_col: int) -> int:
    """
    Map a 1-based terminal visual column (`target_col`, where `> ` or `  ` occupies cols 1..2
    and character 0 starts at col 3) to character index `0..len(prompt_text)`.
    For 2-column CJK characters occupying `[col, col + 1]`, clicking either half (`col` or `col + 1`)
    selects that character (`idx`).
    """
    if target_col <= 3 or not prompt_text:
        return 0
    col = 3
    for idx, ch in enumerate(prompt_text):
        cw = _char_disp_width(ch)
        if cw <= 0:
            continue
        if target_col < col + cw:
            return idx
        col += cw
    return len(prompt_text)


def char_idx_to_col(prompt_text: str, char_idx: int) -> int:
    """Map a character index `0..len(prompt_text)` on a prompt line to its 1-based visual column."""
    col = 3
    for ch in prompt_text[:max(0, char_idx)]:
        col += _char_disp_width(ch)
    return col


@functools.lru_cache(maxsize=8192)
def _cell_width(ch: str) -> int:
    """Terminal cell width of one code point (0 for combining marks, ZWJ, variation selectors)."""
    o = ord(ch)
    if o < 0x7F:
        return 1 if o >= 0x20 else 0
    if o < 0xA0 or o in (0x200B, 0x200C, 0x200D, 0x2060, 0xFE0E, 0xFE0F) or 0x1F3FB <= o <= 0x1F3FF:
        return 0
    if unicodedata.category(ch) in ("Mn", "Me", "Cf"):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


class VScreen:
    """
    Minimal xterm screen model replaying `agy`'s raw output. In no-flicker (altscreen) mode agy
    paints the prompt box with cell diffs (`\x1b[L`, bare text at the current cursor, `\x08`), so the
    prompt rows and the caret can only be recovered by replaying the stream onto a grid.
    Cells hold one grapheme string; `""` marks the right half of a wide character.
    """
    _TOKEN_RE = re.compile(
        r"(?P<text>[^\x00-\x1f\x7f\x1b]+)"
        r"|\x1b\[(?P<cp>[0-?]*)(?P<ci>[ -/]*)(?P<cf>[@-~])"
        r"|\x1b[\]P_^X][^\x07\x1b]*(?:\x07|\x1b\\)"
        r"|\x1b(?P<ei>[ -/]*)(?P<ef>[0-OQ-WYZ\\`-~])"
        r"|(?P<ctl>[\x00-\x1a\x1c-\x1f\x7f])"
    )
    _PARTIAL_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*|[\]P_^X][^\x07\x1b]*\x1b?|[ -/]*)\Z")
    LOGGED_MODES = frozenset((47, 1047, 1049, 1000, 1002, 1003, 1005, 1006, 1015, 1004, 2004))

    def __init__(self, rows: int = 40, cols: int = 100):
        self.rows = max(1, rows)
        self.cols = max(4, cols)
        self._dec = codecs.getincrementaldecoder("utf-8")("replace")
        self._carry = ""
        self.main = self._blank_rows(self.rows)
        self.alt = self._blank_rows(self.rows)
        self.buf = self.main
        self.alt_active = False
        self.ever_alt = False
        self.cursor_known = False
        self.y = 0
        self.x = 0
        self.wrap_pending = False
        self.autowrap = True
        self.origin = False
        self.top = 0
        self.bottom = self.rows - 1
        self.cursor_visible = True
        self.bracketed_paste = False
        self.mouse_tracking: set[int] = set()
        self.mouse_encoding: set[int] = set()
        self.shown_caret: tuple[int, int] | None = None
        self.show_seq = 0
        self.last_char = " "
        self._saved: dict[bool, tuple] = {}
        self.on_mode = None

    def _blank_rows(self, n: int) -> list[list[str]]:
        return [[" "] * self.cols for _ in range(n)]

    def resize(self, rows: int, cols: int) -> None:
        rows, cols = max(1, rows), max(4, cols)
        if (rows, cols) == (self.rows, self.cols):
            return
        self.cols = cols
        for b in (self.main, self.alt):
            for r in b:
                if len(r) > cols:
                    del r[cols:]
                    if r[-1] and _cell_width(r[-1][0]) == 2:
                        r[-1] = " "
                else:
                    r.extend([" "] * (cols - len(r)))
            if len(b) > rows:
                del b[rows:]
            else:
                b.extend(self._blank_rows(rows - len(b)))
        self.rows = rows
        self.top, self.bottom = 0, rows - 1
        self.y, self.x = min(self.y, rows - 1), min(self.x, cols - 1)
        self.wrap_pending = False
        self.shown_caret = None

    def app_mouse_on(self) -> bool:
        return bool(self.mouse_tracking)

    def feed(self, data: bytes) -> None:
        if not data:
            return
        s = self._carry + self._dec.decode(data)
        self._carry = ""
        n = len(s)
        pos = 0
        match = self._TOKEN_RE.match
        while pos < n:
            m = match(s, pos)
            if m is None:
                if self._PARTIAL_RE.match(s, pos):
                    if n - pos <= 65536:
                        self._carry = s[pos:]
                    break
                pos += 1  # stray ESC
                continue
            pos = m.end()
            g = m.lastgroup
            if g == "text":
                self._print(m.group("text"))
            elif g == "cf":
                self._csi(m.group("cp"), m.group("ci"), m.group("cf"))
            elif g == "ef":
                self._esc(m.group("ei"), m.group("ef"))
            elif g == "ctl":
                self._ctl(m.group("ctl"))
        if self.alt_active and self.cursor_visible and self.cursor_known:
            self.shown_caret = (self.y, self.x)

    # -- printing --
    def _fix_span(self, row: list[str], a: int, b: int) -> None:
        if 0 < a < self.cols and row[a] == "":
            row[a - 1] = " "
        if b < self.cols and row[b] == "":
            row[b] = " "

    def _wrap_line(self) -> None:
        self.wrap_pending = False
        if self.autowrap:
            self.x = 0
            self._index()

    def _print(self, s: str) -> None:
        if s.isascii():
            self._print_ascii(s)
            self.last_char = s[-1]
            return
        for ch in s:
            w = _cell_width(ch)
            if w:
                self._put(ch, w)
                self.last_char = ch
            else:
                self._attach(ch)

    def _print_ascii(self, s: str) -> None:
        cols = self.cols
        i, n = 0, len(s)
        while i < n:
            if self.wrap_pending:
                self._wrap_line()
            row = self.buf[self.y]
            x = self.x
            space = cols - x
            if not self.autowrap and n - i > space:
                if space > 1:
                    self._fix_span(row, x, cols - 1)
                    row[x:cols - 1] = s[i:i + space - 1]
                self._fix_span(row, cols - 1, cols)
                row[cols - 1] = s[n - 1]
                self.x = cols - 1
                return
            k = min(space, n - i)
            self._fix_span(row, x, x + k)
            row[x:x + k] = s[i:i + k]
            i += k
            if x + k >= cols:
                self.x = cols - 1
                self.wrap_pending = self.autowrap
            else:
                self.x = x + k

    def _put(self, ch: str, w: int) -> None:
        if self.wrap_pending:
            self._wrap_line()
        cols = self.cols
        if w == 2 and self.x >= cols - 1:
            if not self.autowrap:
                return
            row = self.buf[self.y]
            self._fix_span(row, self.x, self.x + 1)
            row[self.x] = " "
            self.x = 0
            self._index()
        row = self.buf[self.y]
        x = self.x
        self._fix_span(row, x, x + w)
        row[x] = ch
        if w == 2:
            row[x + 1] = ""
        if x + w >= cols:
            self.x = cols - 1
            self.wrap_pending = self.autowrap
        else:
            self.x = x + w

    def _attach(self, ch: str) -> None:
        row = self.buf[self.y]
        x = self.x if self.wrap_pending else self.x - 1
        if x >= 0 and row[x] == "":
            x -= 1
        if x >= 0:
            row[x] += ch

    # -- scrolling / lines --
    def _index(self) -> None:
        if self.y == self.bottom:
            self._scroll_up(1)
        elif self.y < self.rows - 1:
            self.y += 1

    def _rindex(self) -> None:
        if self.y == self.top:
            self._scroll_down(1)
        elif self.y > 0:
            self.y -= 1

    def _scroll_up(self, n: int, top: int | None = None) -> None:
        top = self.top if top is None else top
        bot = self.bottom
        n = max(0, min(n, bot - top + 1))
        if n:
            del self.buf[top:top + n]
            self.buf[bot - n + 1:bot - n + 1] = self._blank_rows(n)

    def _scroll_down(self, n: int, top: int | None = None) -> None:
        top = self.top if top is None else top
        bot = self.bottom
        n = max(0, min(n, bot - top + 1))
        if n:
            del self.buf[bot - n + 1:bot + 1]
            self.buf[top:top] = self._blank_rows(n)

    def _erase(self, y: int, a: int, b: int) -> None:
        row = self.buf[y]
        a, b = max(0, a), min(self.cols, b)
        if a < b:
            self._fix_span(row, a, b)
            row[a:b] = [" "] * (b - a)

    def _goto(self, y: int, x: int) -> None:
        if self.origin:
            y = max(self.top, min(self.bottom, y + self.top))
        else:
            y = max(0, min(self.rows - 1, y))
        self.y, self.x = y, max(0, min(self.cols - 1, x))
        self.wrap_pending = False

    # -- control sequences --
    def _ctl(self, c: str) -> None:
        if c == "\r":
            self.x = 0
            self.wrap_pending = False
        elif c in "\n\x0b\x0c":
            self.wrap_pending = False
            self._index()
        elif c == "\x08":
            self.wrap_pending = False
            self.x = max(0, self.x - 1)
        elif c == "\t":
            self.x = min(self.cols - 1, (self.x // 8 + 1) * 8)
            self.wrap_pending = False

    def _esc(self, ei: str, ef: str) -> None:
        if ei:
            return  # charset designation and friends
        if ef == "7":
            self._saved[self.alt_active] = (self.y, self.x, self.origin, self.wrap_pending)
        elif ef == "8":
            self._restore()
        elif ef == "D":
            self.wrap_pending = False
            self._index()
        elif ef == "E":
            self.x = 0
            self.wrap_pending = False
            self._index()
        elif ef == "M":
            self.wrap_pending = False
            self._rindex()
        elif ef == "c":
            self._reset()

    def _restore(self) -> None:
        st = self._saved.get(self.alt_active)
        if st is None:
            self._goto(0, 0)
            return
        self.y, self.x, self.origin, self.wrap_pending = st
        self.y, self.x = min(self.y, self.rows - 1), min(self.x, self.cols - 1)

    def _reset(self) -> None:
        self.main, self.alt = self._blank_rows(self.rows), self._blank_rows(self.rows)
        self.buf = self.main
        was_alt = self.alt_active
        self.alt_active = False
        self.y = self.x = 0
        self.cursor_known = True
        self.wrap_pending = False
        self.autowrap, self.origin, self.cursor_visible, self.bracketed_paste = True, False, True, False
        self.top, self.bottom = 0, self.rows - 1
        self.mouse_tracking.clear()
        self.mouse_encoding.clear()
        self.shown_caret = None
        self._saved.clear()
        if self.on_mode:
            self.on_mode("RIS", True, was_alt)

    def _csi(self, cp: str, ci: str, cf: str) -> None:
        if cf == "m":
            return
        priv = cp[:1] if cp[:1] in "<=>?" else ""
        if ci:
            if ci == "!" and cf == "p":  # DECSTR
                self.top, self.bottom = 0, self.rows - 1
                self.origin = False
                self.cursor_visible = True
                self.wrap_pending = False
                if self.on_mode:
                    self.on_mode("DECSTR", True, self.alt_active)
            return
        body = cp[1:] if priv else cp
        if priv == "?":
            if cf in "hl":
                for p in body.split(";"):
                    if p.isdigit():
                        self._decset(int(p), cf == "h")
            return
        if priv:
            return  # kitty keyboard (`>1u`, `<u`, `?u`), modifyOtherKeys, XTVERSION, ...
        ps = [int(p) if p.isdigit() else 0 for p in (q.split(":", 1)[0] for q in body.split(";"))] if body else []
        n = (ps[0] if ps else 0) or 1
        if cf in "Hf":
            self._goto(((ps[0] if ps else 0) or 1) - 1, ((ps[1] if len(ps) > 1 else 0) or 1) - 1)
            self.cursor_known = True
        elif cf == "A" or cf == "F":
            lo = self.top if self.y >= self.top else 0
            self.y = max(lo, self.y - n)
            self.wrap_pending = False
            if cf == "F":
                self.x = 0
        elif cf in "BeE":
            hi = self.bottom if self.y <= self.bottom else self.rows - 1
            self.y = min(hi, self.y + n)
            self.wrap_pending = False
            if cf == "E":
                self.x = 0
        elif cf in "Ca":
            self.x = min(self.cols - 1, self.x + n)
            self.wrap_pending = False
        elif cf == "D":
            self.x = max(0, self.x - n)
            self.wrap_pending = False
        elif cf in "G`":
            self.x = max(0, min(self.cols - 1, n - 1))
            self.wrap_pending = False
        elif cf == "d":
            self._goto(n - 1, self.x)
        elif cf == "J":
            mode = ps[0] if ps else 0
            if mode == 0:
                self._erase(self.y, self.x, self.cols)
                for yy in range(self.y + 1, self.rows):
                    self._erase(yy, 0, self.cols)
            elif mode == 1:
                for yy in range(0, self.y):
                    self._erase(yy, 0, self.cols)
                self._erase(self.y, 0, self.x + 1)
            else:
                for yy in range(self.rows):
                    self._erase(yy, 0, self.cols)
        elif cf == "K":
            mode = ps[0] if ps else 0
            self.wrap_pending = False
            if mode == 0:
                self._erase(self.y, self.x, self.cols)
            elif mode == 1:
                self._erase(self.y, 0, self.x + 1)
            else:
                self._erase(self.y, 0, self.cols)
        elif cf == "X":
            self.wrap_pending = False
            self._erase(self.y, self.x, self.x + n)
        elif cf == "@":
            row, x = self.buf[self.y], self.x
            n = min(n, self.cols - x)
            self._fix_span(row, x, x)
            row[x:x] = [" "] * n
            del row[self.cols:]
            if row[-1] and _cell_width(row[-1][0]) == 2:
                row[-1] = " "
        elif cf == "P":
            row, x = self.buf[self.y], self.x
            n = min(n, self.cols - x)
            self._fix_span(row, x, x + n)
            del row[x:x + n]
            row.extend([" "] * n)
        elif cf == "L" or cf == "M":
            if self.top <= self.y <= self.bottom:
                (self._scroll_down if cf == "L" else self._scroll_up)(n, top=self.y)
                self.x = 0
                self.wrap_pending = False
        elif cf == "S":
            self._scroll_up(n)
        elif cf == "T":
            if len(ps) <= 1:
                self._scroll_down(n)
        elif cf == "r":
            t = ((ps[0] if ps else 0) or 1) - 1
            b = ((ps[1] if len(ps) > 1 else 0) or self.rows) - 1
            if 0 <= t < b < self.rows:
                self.top, self.bottom = t, b
                self._goto(0, 0)
        elif cf == "s":
            if not ps:
                self._saved[self.alt_active] = (self.y, self.x, self.origin, self.wrap_pending)
        elif cf == "u":
            if not ps:
                self._restore()
        elif cf == "b":
            self._print(self.last_char * min(n, self.rows * self.cols))
        elif cf == "I":
            for _ in range(n):
                self._ctl("\t")
        elif cf == "Z":
            self.x = max(0, ((self.x - 1) // 8 - (n - 1)) * 8)
            self.wrap_pending = False

    def _decset(self, p: int, on: bool) -> None:
        if p == 25:
            self.cursor_visible = on
            if on and self.alt_active and self.cursor_known:
                self.shown_caret = (self.y, self.x)
                self.show_seq += 1
            return
        prev_alt = self.alt_active
        prev_track = set(self.mouse_tracking)
        prev_enc = set(self.mouse_encoding)
        prev_paste = self.bracketed_paste
        if p == 7:
            self.autowrap = on
        elif p == 6:
            self.origin = on
            self._goto(0, 0)
        elif p in (1049, 1047, 47):
            self._switch_screen(p, on)
        elif p in (1000, 1002, 1003):
            # xterm keeps a single tracking mode: setting one replaces it, resetting any turns tracking off
            self.mouse_tracking = {p} if on else set()
        elif p in (1005, 1006, 1015):
            (self.mouse_encoding.add if on else self.mouse_encoding.discard)(p)
        elif p == 2004:
            self.bracketed_paste = on
        changed = (
            self.alt_active != prev_alt
            or self.mouse_tracking != prev_track
            or self.mouse_encoding != prev_enc
            or self.bracketed_paste != prev_paste
        )
        if changed and self.on_mode and p in self.LOGGED_MODES:
            self.on_mode(p, on, self.alt_active)

    def _switch_screen(self, p: int, on: bool) -> None:
        if on:
            if self.alt_active:
                return
            if p == 1049:
                self._saved[False] = (self.y, self.x, self.origin, self.wrap_pending)
                for r in self.alt:
                    r[:] = [" "] * self.cols
            self.buf = self.alt
            self.alt_active = True
            self.ever_alt = True
            self.cursor_known = False
            self.shown_caret = None
        else:
            if not self.alt_active:
                return
            if p == 1047:
                for r in self.alt:
                    r[:] = [" "] * self.cols
            self.buf = self.main
            self.alt_active = False
            self.shown_caret = None
            if p == 1049:
                self._restore()
