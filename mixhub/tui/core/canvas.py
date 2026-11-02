#!/usr/bin/env python3
"""
`mixhub.tui.core.canvas`: 2D `Canvas` buffer with automatic CJK wide-character half-cell clearing.

Solves CJK wide-character boundary artifacts at the root:
  - A 2-column CJK character occupies `col x` (`Cell(ch, width=2, style)`) and `col x + 1`
    (`Cell("", width=0, style)` continuation cell).
  - Overwriting EITHER half (`col x` or `col x + 1`) automatically clears the other half to `" "` (`width=1`),
    preventing dangling half-characters when floating popovers or narrow text overwrite CJK titles.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mixhub.core.textwidth import char_width
from mixhub.tui.core.geometry import Rect
from mixhub.tui.core.style import P_UNSEL, Style

DEFAULT_STYLE = Style(P_UNSEL)


@dataclass(frozen=True)
class Cell:
    """Single cell in a `Canvas` (`width=2` for wide char head, `width=0` for wide char continuation)."""

    ch: str = " "
    width: int = 1
    style: Style = DEFAULT_STYLE


class Canvas:
    """Offscreen 2D cell buffer with CJK wide-character boundary repair and run-coalesced `flush(stdscr)`."""

    def __init__(self, h: int, w: int, default_style: Style = DEFAULT_STYLE):
        self.h = max(0, int(h))
        self.w = max(0, int(w))
        self.default_style = default_style
        self._grid: list[list[Cell]] = [
            [Cell(" ", 1, default_style) for _ in range(self.w)] for _ in range(self.h)
        ]

    def clear(self, style: Style | None = None) -> None:
        st = style or self.default_style
        blank = Cell(" ", 1, st)
        for y in range(self.h):
            row = self._grid[y]
            for x in range(self.w):
                row[x] = blank

    def _clear_broken_wide_at(self, row: list[Cell], x: int) -> None:
        """
        Before overwriting `row[x]`, repair any wide character whose left or right half sits at `x`.
        """
        if not (0 <= x < self.w):
            return
        cur = row[x]
        if cur.width == 0 and x - 1 >= 0:
            # `x` is the right half (continuation) of a wide char at `x - 1` -> replace `x - 1` with space
            prev = row[x - 1]
            row[x - 1] = Cell(" ", 1, prev.style)
            row[x] = Cell(" ", 1, cur.style)
        elif cur.width == 2 and x + 1 < self.w:
            # `x` is the left half of a wide char whose continuation is at `x + 1` -> replace `x + 1` with space
            nxt = row[x + 1]
            row[x + 1] = Cell(" ", 1, nxt.style)
            row[x] = Cell(" ", 1, cur.style)

    def put(
        self,
        y: int,
        x: int,
        text: str,
        style: Style | None = None,
        clip: Rect | None = None,
    ) -> int:
        """
        Write `text` starting at `(y, x)` using `char_width` per character.
        If any write overwrites either half of a 2-column CJK wide character, the other half is automatically
        cleared to `" "`. Returns the number of columns advanced within bounds.
        """
        if not text or not (0 <= y < self.h):
            return 0
        st = style or self.default_style
        min_x = 0
        max_x = self.w
        if clip is not None:
            if not (clip.y <= y < clip.bottom):
                return 0
            min_x = max(0, clip.x)
            max_x = min(self.w, clip.right)
        if min_x >= max_x:
            return 0

        row = self._grid[y]
        cx = int(x)
        written = 0

        for ch in text:
            if ch in ("\n", "\r"):
                break
            cw = char_width(ch)
            if cw <= 0:
                continue
            if cx >= max_x:
                break
            if cx < min_x:
                if cw == 2 and cx + 1 == min_x and min_x < max_x:
                    # Wide char straddles left clip edge: write a blank space at `min_x`
                    self._clear_broken_wide_at(row, min_x)
                    row[min_x] = Cell(" ", 1, st)
                    written += 1
                cx += cw
                continue

            if cw == 1:
                self._clear_broken_wide_at(row, cx)
                row[cx] = Cell(ch, 1, st)
                cx += 1
                written += 1
            else:
                # cw == 2
                if cx + 1 >= max_x:
                    # Not enough columns left before right bound / clip: pad with 1 space instead of splitting wide char
                    self._clear_broken_wide_at(row, cx)
                    row[cx] = Cell(" ", 1, st)
                    cx += 1
                    written += 1
                    break
                self._clear_broken_wide_at(row, cx)
                self._clear_broken_wide_at(row, cx + 1)
                row[cx] = Cell(ch, 2, st)
                row[cx + 1] = Cell("", 0, st)
                cx += 2
                written += 2

        return written

    def fill(self, rect: Rect, ch: str = " ", style: Style | None = None) -> None:
        """Fill `rect` with single-column character `ch` and `style`, repairing any split wide chars at borders."""
        st = style or self.default_style
        fill_ch = (ch[0] if ch else " ") if char_width(ch[0] if ch else " ") == 1 else " "
        cell = Cell(fill_ch, 1, st)
        y0 = max(0, rect.y)
        y1 = min(self.h, rect.bottom)
        x0 = max(0, rect.x)
        x1 = min(self.w, rect.right)
        if y0 >= y1 or x0 >= x1:
            return
        for y in range(y0, y1):
            row = self._grid[y]
            self._clear_broken_wide_at(row, x0)
            self._clear_broken_wide_at(row, x1 - 1)
            for x in range(x0, x1):
                row[x] = cell

    def cell(self, y: int, x: int) -> Cell:
        if not (0 <= y < self.h and 0 <= x < self.w):
            return Cell(" ", 1, self.default_style)
        return self._grid[y][x]

    def row_text(self, y: int) -> str:
        """Return visual string of row `y` (skipping `width=0` continuation cells of 2-column characters)."""
        if not (0 <= y < self.h):
            return ""
        return "".join(c.ch for c in self._grid[y] if c.width > 0)

    def flush(self, stdscr: Any) -> None:
        """
        Flush this canvas to `stdscr` by coalescing contiguous runs of identical `Style` per row.
        Continuation cells (`width == 0`) are skipped in the string payload because the preceding
        wide character already advances the terminal cursor by 2 columns.
        """
        if stdscr is None:
            return
        max_h, max_w = self.h, self.w
        if hasattr(stdscr, "getmaxyx"):
            try:
                sh, sw = stdscr.getmaxyx()
                max_h = min(max_h, int(sh))
                max_w = min(max_w, int(sw))
            except Exception:
                pass

        for y in range(max_h):
            row = self._grid[y]
            x = 0
            while x < max_w:
                c = row[x]
                if c.width == 0:
                    x += 1
                    continue
                run_x = x
                run_style = c.style
                chars: list[str] = []
                while x < max_w:
                    cur = row[x]
                    if cur.style != run_style:
                        break
                    if cur.width == 2 and x + 1 >= max_w:
                        chars.append(" ")
                        x += 1
                        break
                    if cur.width > 0:
                        chars.append(cur.ch)
                    x += 1
                if chars:
                    try:
                        stdscr.addstr(y, run_x, "".join(chars), run_style.attr())
                    except Exception:
                        pass


__all__ = ["Canvas", "Cell", "DEFAULT_STYLE"]
