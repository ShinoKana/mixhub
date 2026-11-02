#!/usr/bin/env python3
"""`mixhub.filters.agy_claude.stream`: PTY output side: subagent badge, mouse-mode replacement (`transform_chunk`), exit restore bytes, output coalescing and the cursor visibility guard."""

import json
import os
import re
import select
import time

from mixhub.filters.agy_claude.cursor import PromptCursorTracker
from mixhub.filters.agy_claude.patterns import MOUSE_RESET
from mixhub.i18n import t


SUB_LIVE_PATH = "/tmp/agy-subagent-live.json"
ACTIVE_EXT_PATH = "/tmp/agy-active-ext.json"
ACTIVE_EXT_SL_PATH = "/tmp/agy-active-ext-sl.json"
_IN_PTY_MAIN = False
SUB_LIVE_CACHE = {"ts": 0.0, "model": "Argon", "role": "", "ctx": "", "ext_tag": ""}
CURRENT_CID = ""
AGENT_ROLE_RE = re.compile(rb"Agent\(self:\s*[^)\r\n]*\)")
AGENT_TIMER_RE = re.compile(
    rb"(Agent\([^\r\n]*?\xc2\xb7(?:\x1b\[[0-9;]*m|\s)*\d+(?:h\d+m|m(?:\d+s)?|s))(?!\s*\xc2\xb7\s*ctx)"
)


def _get_subagent_badge() -> tuple[str, str, str, str]:
    now = time.monotonic()
    if now - SUB_LIVE_CACHE["ts"] < 1.5:
        return (
            SUB_LIVE_CACHE["model"],
            SUB_LIVE_CACHE["role"],
            SUB_LIVE_CACHE["ctx"],
            SUB_LIVE_CACHE["ext_tag"],
        )
    SUB_LIVE_CACHE["ts"] = now
    model_str, role_str, ctx_str, ext_tag = "Argon", "", "", ""
    allow_live = _IN_PTY_MAIN or (SUB_LIVE_PATH != "/tmp/agy-subagent-live.json") or (ACTIVE_EXT_PATH != "/tmp/agy-active-ext.json")
    if allow_live:
        try:
            if os.path.exists(SUB_LIVE_PATH):
                with open(SUB_LIVE_PATH, encoding="utf-8") as f:
                    d = json.load(f)
                by_cid = d.get("by_cid") or {}
                entry = by_cid.get(CURRENT_CID) if (CURRENT_CID and CURRENT_CID in by_cid) else d
                if isinstance(entry, dict) and time.time() - float(entry.get("ts") or 0) < 90:
                    model_str = str(entry.get("model") or "Argon")
                    raw_role = str(entry.get("role") or "").strip()
                    role_str = re.sub(r"^(?:Flash|Argon)\s*[·:]\s*", "", raw_role, flags=re.I)
                    ctx_str = str(entry.get("ctx") or "")
        except Exception:
            pass
        for ext_cand in (ACTIVE_EXT_PATH, ACTIVE_EXT_SL_PATH):
            try:
                if ext_cand and os.path.exists(ext_cand):
                    with open(ext_cand, encoding="utf-8") as f:
                        ae = json.load(f)
                    if isinstance(ae, dict):
                        pid = int(ae.get("pid") or 0)
                        if pid > 0:
                            os.kill(pid, 0)
                            em = str(ae.get("model") or ae.get("tool") or "ext").replace("claude-", "")
                            et = str(ae.get("title") or t("agy.advisor")).strip()[:12]
                            el = max(0, int(time.time() - float(ae.get("start_ts") or time.time())))
                            ext_tag = f"  \x1b[38;5;141m◆\x1b[m Ext({em}: {et}) · {el}s"
                            break
            except Exception:
                pass
    SUB_LIVE_CACHE["model"] = model_str
    SUB_LIVE_CACHE["role"] = role_str
    SUB_LIVE_CACHE["ctx"] = ctx_str
    SUB_LIVE_CACHE["ext_tag"] = ext_tag
    return model_str, role_str, ctx_str, ext_tag


