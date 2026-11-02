#!/usr/bin/env python3
"""`mixhub.filters.agy_claude.main`: the PTY filter loop (`main`) wrapping the real `agy` binary, and its debug header."""

import os

try:
    import pty
except ImportError:
    pty = None
import select
import signal
import sys

try:
    import termios
except ImportError:
    termios = None
import time

try:
    import tty
except ImportError:
    tty = None

from mixhub.core.paths import AGY_FILTER
from mixhub.filters.agy_claude import stream as _stream
from mixhub.filters.agy_claude.cursor import PromptCursorTracker
from mixhub.filters.agy_claude.debuglog import MouseDebugLog
from mixhub.tmux.gateway import OUTER_SOCKET
from mixhub.filters.agy_claude.mouse import filter_stdin_mouse, _SelClear, sync_winsize
from mixhub.filters.agy_claude.prefs import is_mouse_shim_enabled, _resolve_real_agy
from mixhub.filters.agy_claude.stream import (
    coalesce_pty_output,
    CursorVisibilityGuard,
    exit_restore_bytes,
    transform_chunk,
)


REAL_AGY = _resolve_real_agy()


def _debug_header(dbg: MouseDebugLog, tracker: PromptCursorTracker, args: list[str]) -> None:
    def _stat(path: str) -> str:
        try:
            st = os.stat(path)
            return f"{path} size={st.st_size} mtime={time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(st.st_mtime))}"
        except OSError as exc:
            return f"{path} ({exc.__class__.__name__})"
    env = {k: os.environ.get(k, "") for k in ("TERM", "TERM_PROGRAM", "TERM_PROGRAM_VERSION", "LANG", "LC_ALL", "LC_CTYPE", "TMUX")}
    dbg.header = [
        f"# agy mouse debug  pid={os.getpid()} started={time.strftime('%Y-%m-%d %H:%M:%S')} level={dbg.level}",
        f"# filter {_stat(AGY_FILTER)}  python={sys.version.split()[0]}",
        f"# agy    {_stat(REAL_AGY)}  args={args}",
        f"# env    {env}  winsize={tracker.term_rows}x{tracker.term_cols}",
        "# Read: no `mouse` lines after clicking => Terminal.app sent nothing (View > Allow Mouse Reporting, Cmd-R);",
        "#       `click ... result='no-prompt'` => prompt box/caret not found on the replayed screen;",
        "#       `verify ... correction=` => first estimate was off and was corrected from agy's re-render.",
    ]


