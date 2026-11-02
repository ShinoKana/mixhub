#!/usr/bin/env python3
"""`mixhub.filters.agy_claude.patterns`: byte / text regexes for prompt blocks, cursor moves, mouse reports, DSR replies and paste markers."""

import re


# Regexes to track single-line and multi-line wrapped prompt blocks and cursor coordinates in `agy` output
PROMPT_HEAD_RE = re.compile(
    rb"(?:\x1b\[K(?:>|\x1b\[[0-9;]*m>\x1b\[m) ([^\r\n\x1b]*)(?:\x1b\[\?7h)?(?:\x1b\[(\d+);(\d+)H|\x1b\[(\d+)G)?|\r\n(?:>|\x1b\[[0-9;]*m>\x1b\[m) ([^\r\n\x1b]*)(?=(?:(?:\x1b\[\?7l)?\r\n(?:\x1b\[\?7l)?(?:\x1b\[K)?  [^\r\n\x1b]*(?:\x1b\[K)?)*\r\n(?:\x1b\[[0-9;]*m)*\xe2\x94\x80))"
)
PROMPT_CONT_RE = re.compile(
    rb"^(?:\x1b\[\?7l)?\r\n(?:\x1b\[\?7l)?(?:\x1b\[K)?  ([^\r\n\x1b]*)(?:\x1b\[K)?(?:\x1b\[\?7h)?(?:\x1b\[(\d+);(\d+)H|\x1b\[(\d+)G)?"
)
INCREMENTAL_WRAP_RE = re.compile(
    rb"\r\n(?:\x1b\[\?7l)?(?:\x1b\[K)?  ([^\r\n\x1b]+)\x1b\[K\r\n(?:\x1b\[[0-9;]*m)*\xe2\x94\x80"
)
ASCII_FIRST_RE = re.compile(
    rb"\x1b\[\?25l(?:\r|\n)*\x1b\[J\x1b\[\d+A\x1b\[2C([\x20-\x7e]+)\r?\n"
)
ASCII_INC_RE = re.compile(
    rb"\x1b\[\?25l([\x20-\x7e]+)(?:\x1b\[(\d+)D)?\x1b\[\?25h"
)
ASCII_BS_RE = re.compile(rb"\x1b\[\?25l(?:(\x08+)|\x1b\[(\d+)D)\x1b\[\?25h")
CURSOR_STEP_RE = re.compile(
    rb"\x1b\[(\d+);(\d+)H|\x1b\[(\d+)G|\x1b\[(\d*)D|\x1b\[(\d*)C|\x1b\[(\d*)A|\x1bM|\x08"
)
FOOTER_CURSOR_RE = re.compile(rb"\r\x1b\[(\d*)A(?:\x1b\[(\d*)C)?\x1b\[\?25h")
FOOTER_END_RE = re.compile(rb"\r\x1b\[(\d*)A(?:\x1b\[(\d*)C)?$")
UP_ESCAPE_RE = re.compile(rb"\x1b\[(\d*)A|\x1bM")
MOUSE_EVENT_RE = re.compile(
    rb"\x1b\[<(\d+);(\d+);(\d+)([Mm])|\x1b\[M([\x20-\xff])([\x21-\xff])([\x21-\xff])"
)
SGR_MOUSE_RE = MOUSE_EVENT_RE
DSR_REPLY_RE = re.compile(rb"\x1b\[(\d+);(\d+)R")
# Trailing, still-incomplete mouse report at the end of a stdin read (never a lone ESC, so Esc stays instant)
INPUT_MOUSE_PREFIX_RE = re.compile(rb"\x1b\[(?:<[\d;]*|M[\x20-\xff]{0,2})\Z")
PASTE_SPLIT_RE = re.compile(rb"(\x1b\[200~|\x1b\[201~)")
MORE_LINES_RE = re.compile(r"^\s*[↑↓]\s*\d+\s+more\b")
MOUSE_RESET = b"\x1b[?1000l\x1b[?1002l\x1b[?1003l\x1b[?1006l"
