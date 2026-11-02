#!/usr/bin/env python3
"""
`mixhub.core.textwidth`: Unified terminal display width, truncation, padding, and wrapping.

Single source of truth shared between `tui.py`, `agy_claude_filter.py`, and `mixhub.tui.core.canvas`.
Pure stdlib, zero curses / subprocess imports.
"""
from __future__ import annotations

import functools
import time
import unicodedata

# Ambiguous-width geometric/star symbols treated as 1-column in standard terminals
SINGLE_COL_SYMBOLS: frozenset[str] = frozenset(
    "★☆●○◆◇◈◜◝◞◟·─━│┃┆⋮◀▶◂▸‹›⌄◧▤⇤✕❯╭╮╰╯┌┐└┘├┤┬┴┼⋯▵▴▾▿✎↩⇄↗↙↵▐▌▰▱▁▂▃▄▅▆▇█✻⬡✦"
    "ⒶⒷⒸⒹⒺⒻⒼⒽⒾⒿⓀⓁⓂⓃⓄⓅⓆⓇⓈⓉⓊⓋⓌⓍⓎⓏ"
)


@functools.lru_cache(maxsize=16384)
def char_width(ch: str) -> int:
    """Return terminal display column width (0, 1, or 2) for a single character."""
    if not ch or ch == "\0":
        return 0
    if ch in SINGLE_COL_SYMBOLS:
        return 1
    cp = ord(ch)
    if 32 <= cp < 127:
        return 1
    if cp < 32 or (0x7F <= cp < 0xA0) or cp in (0x200D, 0xFE0E, 0xFE0F):
        return 0
    if unicodedata.combining(ch):
        return 0
    eaw = unicodedata.east_asian_width(ch)
    if eaw in ("W", "F"):
        return 2
    if 0x1F300 <= cp <= 0x1FAFF or 0x2600 <= cp <= 0x27BF:
        return 2
    return 1


@functools.lru_cache(maxsize=8192)
def disp_width(text: str) -> int:
    """Return total terminal display columns for `text` (with C-level ASCII fast-path)."""
    if not text:
        return 0
    if text.isascii():
        if text.isprintable():
            return len(text)
        return sum(1 for c in text if 32 <= ord(c) < 127)
    return sum(char_width(ch) for ch in text)


@functools.lru_cache(maxsize=8192)
def clean_single_line(text: str) -> str:
    """Collapse any newlines, tabs, and control chars into single spaces."""
    if not text:
        return ""
    if text.isascii() and "\n" not in text and "\r" not in text and "\t" not in text:
        return " ".join(text.split())
    return " ".join(
        "".join(ch if ord(ch) >= 32 else " " for ch in str(text)).split()
    )


@functools.lru_cache(maxsize=8192)
def disp_trunc(text: str, max_cols: int, ellipsis: str = "…") -> str:
    """Truncate `text` to at most `max_cols` terminal display columns without splitting wide chars."""
    text = clean_single_line(text)
    if max_cols <= 0:
        return ""
    if text.isascii() and len(text) <= max_cols:
        return text
    if disp_width(text) <= max_cols:
        return text
    ell_w = disp_width(ellipsis)
    target = max(0, max_cols - ell_w)
    out: list[str] = []
    w = 0
    for ch in text:
        cw = char_width(ch)
        if w + cw > target:
            break
        out.append(ch)
        w += cw
    return "".join(out) + (ellipsis if max_cols >= ell_w else "")


@functools.lru_cache(maxsize=8192)
def disp_pad(text: str, target_cols: int) -> str:
    """Truncate or right-pad `text` with spaces to exact `target_cols` display width."""
    s = disp_trunc(text, target_cols, "…")
    pad = max(0, target_cols - disp_width(s))
    return s + (" " * pad)


@functools.lru_cache(maxsize=8192)
def _disp_wrap_cached(text: str, max_cols: int) -> tuple[str, ...]:
    if max_cols < 8:
        max_cols = 8
    cleaned = "".join(ch for ch in str(text) if ch == "\t" or ord(ch) >= 32).expandtabs(2)
    if not cleaned:
        return ("",)
    if cleaned.isascii() and len(cleaned) <= max_cols:
        return (cleaned,)
    lines: list[str] = []
    cur: list[str] = []
    cur_w = 0
    for ch in cleaned:
        cw = char_width(ch)
        if cur_w + cw > max_cols and cur:
            # Break at the last space after the indent so English words stay whole; CJK and long words split anywhere
            indent = len(cur) - len("".join(cur).lstrip(" "))
            cut = next((i for i in range(len(cur) - 1, indent, -1) if cur[i] == " "), -1)
            if ch == " ":
                lines.append("".join(cur))
                cur, cur_w = [], 0
                continue
            if cut > 0:
                lines.append("".join(cur[:cut]))
                cur = cur[cut + 1:] + [ch]
                cur_w = sum(char_width(c) for c in cur)
                continue
            lines.append("".join(cur))
            cur = [ch]
            cur_w = cw
        else:
            cur.append(ch)
            cur_w += cw
    if cur or not lines:
        lines.append("".join(cur))
    return tuple(lines)


def disp_wrap(text: str, max_cols: int) -> list[str]:
    """Wrap a single paragraph line to `max_cols` terminal display columns (CJK-safe, cached)."""
    return list(_disp_wrap_cached(str(text), max_cols))


def rel_time(ts: float, now: float | None = None) -> str:
    """Compact minute-granularity age string (`1m`, `12m`, `3h`, `5d`, `2w`)."""
    if not ts:
        return ""
    ref = time.time() if now is None else float(now)
    diff = max(0, int(ref - ts))
    if diff < 60:
        return "1m"
    if diff < 3600:
        return f"{diff // 60}m"
    if diff < 86400:
        return f"{diff // 3600}h"
    if diff < 86400 * 99:
        return f"{diff // 86400}d"
    return f"{diff // (86400 * 7)}w"


__all__ = [
    "SINGLE_COL_SYMBOLS",
    "_disp_wrap_cached",
    "char_width",
    "clean_single_line",
    "disp_pad",
    "disp_trunc",
    "disp_width",
    "disp_wrap",
    "rel_time",
]
