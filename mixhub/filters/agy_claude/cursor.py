#!/usr/bin/env python3
"""`mixhub.filters.agy_claude.cursor`: prompt box cursor tracker (`PromptCursorTracker`) for click-to-move, drag selection and key filtering."""

import re
import time

from mixhub.filters.agy_claude.debuglog import MouseDebugLog
from mixhub.filters.agy_claude.patterns import (
    ASCII_BS_RE,
    ASCII_FIRST_RE,
    ASCII_INC_RE,
    CURSOR_STEP_RE,
    FOOTER_CURSOR_RE,
    FOOTER_END_RE,
    INCREMENTAL_WRAP_RE,
    INPUT_MOUSE_PREFIX_RE,
    MORE_LINES_RE,
    MOUSE_EVENT_RE,
    PASTE_SPLIT_RE,
    PROMPT_CONT_RE,
    PROMPT_HEAD_RE,
    UP_ESCAPE_RE,
)
from mixhub.filters.agy_claude.vscreen import _cell_width, _char_disp_width, char_idx_to_col, col_to_char_idx, VScreen


class PromptCursorTracker:
    """
    Tracks single-line and multi-line wrapped prompt text and cursor position from both stdin
    keystrokes and `agy` ANSI stdout, converting visual `(row, col)` mouse clicks into exact
    `\x1b[D` / `\x1b[C` character steps.
    """
    def __init__(self):
        self.text = ""
        self.lines: list[str] = [""]
        self.top_row: int | None = None
        self.cur_row: int | None = None
        self.cur_col: int = 3
        self.cur_idx: int = 0
        self.cur_line_idx: int = 0
        self.last_prompt_row: int | None = None
        self.term_rows = 40
        self.term_cols = 100
        self._tail_buf = b""
        self._stdin_utf8_buf = b""
        self._stdin_esc_buf = b""
        self._stdin_esc_ts = 0.0
        self._in_paste = False
        self.need_dsr = False
        self._last_footer_down = 4
        self._pending_stdin_left = 0
        self._pending_stdin_chars = 0
        self._allow_output_shrink = False
        # Screen replay (authoritative in altscreen mode) + altscreen gesture / verification state
        self.vs = VScreen(self.term_rows, self.term_cols)
        self.vs.on_mode = self._on_mode
        self._pending_size: tuple[int, int] | None = None
        self._dsr_inflight = 0
        self._last_output_t = 0.0
        self._held: dict | None = None
        self._drag_origin: tuple[int, int] | None = None
        self._fwd_gesture = False
        self._verify: dict | None = None
        self._in_carry = b""
        self._in_carry_t = 0.0
        self._alt_paste = False
        self.prompt_sel: tuple[int, int] | None = None
        self._alt_sel_meta: dict | None = None
        self._inline_drag_start: int | None = None
        self.dbg: MouseDebugLog | None = None
        self.disabled = False  # set after an internal error: everything passes through untouched

    @property
    def _alt_screen(self) -> bool:
        return self.vs.alt_active

    def _log(self, kind: str, **kw) -> None:
        if self.dbg is not None:
            self.dbg.log(kind, **kw)

    def _on_mode(self, p, on: bool, alt: bool) -> None:
        self._log("mode", p=p, on=on, alt=alt, app_mouse=sorted(self.vs.mouse_tracking), enc=sorted(self.vs.mouse_encoding))

    def _apply_pending_size(self) -> None:
        size = self._pending_size
        if size:
            self._pending_size = None
            self.vs.resize(size[0], size[1])
            self._held = None
            self._drag_origin = None
            self._verify = None
            self.clear_prompt_sel()
            self._log("winsize", rows=size[0], cols=size[1])

    def clear_prompt_sel(self) -> None:
        self.prompt_sel = None
        self._alt_sel_meta = None
        self._inline_drag_start = None

    def next_timeout(self) -> float | None:
        if self._verify is not None or self._in_carry:
            return 0.02
        if self.dbg is not None and self.dbg.dirty:
            return 0.3
        return None

    def reset(self) -> None:
        self.text = ""
        self.lines = [""]
        self.top_row = None
        self.cur_row = None
        self.cur_col = 3
        self.cur_idx = 0
        self.cur_line_idx = 0
        self.last_prompt_row = None
        self._stdin_utf8_buf = b""
        self._stdin_esc_buf = b""
        self._stdin_esc_ts = 0.0
        self._in_paste = False
        self._pending_stdin_left = 0
        self._pending_stdin_chars = 0
        self._allow_output_shrink = False
        self._drag_origin = None
        self.clear_prompt_sel()

    def _sync_cursor_from_idx(self) -> None:
        if not self.lines:
            self.lines = [""]
        self.cur_idx = max(0, min(len(self.text), self.cur_idx))
        rem = self.cur_idx
        for i, line in enumerate(self.lines):
            if rem < len(line) or i == len(self.lines) - 1:
                self.cur_line_idx = i
                self.cur_col = char_idx_to_col(line, rem)
                break
            rem -= len(line)
        if self.top_row is not None:
            self.cur_row = self.top_row + self.cur_line_idx

    def _recompute_lines(self) -> None:
        if not self.text:
            self.lines = [""]
            self.cur_idx = 0
            self.cur_line_idx = 0
            self.cur_col = 3
            if self.top_row is not None:
                self.cur_row = self.top_row
            return
        avail_w = max(10, self.term_cols - 2)
        lines: list[str] = []
        cur_line: list[str] = []
        cur_w = 0
        for ch in self.text:
            if ch == "\n":
                lines.append("".join(cur_line))
                cur_line = []
                cur_w = 0
                continue
            cw = _char_disp_width(ch)
            if cur_w + cw > avail_w and cur_line:
                lines.append("".join(cur_line))
                cur_line = [ch]
                cur_w = cw
            else:
                cur_line.append(ch)
                cur_w += cw
        lines.append("".join(cur_line))
        self.lines = lines or [""]
        self._sync_cursor_from_idx()

    def _move_word_left(self) -> None:
        while self.cur_idx > 0 and self.text[self.cur_idx - 1].isspace():
            self.cur_idx -= 1
        while self.cur_idx > 0 and not self.text[self.cur_idx - 1].isspace():
            self.cur_idx -= 1
        self._sync_cursor_from_idx()

    def _move_word_right(self) -> None:
        n = len(self.text)
        while self.cur_idx < n and not self.text[self.cur_idx].isspace():
            self.cur_idx += 1
        while self.cur_idx < n and self.text[self.cur_idx].isspace():
            self.cur_idx += 1
        self._sync_cursor_from_idx()

    def _delete_word_left(self) -> bool:
        old = self.cur_idx
        while self.cur_idx > 0 and self.text[self.cur_idx - 1].isspace():
            self.cur_idx -= 1
        while self.cur_idx > 0 and not self.text[self.cur_idx - 1].isspace():
            self.cur_idx -= 1
        if self.cur_idx < old:
            self.text = self.text[: self.cur_idx] + self.text[old:]
            return True
        return False

    def feed_stdin(self, data: bytes) -> None:
        if not data:
            return
        now = time.monotonic()
        if self._stdin_esc_buf:
            if now - self._stdin_esc_ts <= 0.05 and len(self._stdin_esc_buf) <= 512:
                data = self._stdin_esc_buf + data
            self._stdin_esc_buf = b""
        buf = self._stdin_utf8_buf + data
        self._stdin_utf8_buf = b""
        i = 0
        n = len(buf)
        text_changed = False
        while i < n:
            b = buf[i]
            if b == 0x1B:
                if i + 1 >= n:
                    self._stdin_esc_buf = buf[i:]
                    self._stdin_esc_ts = now
                    break
                nxt = buf[i + 1]
                if nxt == ord("["):
                    j = i + 2
                    while j < n and not (0x40 <= buf[j] <= 0x7E):
                        j += 1
                    if j >= n:
                        if n - i <= 256:
                            self._stdin_esc_buf = buf[i:]
                            self._stdin_esc_ts = now
                        break
                    seq = buf[i : j + 1]
                    i = j + 1
                    if seq == b"\x1b[200~":
                        self._in_paste = True
                    elif seq == b"\x1b[201~":
                        self._in_paste = False
                    elif seq == b"\x1b[D":
                        if self.cur_idx > 0:
                            self.cur_idx -= 1
                            self._pending_stdin_left += 1
                            self._sync_cursor_from_idx()
                    elif seq == b"\x1b[C":
                        self._pending_stdin_left = 0
                        if self.cur_idx < len(self.text):
                            self.cur_idx += 1
                            self._sync_cursor_from_idx()
                    elif seq in (b"\x1b[H", b"\x1b[1~", b"\x1b[7~"):
                        self._pending_stdin_left = 0
                        self.cur_idx = 0
                        self._sync_cursor_from_idx()
                    elif seq in (b"\x1b[F", b"\x1b[4~", b"\x1b[8~"):
                        self._pending_stdin_left = 0
                        self.cur_idx = len(self.text)
                        self._sync_cursor_from_idx()
                    elif seq == b"\x1b[3~":
                        self._pending_stdin_left = 0
                        if 0 <= self.cur_idx < len(self.text):
                            self.text = self.text[: self.cur_idx] + self.text[self.cur_idx + 1 :]
                            text_changed = True
                    elif seq in (b"\x1b[1;3D", b"\x1b[1;5D"):
                        self._pending_stdin_left = 0
                        self._move_word_left()
                    elif seq in (b"\x1b[1;3C", b"\x1b[1;5C"):
                        self._pending_stdin_left = 0
                        self._move_word_right()
                    elif seq in (b"\x1b[A", b"\x1b[B"):
                        self._pending_stdin_left = 0
                        self._allow_output_shrink = True
                    continue
                if nxt in (ord("]"), ord("P"), ord("_"), ord("^"), ord("X")):
                    # OSC (`\x1b]...\x07` or `\x1b\\`), DCS (`\x1bP...\x1b\\`), APC/PM/SOS
                    j = i + 2
                    found_end = False
                    while j < n:
                        if buf[j] == 0x07:
                            i = j + 1
                            found_end = True
                            break
                        if buf[j] == 0x1B and j + 1 < n and buf[j + 1] == ord("\\"):
                            i = j + 2
                            found_end = True
                            break
                        j += 1
                    if not found_end:
                        if n - i <= 512:
                            self._stdin_esc_buf = buf[i:]
                            self._stdin_esc_ts = now
                        break
                    continue
                if nxt == ord("O"):
                    if i + 2 < n:
                        code = buf[i + 2]
                        i += 3
                        if code == ord("D") and self.cur_idx > 0:
                            self.cur_idx -= 1
                            self._pending_stdin_left += 1
                            self._sync_cursor_from_idx()
                        elif code == ord("C") and self.cur_idx < len(self.text):
                            self._pending_stdin_left = 0
                            self.cur_idx += 1
                            self._sync_cursor_from_idx()
                        elif code == ord("H"):
                            self._pending_stdin_left = 0
                            self.cur_idx = 0
                            self._sync_cursor_from_idx()
                        elif code == ord("F"):
                            self._pending_stdin_left = 0
                            self.cur_idx = len(self.text)
                            self._sync_cursor_from_idx()
                        continue
                    self._stdin_esc_buf = buf[i:]
                    self._stdin_esc_ts = now
                    break
                i += 2
                self._pending_stdin_left = 0
                if nxt in (ord("b"), ord("B")):
                    self._move_word_left()
                elif nxt in (ord("f"), ord("F")):
                    self._move_word_right()
                elif nxt in (0x7F, 0x08):
                    if self._delete_word_left():
                        text_changed = True
                continue
            if b in (0x0D, 0x0A):
                if self._in_paste:
                    self.text = self.text[: self.cur_idx] + "\n" + self.text[self.cur_idx :]
                    self.cur_idx += 1
                    text_changed = True
                else:
                    self.reset()
                    text_changed = False
                i += 1
            elif b in (0x7F, 0x08):
                self._pending_stdin_left = 0
                if self.cur_idx > 0:
                    self.text = self.text[: self.cur_idx - 1] + self.text[self.cur_idx :]
                    self.cur_idx -= 1
                    text_changed = True
                i += 1
            elif b == 0x01:
                self._pending_stdin_left = 0
                self.cur_idx = 0
                self._sync_cursor_from_idx()
                i += 1
            elif b == 0x05:
                self._pending_stdin_left = 0
                self.cur_idx = len(self.text)
                self._sync_cursor_from_idx()
                i += 1
            elif b == 0x02:
                if self.cur_idx > 0:
                    self.cur_idx -= 1
                    self._pending_stdin_left += 1
                    self._sync_cursor_from_idx()
                i += 1
            elif b == 0x06:
                self._pending_stdin_left = 0
                if self.cur_idx < len(self.text):
                    self.cur_idx += 1
                    self._sync_cursor_from_idx()
                i += 1
            elif b == 0x04:
                self._pending_stdin_left = 0
                if 0 <= self.cur_idx < len(self.text):
                    self.text = self.text[: self.cur_idx] + self.text[self.cur_idx + 1 :]
                    text_changed = True
                i += 1
            elif b == 0x0B:
                self._pending_stdin_left = 0
                if self.cur_idx < len(self.text):
                    self.text = self.text[: self.cur_idx]
                    text_changed = True
                i += 1
            elif b == 0x15:
                self._pending_stdin_left = 0
                self.text = ""
                self.cur_idx = 0
                text_changed = True
                i += 1
            elif b == 0x17:
                self._pending_stdin_left = 0
                if self._delete_word_left():
                    text_changed = True
                i += 1
            elif b == 0x03:
                self.reset()
                text_changed = False
                i += 1
            elif b == 0x09:
                self._pending_stdin_left = 0
                self._allow_output_shrink = True
                i += 1
            elif b < 0x20:
                i += 1
            else:
                j = i
                while j < n and buf[j] >= 0x20 and buf[j] != 0x7F and buf[j] != 0x1B:
                    j += 1
                chunk = buf[i:j]
                i = j
                self._pending_stdin_left = 0
                s = ""
                for trim in range(min(4, len(chunk))):
                    try:
                        s = (chunk[: len(chunk) - trim] if trim else chunk).decode("utf-8")
                        if trim and i == n:
                            self._stdin_utf8_buf = chunk[len(chunk) - trim :]
                        break
                    except UnicodeDecodeError:
                        s = ""
                else:
                    s = chunk.decode("utf-8", errors="ignore")
                if s:
                    self.text = self.text[: self.cur_idx] + s + self.text[self.cur_idx :]
                    self.cur_idx += len(s)
                    self._pending_stdin_chars += len(s)
                    text_changed = True
        if text_changed:
            self._recompute_lines()

    def _row_col_to_char_idx(self, row: int | None, col: int, line_idx_hint: int | None = None) -> int:
        if not self.lines:
            return 0
        if len(self.lines) == 1:
            return col_to_char_idx(self.lines[0], col)
        if self.top_row is not None and row is not None:
            line_idx = max(0, min(len(self.lines) - 1, row - self.top_row))
        elif line_idx_hint is not None:
            line_idx = max(0, min(len(self.lines) - 1, line_idx_hint))
        else:
            line_idx = max(0, min(len(self.lines) - 1, self.cur_line_idx))
        prefix_chars = sum(len(x) for x in self.lines[:line_idx])
        return prefix_chars + col_to_char_idx(self.lines[line_idx], col)

    # ---- no-flicker (altscreen) mode: prompt geometry read from the replayed screen ----
    # Prompt box: a border row, `> text` (marker col 1, text from col 3), `  text` continuation rows,
    # a border row. agy word-wraps keeping the separating space at the end of the upper row, so a
    # Left/Right press always moves exactly one character, including across soft wraps; a hard newline
    # is one extra press, shown as a caret cell right after the row's last character.

    def _row_text(self, y: int) -> str:
        return "".join(self.vs.buf[y])

    def _is_border_row(self, y: int) -> bool:
        if not 0 <= y < self.vs.rows:
            return False
        t = self._row_text(y).strip()
        if len(t) < max(8, self.vs.cols // 2):
            return False
        return all("─" <= ch <= "▟" or ch in "-=_~·•" for ch in t)

    def _is_head_row(self, y: int) -> bool:
        r = self.vs.buf[y]
        return r[0] not in (" ", "") and r[1] == " " and not self._is_border_row(y)

    def _is_cont_row(self, y: int) -> bool:
        r = self.vs.buf[y]
        return r[0] == " " and r[1] == " " and not self._is_border_row(y)

    def _alt_prompt_region(self) -> tuple[int, int] | None:
        """Rows (0-based, inclusive) of the prompt text that holds the visible caret, else None."""
        vs = self.vs
        if not (vs.alt_active and vs.cursor_known and vs.cursor_visible) or vs.shown_caret is None:
            return None
        cy, cx = vs.shown_caret
        if cx < 2 or not 0 <= cy < vs.rows:
            return None
        y = cy
        while not self._is_head_row(y):
            if not self._is_cont_row(y) or y == 0:
                return None
            if self._is_border_row(y - 1):
                break  # prompt scrolled inside its box: the `>` row is out of view
            y -= 1
        top = y
        if not self._is_border_row(top - 1):
            return None
        bottom = cy
        while bottom + 1 < vs.rows and self._is_cont_row(bottom + 1):
            bottom += 1
        if not self._is_border_row(bottom + 1):
            return None
        while top < cy and MORE_LINES_RE.match(self._row_text(top)):
            top += 1
        while bottom > cy and MORE_LINES_RE.match(self._row_text(bottom)):
            bottom -= 1
        return top, bottom

    def _content_end(self, y: int, caret: tuple[int, int] | None) -> int:
        """0-based column just after the last visible character of prompt row `y` (>= caret on its row)."""
        row = self.vs.buf[y]
        x = self.vs.cols - 1
        while x >= 2 and row[x] == " ":
            x -= 1
        end = x + 1 if x >= 2 else 2
        if caret is not None and caret[0] == y:
            end = max(end, caret[1])
        return end

    def _count_chars(self, y: int, x0: int, x1: int) -> int:
        return sum(1 for c in self.vs.buf[y][x0:x1] if c != "")

    def _first_unit_width(self, y: int) -> int:
        """Width of the first wrap unit on row `y` (a word, or one wide char); 0 for an empty row."""
        row = self.vs.buf[y]
        end = self._content_end(y, None)
        if end <= 2:
            return 0
        if row[2] == " ":
            return 1
        if _cell_width(row[2][0]) == 2:
            return 2
        w, x = 0, 2
        while x < end and row[x] != " ":
            if row[x] and _cell_width(row[x][0]) == 2:
                break
            w += 1 if row[x] else 0
            x += 1
        return w

    def _boundary(self, y: int, caret: tuple[int, int] | None) -> tuple[int, int]:
        """(characters on row `y`, 1 if a hard newline separates it from row `y + 1` else 0)."""
        end = self._content_end(y, caret)
        chars = self._count_chars(y, 2, end)
        used, width = end - 2, self.vs.cols - 2
        first_w = self._first_unit_width(y + 1)
        if used <= 0 or first_w == 0:
            return chars, 1
        row, nxt = self.vs.buf[y], self.vs.buf[y + 1]
        last = row[end - 1] or row[end - 2]
        last_wide = bool(last) and _cell_width(last[0]) == 2
        next_wide = nxt[2] not in (" ", "") and _cell_width(nxt[2][0]) == 2
        if last_wide or next_wide:
            return (chars, 0) if used + first_w > width else (chars, 1)
        if used + 1 + first_w > width:
            return chars + (1 if used < width else 0), 0  # soft wrap; the space stays on row y
        return chars, 1

    def _alt_index(self, region: tuple[int, int], pos: tuple[int, int], caret: tuple[int, int]) -> int:
        idx = 0
        for y in range(region[0], pos[0]):
            chars, joiner = self._boundary(y, caret)
            idx += chars + joiner
        return idx + self._count_chars(pos[0], 2, pos[1])

    def _snap_target(self, y: int, x: int, caret: tuple[int, int]) -> int:
        row = self.vs.buf[y]
        x = max(2, min(x, self.vs.cols - 1))
        if row[x] == "" and x > 2:
            x -= 1  # right half of a wide char selects that char
        return min(x, self._content_end(y, caret))

    def _region_sig(self, region: tuple[int, int]) -> tuple[str, ...]:
        return tuple(self._row_text(y) for y in range(region[0], region[1] + 1))

    def _rows_for_log(self, region: tuple[int, int]) -> list:
        if self.dbg is not None and self.dbg.level >= 2:
            return [(y + 1, self._row_text(y).rstrip()) for y in range(region[0], region[1] + 1)]
        return [(y + 1, self._content_end(y, None) - 2) for y in range(region[0], region[1] + 1)]

    @staticmethod
    def _arrows(delta: int) -> bytes:
        return b"\x1b[D" * -delta if delta < 0 else b"\x1b[C" * delta

    def _alt_click(self, col: int, row: int) -> bytes:
        """Arrow keys moving agy's caret to the clicked cell (1-based coordinates)."""
        region = self._alt_prompt_region()
        caret = self.vs.shown_caret
        ty, tx = row - 1, col - 1
        if region is None or caret is None or not region[0] <= ty <= region[1]:
            self._log("click", at=(row, col), result="no-prompt", region=region, caret=caret,
                      visible=self.vs.cursor_visible, known=self.vs.cursor_known)
            return b""
        tx = self._snap_target(ty, tx, caret)
        delta = self._alt_index(region, (ty, tx), caret) - self._alt_index(region, caret, caret)
        now = time.monotonic()
        self._verify = {
            "target": (ty, tx), "t0": now, "sent_at": now, "seq": self.vs.show_seq,
            "before": caret, "iter": 0, "sig": self._region_sig(region),
        } if delta else None
        self._log("click", at=(row, col), caret=(caret[0] + 1, caret[1] + 1), target=(ty + 1, tx + 1),
                  region=(region[0] + 1, region[1] + 1), delta=delta, rows=self._rows_for_log(region))
        return self._arrows(delta)

    def tick(self, now: float | None = None) -> bytes:
        """Periodic work: flush a stale partial mouse read and verify/correct the last click."""
        now = time.monotonic() if now is None else now
        out = b""
        if self._in_carry and now - self._in_carry_t > 0.05:
            out, self._in_carry = self._in_carry, b""
        v = self._verify
        if v is None:
            return out
        if now - v["t0"] > 1.0:
            return out + self._end_verify("timeout")
        if self.vs.show_seq == v["seq"]:
            return out + (self._end_verify("no-frame") if now - v["sent_at"] > 0.4 else b"")
        if now - self._last_output_t < 0.04 and now - v["sent_at"] < 0.25:
            return out  # agy is still painting
        caret = self.vs.shown_caret
        region = self._alt_prompt_region()
        if region is None or caret is None or self._region_sig(region) != v["sig"]:
            return out + self._end_verify("layout-changed")
        if caret == v["before"]:
            return out + (self._end_verify("caret-unchanged") if now - v["sent_at"] >= 0.25 else b"")
        target = v["target"]
        delta = 0 if caret == target else self._alt_index(region, target, caret) - self._alt_index(region, caret, caret)
        if delta == 0:
            return out + self._end_verify("ok", caret=(caret[0] + 1, caret[1] + 1))
        if v["iter"] >= 2:
            return out + self._end_verify("gave-up", caret=(caret[0] + 1, caret[1] + 1), delta=delta)
        v.update(iter=v["iter"] + 1, sent_at=now, seq=self.vs.show_seq, before=caret)
        self._log("verify", caret=(caret[0] + 1, caret[1] + 1), target=(target[0] + 1, target[1] + 1), correction=delta)
        return out + self._arrows(delta)

    def _end_verify(self, result: str, **kw) -> bytes:
        self._log("verify", result=result, **kw)
        self._verify = None
        return b""

    def _record_alt_drag_sel(self, x0: int, y0: int, x1: int, y1: int) -> None:
        region = self._alt_prompt_region()
        caret = self.vs.shown_caret
        ty0, tx0 = y0 - 1, x0 - 1
        ty1, tx1 = y1 - 1, x1 - 1
        if (
            region is None
            or caret is None
            or not (region[0] <= ty0 <= region[1])
            or not (region[0] <= ty1 <= region[1])
        ):
            self.clear_prompt_sel()
            return
        tx0 = self._snap_target(ty0, tx0, caret)
        tx1 = self._snap_target(ty1, tx1, caret)
        idx0 = self._alt_index(region, (ty0, tx0), caret)
        idx1 = self._alt_index(region, (ty1, tx1), caret)
        if idx0 == idx1:
            self.clear_prompt_sel()
            return
        start_idx, end_idx = min(idx0, idx1), max(idx0, idx1)
        caret_idx = self._alt_index(region, caret, caret)
        self.prompt_sel = (start_idx, end_idx)
        self._alt_sel_meta = {
            "sig": self._region_sig(region),
            "region": region,
            "caret_idx": caret_idx,
        }
        self._verify = None
        self._log(
            "drag_sel",
            start=start_idx,
            end=end_idx,
            caret_idx=caret_idx,
            from_pos=(y0, x0),
            to_pos=(y1, x1),
        )

    def _build_sel_delete_prefix(self, is_alt: bool) -> bytes:
        if self.prompt_sel is None:
            return b""
        start_idx, end_idx = self.prompt_sel
        count = end_idx - start_idx
        if count <= 0:
            self.clear_prompt_sel()
            return b""
        if is_alt:
            region = self._alt_prompt_region()
            caret = self.vs.shown_caret
            meta = self._alt_sel_meta
            if meta is not None:
                if region is None or caret is None or self._region_sig(region) != meta["sig"]:
                    self.clear_prompt_sel()
                    return b""
                caret_idx = self._alt_index(region, caret, caret)
            elif region is not None and caret is not None:
                caret_idx = self._alt_index(region, caret, caret)
            else:
                caret_idx = end_idx
            delta = end_idx - caret_idx
            arrows = self._arrows(delta)
            self.clear_prompt_sel()
            self._log("sel_del", mode="alt", start=start_idx, end=end_idx, delta=delta, count=count)
            return arrows + (b"\x7f" * count)

        delta = end_idx - self.cur_idx
        arrows = self._arrows(delta)
        if delta < 0:
            self._pending_stdin_left += -delta
        elif delta > 0:
            self._pending_stdin_left = 0
        self.text = self.text[:start_idx] + self.text[end_idx:]
        self.cur_idx = start_idx
        self._recompute_lines()
        self.clear_prompt_sel()
        self._log("sel_del", mode="inline", start=start_idx, end=end_idx, delta=delta, count=count)
        return arrows + (b"\x7f" * count)

    def _filter_key_chunk_with_sel(self, chunk: bytes, is_alt: bool) -> bytes:
        if not chunk:
            return b""
        if self.prompt_sel is None:
            if not is_alt:
                self._inline_drag_start = None
                self.feed_stdin(chunk)
            return chunk
        out = bytearray()
        pos = 0
        while pos + 3 <= len(chunk) and chunk[pos : pos + 3] in (b"\x1b[I", b"\x1b[O"):
            out.extend(chunk[pos : pos + 3])
            pos += 3
        rem = chunk[pos:]
        if not rem:
            return bytes(out)
        del_key_len = 0
        if rem[:1] in (b"\x7f", b"\x08"):
            del_key_len = 1
        elif rem.startswith((b"\x1b\x7f", b"\x1b\x08")):
            del_key_len = 2
        elif rem.startswith(b"\x1b[3~"):
            del_key_len = 4
        if del_key_len > 0:
            prefix = self._build_sel_delete_prefix(is_alt=is_alt)
            if prefix:
                out.extend(prefix)
                tail = rem[del_key_len:]
                if tail:
                    if not is_alt:
                        self.feed_stdin(tail)
                    out.extend(tail)
                return bytes(out)
            if not is_alt:
                self.feed_stdin(rem)
            out.extend(rem)
            return bytes(out)
        b0 = rem[0]
        if b0 >= 0x20 and b0 != 0x7F and b0 != 0x1B:
            prefix = self._build_sel_delete_prefix(is_alt=is_alt)
            if prefix:
                out.extend(prefix)
            if not is_alt:
                self.feed_stdin(rem)
            out.extend(rem)
            return bytes(out)
        self.clear_prompt_sel()
        if not is_alt:
            self.feed_stdin(rem)
        out.extend(rem)
        return bytes(out)

    def filter_alt_input(self, data: bytes) -> bytes:
        """altscreen stdin path: turn plain left clicks inside the prompt into arrows, pass the rest."""
        if self._in_carry:
            data, self._in_carry = self._in_carry + data, b""
        if not self._alt_paste:
            m = INPUT_MOUSE_PREFIX_RE.search(data)
            if m and len(data) - m.start() <= 32:
                self._in_carry, self._in_carry_t = data[m.start():], time.monotonic()
                data = data[: m.start()]
        out = bytearray()
        for part in PASTE_SPLIT_RE.split(data):
            if not part:
                continue
            if part == b"\x1b[200~":
                self._alt_paste = True
                self._verify = None
                if self.prompt_sel is not None:
                    out += self._build_sel_delete_prefix(is_alt=True)
                out += part
            elif part == b"\x1b[201~":
                self._alt_paste = False
                out += part
            elif self._alt_paste:
                out += part
            else:
                last = 0
                for m in MOUSE_EVENT_RE.finditer(part):
                    if m.start() > last:
                        self._verify = None  # the user typed: never fight their keys
                        out += self._filter_key_chunk_with_sel(part[last:m.start()], is_alt=True)
                    last = m.end()
                    out += self._alt_mouse(m)
                if last < len(part):
                    self._verify = None
                    out += self._filter_key_chunk_with_sel(part[last:], is_alt=True)
        return bytes(out)

    def _alt_mouse(self, m: "re.Match[bytes]") -> bytes:
        raw = m.group(0)
        if m.group(1) is not None:
            b, x, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            release = m.group(4) == b"m"
        else:
            b, x, y = m.group(5)[0] - 32, m.group(6)[0] - 32, m.group(7)[0] - 32
            release = (b & 3) == 3 and not b & 96
        motion, wheel, button, mods = bool(b & 32), bool(b & 64), b & 3, b & 28
        fwd = raw if self.vs.app_mouse_on() else b""
        held = self._held
        if wheel or mods:
            if not wheel:
                self.clear_prompt_sel()
            decision, res = "pass", fwd
        elif release:
            if held is not None:
                self._held = None
                if (x == held["x"] and y == held["y"]) or (
                    held.get("had_motion") and y == held["y"] and abs(x - held["x"]) <= 1
                ):
                    self.clear_prompt_sel()
                    decision, res = "click", self._alt_click(held["x"], held["y"])
                else:
                    self._record_alt_drag_sel(held["x"], held["y"], x, y)
                    decision, res = "drag-release", (held["raw"] + raw) if fwd else b""
            elif self._fwd_gesture:
                self._fwd_gesture = False
                if self._drag_origin is not None:
                    ox, oy = self._drag_origin
                    self._drag_origin = None
                    self._record_alt_drag_sel(ox, oy, x, y)
                decision, res = "pass", raw
            else:
                self._drag_origin = None
                decision, res = "pass", fwd
        elif motion:
            if held is not None and button == 0 and y == held["y"] and abs(x - held["x"]) <= 1:
                held["had_motion"] = True
                decision, res = "jitter", b""
            elif held is not None:
                self._drag_origin = (held["x"], held["y"])
                self._held = None
                self._fwd_gesture = bool(fwd)
                decision, res = "drag", (held["raw"] + raw) if fwd else b""
            else:
                decision, res = "pass", fwd
        elif button == 0 and self._region_has_row(y - 1):
            self.clear_prompt_sel()
            self._drag_origin = None
            self._held = {"x": x, "y": y, "raw": raw, "had_motion": False}
            decision, res = "hold", b""
        else:
            self.clear_prompt_sel()
            self._drag_origin = None
            self._fwd_gesture = button == 0 and bool(fwd)
            decision, res = "pass", fwd
        if decision != "jitter":
            self._log("mouse", enc="sgr" if m.group(1) is not None else "x10", b=b, x=x, y=y,
                      ev="release" if release else ("motion" if motion else "press"), decision=decision,
                      out=len(res), app_mouse=sorted(self.vs.mouse_tracking))
        return res

    def _region_has_row(self, y: int) -> bool:
        region = self._alt_prompt_region()
        return region is not None and region[0] <= y <= region[1]

    def update_from_output(self, raw_bytes: bytes):
        if not raw_bytes:
            return
        self._apply_pending_size()
        was_alt = self.vs.alt_active
        self.vs.feed(raw_bytes)
        self._last_output_t = time.monotonic()
        if was_alt or self.vs.alt_active:
            # Inline regex tracking means nothing on the alternate screen (and must never request DSR there)
            self._tail_buf = b""
            return
        combined = self._tail_buf + raw_bytes
        if len(combined) > 8192:
            combined = combined[-8192:]
        new_start = max(0, len(combined) - len(raw_bytes))
        self._tail_buf = combined
        buf = combined
        matched_prompt_in_chunk = False
        footer_handled_in_head = False

        for m in PROMPT_HEAD_RE.finditer(buf):
            raw_l0 = m.group(1) if m.group(1) is not None else m.group(5)
            try:
                line0 = (raw_l0 or b"").decode("utf-8", errors="ignore")
            except Exception:
                line0 = ""
            r0 = int(m.group(2)) if m.group(2) else None
            c0 = (
                int(m.group(3))
                if m.group(3)
                else (int(m.group(4)) if m.group(4) else char_idx_to_col(line0, len(line0)))
            )
            lines = [line0]
            cur_r = r0
            cur_c = c0
            pos = m.end()
            while True:
                cm = PROMPT_CONT_RE.match(buf[pos:])
                if not cm:
                    break
                try:
                    cont_txt = cm.group(1).decode("utf-8", errors="ignore")
                except Exception:
                    cont_txt = ""
                if cont_txt.startswith("─"):
                    break
                lines.append(cont_txt)
                if cm.group(2):
                    cur_r = int(cm.group(2))
                    cur_c = int(cm.group(3))
                    if r0 is None:
                        r0 = max(1, cur_r - (len(lines) - 1))
                elif cm.group(4):
                    if cur_r is not None:
                        cur_r += 1
                    cur_c = int(cm.group(4))
                else:
                    if cur_r is not None:
                        cur_r += 1
                    cur_c = char_idx_to_col(cont_txt, len(cont_txt))
                pos += cm.end()

            cur_line_idx = len(lines) - 1
            after_prompt = buf[pos:]
            show_idx = after_prompt.find(b"\x1b[?25h")
            if show_idx != -1:
                frame_slice = after_prompt[:show_idx]
                end_with_tail = pos + show_idx + 6
            else:
                frame_slice = after_prompt[:64]
                end_with_tail = pos + len(frame_slice)

            if end_with_tail <= new_start:
                continue

            # Split at the first \r or \n so footer excursions (\n\n...\x1b[3A\x1b[58D) never corrupt prompt cur_c
            nl_pos = -1
            for k, ch_b in enumerate(frame_slice):
                if ch_b in (0x0A, 0x0D):
                    nl_pos = k
                    break
            intra_prompt_slice = frame_slice[:nl_pos] if nl_pos != -1 else frame_slice
            footer_slice = frame_slice[nl_pos:] if nl_pos != -1 else b""

            for sm in CURSOR_STEP_RE.finditer(intra_prompt_slice):
                tok = sm.group(0)
                if sm.group(1):  # \x1b[row;colH
                    cand_r = int(sm.group(1))
                    cand_c = int(sm.group(2))
                    if r0 is None:
                        r0 = max(1, cand_r - (len(lines) - 1))
                    if r0 <= cand_r < r0 + len(lines):
                        cur_r, cur_c = cand_r, cand_c
                        cur_line_idx = cand_r - r0
                elif sm.group(3):  # \x1b[colG
                    cur_c = int(sm.group(3))
                elif sm.group(4) is not None:  # \x1b[nD
                    n = int(sm.group(4)) if sm.group(4) else 1
                    cur_c = max(3, cur_c - n)
                elif sm.group(5) is not None:  # \x1b[nC
                    n = int(sm.group(5)) if sm.group(5) else 1
                    cur_c += n
                elif sm.group(6) is not None:  # \x1b[nA
                    n = int(sm.group(6)) if sm.group(6) else 1
                    if cur_r is not None:
                        cur_r = max(1, cur_r - n)
                    cur_line_idx = max(0, cur_line_idx - n)
                elif tok == b"\x1bM":
                    if cur_r is not None:
                        cur_r = max(1, cur_r - 1)
                    cur_line_idx = max(0, cur_line_idx - 1)
                elif tok == b"\x08":
                    cur_c = max(3, cur_c - 1)

            if footer_slice and show_idx != -1:
                fm_end = FOOTER_END_RE.search(footer_slice)
                if fm_end:
                    footer_body = footer_slice[: fm_end.start()]
                    up_in_body = sum(
                        (int(um.group(1)) if um.group(1) else 1)
                        for um in UP_ESCAPE_RE.finditer(footer_body)
                    )
                    down_rows = max(0, footer_body.count(b"\n") - up_in_body)
                    if down_rows > 0:
                        self._last_footer_down = down_rows
                    up_rows = int(fm_end.group(1)) if fm_end.group(1) else 1
                    right_shift = int(fm_end.group(2)) if fm_end.group(2) else (1 if fm_end.group(2) is not None else 0)
                    cur_line_idx = max(
                        0, min(len(lines) - 1, (len(lines) - 1) + down_rows - up_rows)
                    )
                    if r0 is not None:
                        cur_r = r0 + cur_line_idx
                    if right_shift >= 2:
                        cur_c = 1 + right_shift
                    footer_handled_in_head = True

            out_text = "".join(lines)
            if r0 is not None:
                self.top_row = r0
            if (
                self.text
                and self.text.startswith(out_text)
                and len(self.text) > len(out_text)
                and not self._allow_output_shrink
            ):
                self._pending_stdin_chars = len(self.text) - len(out_text)
                continue

            self._allow_output_shrink = False
            self._pending_stdin_left = 0
            self._pending_stdin_chars = 0
            self.lines = lines
            self.text = out_text
            self.cur_row = cur_r
            self.cur_line_idx = cur_line_idx
            self.cur_col = max(3, cur_c)
            self.cur_idx = self._row_col_to_char_idx(self.cur_row, self.cur_col, line_idx_hint=cur_line_idx)
            matched_prompt_in_chunk = True
            if self.top_row is None and self.last_prompt_row is None:
                self.need_dsr = True

        matched_wrap_in_chunk = False
        if not matched_prompt_in_chunk:
            for wm in INCREMENTAL_WRAP_RE.finditer(raw_bytes):
                try:
                    cont_txt = wm.group(1).decode("utf-8", errors="ignore")
                except Exception:
                    cont_txt = ""
                if cont_txt and not cont_txt.startswith("─"):
                    if self.text.endswith(cont_txt) and len(self.text) > len(cont_txt):
                        prefix = self.text[: -len(cont_txt)]
                        self.lines = [prefix, cont_txt]
                        self._sync_cursor_from_idx()
                        matched_wrap_in_chunk = True

            for fm1 in ASCII_FIRST_RE.finditer(raw_bytes):
                try:
                    first_s = fm1.group(1).decode("utf-8", errors="ignore")
                except Exception:
                    first_s = ""
                if not first_s:
                    continue
                if self._pending_stdin_chars > 0:
                    used = min(self._pending_stdin_chars, len(first_s))
                    self._pending_stdin_chars -= used
                    first_s = first_s[used:]
                if first_s:
                    self.text = self.text[: self.cur_idx] + first_s + self.text[self.cur_idx :]
                    self.cur_idx += len(first_s)
                    self._recompute_lines()

            for im in ASCII_INC_RE.finditer(raw_bytes):
                try:
                    inc_s = im.group(1).decode("utf-8", errors="ignore")
                except Exception:
                    inc_s = ""
                back_n = int(im.group(2)) if im.group(2) else 0
                ins_len = max(0, len(inc_s) - back_n)
                ins_s = inc_s[:ins_len]
                if not ins_s:
                    continue
                if self._pending_stdin_chars > 0:
                    used = min(self._pending_stdin_chars, len(ins_s))
                    self._pending_stdin_chars -= used
                    ins_s = ins_s[used:]
                if ins_s:
                    self.text = self.text[: self.cur_idx] + ins_s + self.text[self.cur_idx :]
                    self.cur_idx += len(ins_s)
                    self._recompute_lines()

        for bm in ASCII_BS_RE.finditer(raw_bytes):
            n_bs = len(bm.group(1)) if bm.group(1) else int(bm.group(2))
            if self._pending_stdin_left > 0:
                used = min(self._pending_stdin_left, n_bs)
                self._pending_stdin_left -= used
                n_bs -= used
            if n_bs > 0:
                self.cur_idx = max(0, self.cur_idx - n_bs)
                self._sync_cursor_from_idx()

        if not footer_handled_in_head:
            for fm in FOOTER_CURSOR_RE.finditer(raw_bytes):
                up_rows = int(fm.group(1)) if fm.group(1) else 1
                right_shift = int(fm.group(2)) if fm.group(2) else (1 if fm.group(2) is not None else 0)
                if right_shift >= 2 and (matched_prompt_in_chunk or matched_wrap_in_chunk or not self.text):
                    down_rows = self._last_footer_down
                    target_line_idx = max(
                        0, min(len(self.lines) - 1, (len(self.lines) - 1) + down_rows - up_rows)
                    )
                    self.cur_line_idx = target_line_idx
                    self.cur_col = 1 + right_shift
                    self.cur_idx = self._row_col_to_char_idx(
                        self.cur_row, self.cur_col, line_idx_hint=target_line_idx
                    )
                    self._sync_cursor_from_idx()
                    if self.top_row is None and self.last_prompt_row is None:
                        self.need_dsr = True

    def _inline_click_target(self, click_col: int, click_row: int) -> tuple[int, int, int] | None:
        if self._alt_screen or not self.text:
            return None
        if self.top_row is not None:
            # Accept clicks on any line of the prompt (`top_row .. top_row + len(lines) - 1`) with 1-row tolerance
            if not (self.top_row - 1 <= click_row <= self.top_row + len(self.lines)):
                return None
            line_idx = max(0, min(len(self.lines) - 1, click_row - self.top_row))
        else:
            if not (1 <= click_row <= self.term_rows):
                return None
            if len(self.lines) == 1:
                self.last_prompt_row = click_row
                line_idx = 0
            else:
                if self.last_prompt_row is None or click_row < self.last_prompt_row:
                    self.last_prompt_row = click_row
                elif click_row - self.last_prompt_row > len(self.lines) + 2:
                    self.last_prompt_row = click_row - (len(self.lines) - 1)
                line_idx = max(0, min(len(self.lines) - 1, click_row - self.last_prompt_row))

        prefix_chars = sum(len(x) for x in self.lines[:line_idx])
        target_idx = prefix_chars + col_to_char_idx(self.lines[line_idx], click_col)
        return line_idx, prefix_chars, target_idx

    def arrows_for_click(self, click_col: int, click_row: int) -> bytes:
        """Return `\x1b[D` or `\x1b[C` sequence to move cursor to `(click_row, click_col)` in prompt."""
        self.clear_prompt_sel()
        res = self._inline_click_target(click_col, click_row)
        if res is None:
            return b""
        line_idx, prefix_chars, target_idx = res
        self._inline_drag_start = target_idx
        delta = target_idx - self.cur_idx
        self.cur_idx = target_idx
        self.cur_line_idx = line_idx
        if self.top_row is not None:
            self.cur_row = max(self.top_row, min(self.top_row + len(self.lines) - 1, click_row))
        self.cur_col = char_idx_to_col(self.lines[line_idx], target_idx - prefix_chars)
        if delta < 0:
            self._pending_stdin_left += -delta
            return b"\x1b[D" * (-delta)
        if delta > 0:
            self._pending_stdin_left = 0
            return b"\x1b[C" * delta
        return b""

    def finish_inline_drag(self, idx0: int, release_col: int, release_row: int) -> bytes:
        res = self._inline_click_target(release_col, release_row)
        if res is None:
            self.clear_prompt_sel()
            return b""
        _, _, idx1 = res
        if idx1 == idx0:
            self.clear_prompt_sel()
            return b""
        start_idx, end_idx = min(idx0, idx1), max(idx0, idx1)
        self.prompt_sel = (start_idx, end_idx)
        delta = end_idx - self.cur_idx
        self.cur_idx = end_idx
        self._sync_cursor_from_idx()
        if delta < 0:
            self._pending_stdin_left += -delta
            return b"\x1b[D" * (-delta)
        if delta > 0:
            self._pending_stdin_left = 0
            return b"\x1b[C" * delta
        return b""

    def filter_inline_keys(self, data: bytes) -> bytes:
        if not data:
            return b""
        if self.prompt_sel is None:
            self._inline_drag_start = None
            self.feed_stdin(data)
            return data
        out = bytearray()
        for part in PASTE_SPLIT_RE.split(data):
            if not part:
                continue
            if part == b"\x1b[200~":
                if self.prompt_sel is not None:
                    out.extend(self._build_sel_delete_prefix(is_alt=False))
                self.feed_stdin(part)
                out.extend(part)
            elif part == b"\x1b[201~" or self._in_paste:
                self.feed_stdin(part)
                out.extend(part)
            else:
                out.extend(self._filter_key_chunk_with_sel(part, is_alt=False))
        return bytes(out)