MOUSE_REPLACEMENTS = (
    # Inline mode only: keep the filter's SGR click reporting alive when `agy` resets mouse modes.
    #    Once agy uses the alternate screen it owns mouse reporting (?1002h for scroll/selection) and
    #    must be able to turn it off (exit, Ctrl+G editor, suspend), so these are skipped there.
    (b"\x1b[?1000l", b"\x1b[?1000h\x1b[?1006h"),
    (b"\x1b[?1002l", b"\x1b[?1000h\x1b[?1006h"),
    (b"\x1b[?1003l", b"\x1b[?1000h\x1b[?1006h"),
    (b"\x1b[?1006l", b"\x1b[?1000h\x1b[?1006h"),
)
MAX_PAT_LEN = max(len(src) for src, _ in MOUSE_REPLACEMENTS)


def transform_chunk(
    data: bytes,
    carry: bytes = b"",
    force_mouse: bool = True,
) -> tuple[bytes, bytes]:
    """
    Transform ANSI sequences in `carry + data`, holding back ONLY a trailing prefix that
    could complete one of `MOUSE_REPLACEMENTS` across read boundaries (never trapping `\x1b[?25h`).
    `force_mouse=False` (agy is in altscreen mode) leaves agy's own mouse mode changes untouched.
    """
    table = MOUSE_REPLACEMENTS if force_mouse else ()
    buf = carry + data
    hold = b""
    if table:
        scan_start = max(0, len(buf) - MAX_PAT_LEN)
        idx = buf.find(b"\x1b", scan_start)
        while idx != -1:
            tail = buf[idx:]
            if any(src.startswith(tail) and tail != src for src, _ in table):
                buf, hold = buf[:idx], tail
                break
            idx = buf.find(b"\x1b", idx + 1)
        for src, dst in table:
            if src in buf:
                buf = buf.replace(src, dst)
    if force_mouse and b"Agent(" in buf:
        sub_mod, sub_role, sub_ctx, ext_tag = _get_subagent_badge()
        if b"Agent(self:" in buf:
            if sub_role:
                buf = AGENT_ROLE_RE.sub(f"Agent({sub_mod}: {sub_role})".encode("utf-8"), buf)
            else:
                buf = buf.replace(b"Agent(self:", f"Agent({sub_mod}:".encode("utf-8"))
        if (sub_ctx or ext_tag) and b"\xc2\xb7" in buf:
            suffix_str = (f" · ctx {sub_ctx}" if sub_ctx else "") + ext_tag
            buf = AGENT_TIMER_RE.sub(rb"\1" + suffix_str.encode("utf-8"), buf)
    if force_mouse and b"\x1b[?1049l" in buf:
        buf = buf + b"\x1b[?1000h\x1b[?1006h"
    return buf, hold


def exit_restore_bytes(tracker: PromptCursorTracker) -> bytes:
    """Terminal state to restore when the filter exits (every mouse mode off, even if agy crashed)."""
    vs = tracker.vs
    out = b"\x1b[?1049l" if vs.alt_active else b""
    out += MOUSE_RESET
    if vs.bracketed_paste:
        out += b"\x1b[?2004l"
    if not vs.cursor_visible:
        out += b"\x1b[?25h"
    return out


def _has_unclosed_redraw(buf: bytes | bytearray) -> bool:
    """True when `buf` has an unclosed cursor-hide (`?25l`) or synchronized update (`?2026h`)."""
    r_hide = buf.rfind(b"\x1b[?25l")
    r_show = buf.rfind(b"\x1b[?25h")
    if r_hide > r_show:
        return True
    r_s0 = buf.rfind(b"\x1b[?2026h")
    r_s1 = buf.rfind(b"\x1b[?2026l")
    return r_s0 > r_s1


def coalesce_pty_output(
    master_fd: int,
    first_chunk: bytes,
    *,
    idle_window: float = 0.004,
    max_hold: float = 0.016,
    sync_hold: float = 0.060,
    select_fn=select.select,
    read_fn=os.read,
    mono_fn=time.monotonic,
) -> bytes:
    """
    Coalesce multi-chunk PTY redraw bursts (macOS 1,024-byte kernel PTY buffer slices) into a single
    logical frame before writing to stdout.
    """
    if not first_chunk:
        return b""
    buf = bytearray(first_chunk)
    t0 = mono_fn()
    in_sync = _has_unclosed_redraw(buf)
    while True:
        now = mono_fn()
        limit = sync_hold if in_sync else max_hold
        elapsed = now - t0
        if elapsed >= limit:
            break
        wait_win = min(0.012 if in_sync else idle_window, limit - elapsed)
        if wait_win <= 0.0:
            break
        mr, _, _ = select_fn([master_fd], [], [], wait_win)
        if not mr:
            if in_sync and (mono_fn() - t0) < max_hold:
                continue
            break
        try:
            more = read_fn(master_fd, 65536)
        except OSError:
            break
        if not more:
            break
        buf.extend(more)
        was_sync = in_sync
        in_sync = _has_unclosed_redraw(buf)
        if was_sync and not in_sync:
            # Frame just closed `?25h` / `?2026l`: do one ultra-short 1.5ms check for any trailing tail bytes
            mr2, _, _ = select_fn([master_fd], [], [], 0.0015)
            if mr2:
                try:
                    tail = read_fn(master_fd, 65536)
                    if tail:
                        buf.extend(tail)
                except OSError:
                    pass
            break
    return bytes(buf)