def main(argv: list[str] | None = None) -> int:
    global REAL_AGY
    _stream._IN_PTY_MAIN = True
    args = list(sys.argv[1:] if argv is None else argv)
    if args and os.path.isabs(args[0]) and os.path.isfile(args[0]) and os.access(args[0], os.X_OK):
        REAL_AGY = args.pop(0)
    else:
        REAL_AGY = _resolve_real_agy()
    raw_args = list(args)
    mouse_shim_on = is_mouse_shim_enabled(raw_args)
    args = [a for a in args if a not in ("--no-agy-mouse",)]
    for idx, a in enumerate(args):
        if a in ("--conversation", "-c") and idx + 1 < len(args):
            _stream.CURRENT_CID = args[idx + 1]
            break
    non_interactive = (
        os.environ.get("AGY_RAW_COLORS") == "1"
        or os.environ.get("AGY_FILTER_ACTIVE") == "1"
        or not mouse_shim_on
        or not sys.stdin.isatty()
        or not sys.stdout.isatty()
        or any(
            a in ("-p", "--print", "--prompt", "-v", "--version", "version", "help", "--help", "-h", "models", "mcp", "plugin", "plugins", "update", "auth", "login", "logout", "config", "completion")
            for a in args
        )
    )
    if non_interactive:
        os.execvp(REAL_AGY, ["agy", *args])

    if os.path.basename(sys.argv[0]) == "agy" and not any(
        a == "--mode" or a.startswith("--mode=") for a in args
    ):
        args = ["--mode", "auto", *args]

    os.environ["AGY_FILTER_ACTIVE"] = "1"
    stdin_fd = sys.stdin.fileno()
    stdout_fd = sys.stdout.fileno()
    orig_termios = termios.tcgetattr(stdin_fd)
    tracker = PromptCursorTracker()
    if not mouse_shim_on:
        tracker.disabled = True
    dbg = MouseDebugLog.from_env()
    tracker.dbg = dbg

    pid, master_fd = pty.fork()
    if pid == 0:
        os.execvp(REAL_AGY, ["agy", *args])

    sync_winsize(stdin_fd, master_fd, tracker)
    if dbg is not None:
        _debug_header(dbg, tracker, args)
        dbg.log("start", agy_pid=pid, filter_mouse_on="?1000h?1006h")
        signal.signal(signal.SIGUSR1, lambda signum, frame: dbg.flush("SIGUSR1"))

    sel_clear = _SelClear()

    def _on_sigwinch(signum, frame):
        sync_winsize(stdin_fd, master_fd, tracker)
        sel_clear.on_winch()
        tracker.top_row = None
        tracker.last_prompt_row = None
        tracker.need_dsr = True
        try:
            os.kill(pid, signal.SIGWINCH)
        except OSError:
            pass

    signal.signal(signal.SIGWINCH, _on_sigwinch)

    carry = b""
    # Enable SGR button click reporting (`?1000h` + `?1006h`); DSR (`\x1b[6n`) is queried after prompt renders
    if mouse_shim_on:
        try:
            os.write(stdout_fd, b"\x1b[?1000h\x1b[?1006h")
        except OSError:
            pass
    def _disable(where: str, exc: BaseException) -> None:
        # A bug in click support must never cost the user their session: fall back to plain passthrough
        tracker.disabled = True
        tracker._log("error", where=where, exc=repr(exc)[:300])
        if dbg is not None:
            dbg.flush("error")
        if not tracker.vs.ever_alt:
            try:
                os.write(stdout_fd, b"\x1b[?1000l\x1b[?1006l")
            except OSError:
                pass

    try:
        tty.setraw(stdin_fd)
        last_cjk_refresh = 0.0
        cursor_guard = CursorVisibilityGuard(in_tmux=bool(os.environ.get("TMUX")))
        while True:
            now_loop = time.monotonic()
            due_show = cursor_guard.flush_due(now_loop)
            if due_show:
                try:
                    os.write(stdout_fd, due_show)
                except OSError:
                    break
            t_wait = None if tracker.disabled else tracker.next_timeout()
            s_wait = sel_clear.next_timeout()
            if s_wait is not None:
                t_wait = s_wait if t_wait is None else min(t_wait, s_wait)
            c_wait = cursor_guard.next_timeout(now_loop)
            if c_wait is not None:
                t_wait = c_wait if t_wait is None else min(t_wait, c_wait)
            rlist, _, _ = select.select([stdin_fd, master_fd], [], [], t_wait)
            if stdin_fd in rlist:
                try:
                    in_bytes = os.read(stdin_fd, 4096)
                except OSError:
                    break
                if not in_bytes:
                    break
                try:
                    filtered_in = filter_stdin_mouse(in_bytes, tracker)
                except Exception as exc:
                    _disable("stdin", exc)
                    filtered_in = in_bytes
                if filtered_in:
                    cursor_guard.note_stdin(time.monotonic())
                    try:
                        sel_clear.on_stdin_bytes(
                            filtered_in,
                            tracker.term_rows or 40,
                            lambda b: os.write(master_fd, b),
                            prompt_sel_active=bool(tracker.prompt_sel),
                        )
                    except Exception:
                        pass
                    os.write(master_fd, filtered_in)
                    # When macOS Pinyin IME commits CJK characters, Terminal.app's local pre-edit
                    # overlay may have wrapped past the right edge onto the left tmux pane; refresh client.
                    if os.environ.get("TMUX") and any(b >= 0xE0 for b in filtered_in):
                        now_r = time.monotonic()
                        if now_r - last_cjk_refresh > 0.25:
                            last_cjk_refresh = now_r
                            try:
                                subprocess.Popen(
                                    ["env", "-u", "TMUX", "tmux", "-L", OUTER_SOCKET, "refresh-client"],
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL,
                                )
                            except Exception:
                                pass
            if master_fd in rlist:
                try:
                    out_bytes = os.read(master_fd, 65536)
                except OSError:
                    break
                if not out_bytes:
                    break
                out_bytes = coalesce_pty_output(master_fd, out_bytes)
                try:
                    sel_clear.on_output(out_bytes)
                except Exception:
                    pass
                if not tracker.disabled:
                    try:
                        tracker.update_from_output(out_bytes)
                    except Exception as exc:
                        _disable("output", exc)
                force_mouse = not (tracker.vs.ever_alt or tracker.disabled)
                transformed, carry = transform_chunk(out_bytes, carry, force_mouse=force_mouse)
                if tracker.need_dsr:
                    tracker.need_dsr = False
                    if not (tracker.vs.alt_active or tracker.disabled):
                        tracker._dsr_inflight += 1
                        tracker._log("dsr", action="injected", inflight=tracker._dsr_inflight)
                        transformed = transformed + b"\x1b[6n"
                if transformed:
                    pre_b, body_b, sleep_s = cursor_guard.prepare_write(transformed, time.monotonic())
                    if pre_b:
                        os.write(stdout_fd, pre_b)
                        if sleep_s > 0.0:
                            time.sleep(sleep_s)
                    if body_b:
                        os.write(stdout_fd, body_b)
            if not tracker.disabled:
                try:
                    extra = tracker.tick()
                except Exception as exc:
                    _disable("tick", exc)
                    extra = b""
                if extra:
                    os.write(master_fd, extra)
            try:
                sel_clear.on_timeout(lambda b: os.write(master_fd, b))
            except Exception:
                pass
            if dbg is not None:
                dbg.maybe_flush()
        if carry:
            os.write(stdout_fd, carry)
    finally:
        try:
            restore_b = exit_restore_bytes(tracker)
            if b"\x1b[?25h" not in restore_b:
                restore_b += b"\x1b[?25h"
            os.write(stdout_fd, restore_b)
        except OSError:
            pass
        if dbg is not None:
            dbg.log("exit", alt=tracker.vs.alt_active, app_mouse=sorted(tracker.vs.mouse_tracking))
            dbg.flush("exit")
        try:
            termios.tcsetattr(stdin_fd, termios.TCSAFLUSH, orig_termios)
        except termios.error:
            pass
        try:
            os.close(master_fd)
        except OSError:
            pass

    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status)