class CursorVisibilityGuard:
    """
    Prevent `tmux` + macOS 1,024-byte PTY chunking from drawing multi-row `agy` frames with a visible
    hardware cursor (`?25h`):
      1. When transitioning from cursor-visible to a multi-row hidden-cursor redraw (`len > 64` with `\\x1b[?25l`),
         emit `b"\\x1b[?25l"` alone first and pause `pre_hide_delay` (3ms) in `tmux` so `hubpool` and outer `hub`
         complete `server_client_reset_state()` -> `tty_update_mode()` and hide the hardware cursor on
         `Terminal.app` BEFORE any cell-drawing bytes arrive.
      2. Hold the trailing `\\x1b[?25h` until output has been quiet for `idle_show_delay` (`0.120s`, or
         `typing_show_delay = 0.020s` right after user keystrokes on `stdin`), keeping the cursor 100% hidden
         across back-to-back 80ms spinner frames with zero `?25h`/`?25l` toggling and zero 3ms sleeps on
         subsequent frames.
    """

    def __init__(
        self,
        *,
        in_tmux: bool = True,
        pre_hide_delay: float = 0.003,
        idle_show_delay: float = 0.120,
        typing_show_delay: float = 0.020,
    ) -> None:
        self.in_tmux = bool(in_tmux)
        self.pre_hide_delay = float(pre_hide_delay)
        self.idle_show_delay = float(idle_show_delay)
        self.typing_show_delay = float(typing_show_delay)
        self.cursor_hidden_on_tty = False
        self.pending_cursor_show = False
        self.last_redraw_mono = 0.0
        self.last_stdin_mono = 0.0

    def note_stdin(self, now: float) -> None:
        self.last_stdin_mono = now

    def _effective_show_delay(self, now: float) -> float:
        if (now - self.last_stdin_mono) < 0.25:
            return self.typing_show_delay
        return self.idle_show_delay

    def next_timeout(self, now: float) -> float | None:
        if not self.pending_cursor_show:
            return None
        due = self.last_redraw_mono + self._effective_show_delay(now)
        return max(0.0, due - now)

    def flush_due(self, now: float) -> bytes:
        if not self.pending_cursor_show:
            return b""
        if now >= self.last_redraw_mono + self._effective_show_delay(now):
            self.pending_cursor_show = False
            self.cursor_hidden_on_tty = False
            return b"\x1b[?25h"
        return b""

    def prepare_write(self, data: bytes, now: float) -> tuple[bytes, bytes, float]:
        """Return `(pre_write, body_write, pre_sleep_sec)` for a transformed output frame."""
        if not data:
            return b"", b"", 0.0
        r_hide = data.rfind(b"\x1b[?25l")
        r_show = data.rfind(b"\x1b[?25h")
        is_redraw = (r_hide != -1 and len(data) > 64)
        if is_redraw:
            ends_visible = r_show > r_hide
            body = data.replace(b"\x1b[?25h", b"") if ends_visible else data
            self.last_redraw_mono = now
            self.pending_cursor_show = bool(ends_visible)
            if not self.cursor_hidden_on_tty:
                self.cursor_hidden_on_tty = True
                if self.in_tmux and self.pre_hide_delay > 0.0:
                    return b"\x1b[?25l", body, self.pre_hide_delay
            return b"", body, 0.0

        if r_show > r_hide:
            self.cursor_hidden_on_tty = False
            self.pending_cursor_show = False
        elif r_hide > r_show:
            self.cursor_hidden_on_tty = True
            self.pending_cursor_show = False
        return b"", data, 0.0
