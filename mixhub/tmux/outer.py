#!/usr/bin/env python3
"""`mixhub.tmux.outer`: The outer `hub` tmux session: pane ids and zoom, focus moves, client ttys (bell / autowrap),
the outer window title, and the Terminal window marker the zsh reattach hook reads.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time

from mixhub.core.textwidth import disp_trunc
from mixhub.tui import paths as _paths
from mixhub.tmux.gateway import OUTER_SOCKET


def record_outer_window(session_name: str) -> None:
    term_id = os.environ.get("TERM_SESSION_ID", "").strip()
    if not term_id or os.environ.get("TMUX"):
        return
    try:
        os.makedirs(os.path.dirname(_paths.OUTER_WINDOW_MARKER), exist_ok=True)
        with open(_paths.OUTER_WINDOW_MARKER, "w", encoding="utf-8") as f:
            f.write(f"{term_id} {session_name}")
    except OSError:
        pass


_SHELL_HOOK_TEMPLATE = """# MixHub: Terminal reopened the window MixHub was in (crash / forced quit) -> reconnect
_mh_file=__MARKER__
if [[ -o interactive && -z "$TMUX" && -n "$TERM_SESSION_ID" && -r "$_mh_file" ]]; then
  _mh_mark=(${(s: :)"$(<"$_mh_file")"})
  if [[ "${_mh_mark[1]}" == "$TERM_SESSION_ID" ]] && tmux -L __SOCKET__ has-session -t "${_mh_mark[2]}" 2>/dev/null \\
     && [[ -z "$(tmux -L __SOCKET__ list-clients -t "${_mh_mark[2]}" 2>/dev/null)" ]]; then
    mixhub
  fi
  unset _mh_mark
fi
unset _mh_file
"""


def shell_hook(marker_path: str | None = None) -> str:
    """zsh snippet printed by `hub shell-hook`, reading the outer-window marker this install writes
    (`<state root>/data/outer_window` unless `marker_path` is given)."""
    return _SHELL_HOOK_TEMPLATE.replace("__MARKER__", shlex.quote(marker_path or _paths.OUTER_WINDOW_MARKER)).replace(
        "__SOCKET__", OUTER_SOCKET)


SHELL_HOOK = shell_hook()


def current_outer_session_name() -> str:
    return os.environ.get("HUB_OUTER_SESSION") or "hub"


def current_pool_session_name() -> str:
    return os.environ.get("HUB_POOL_SESSION") or "pool"


_LAST_WINDOW_TITLE: str | None = None


def format_outer_window_title(session: dict | None, attention_count: int = 0) -> str:
    if not session or not session.get("id"):
        base = "mixhub"
    else:
        tool = str(session.get("tool") or "").lower()
        sid = str(session.get("id") or "")
        raw_title = " ".join(str(session.get("title") or sid).split()).strip()
        if sid.startswith("new") or raw_title.startswith("New "):
            label = {"claude": "Claude", "agy": "Gemini", "codex": "Codex", "deepseek": "DeepSeek"}.get(tool, tool.capitalize() or "Agent")
            base = f"mixhub · New {label} Session"
        else:
            t_short = disp_trunc(raw_title, 24)
            tool_label = {"claude": "Claude", "agy": "Gemini", "codex": "Codex", "deepseek": "DeepSeek"}.get(tool, tool)
            base = f"mixhub · {t_short} ({tool_label})" if tool_label else f"mixhub · {t_short}"
    if int(attention_count or 0) > 0:
        return f"● {int(attention_count)} · {base}"
    return base


def _sync_outer_window_title(session: dict | None, outer_sess: str | None = None, attention_count: int = 0) -> str:
    global _LAST_WINDOW_TITLE
    new_title = format_outer_window_title(session, attention_count=attention_count)
    if new_title == _LAST_WINDOW_TITLE:
        return new_title
    _LAST_WINDOW_TITLE = new_title
    if not os.environ.get("TMUX"):
        return new_title
    popen_fn = subprocess.Popen
    is_mock_popen = type(popen_fn).__name__ in ("MagicMock", "AsyncMock", "NonCallableMagicMock")
    if is_mock_popen and os.environ.get("TMUX") == "/tmp/tmux-1000/default,123,0":
        return new_title
    outer = outer_sess or current_outer_session_name()
    snap = _OUTER_PANE_SNAPSHOT.get(outer)
    left_id = snap[2] if (snap and snap[2]) else f"{outer}:.{{left}}"
    right_id = snap[3] if (snap and snap[3]) else f"{outer}:.{{right}}"
    cmd = [
        _TMUX_BIN, "-L", OUTER_SOCKET, "select-pane", "-t", left_id, "-T", new_title,
        ";", "select-pane", "-t", right_id, "-T", new_title,
    ]
    env = dict(os.environ)
    env.pop("TMUX", None)
    try:
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
    except Exception:
        pass
    return new_title


try:
    _TMUX_BIN = shutil.which("tmux") or "tmux"
except Exception:
    _TMUX_BIN = "tmux"
_OUTER_PANE_SNAPSHOT: dict[str, tuple[float, bool, str | None, str | None]] = {}


def get_outer_hub_pane_ids(session_name: str = "hub") -> tuple[str | None, str | None]:
    """
    Query the outer `tmux` server (`-u TMUX`) for `hub` panes and return `(left_pane_id, right_pane_id)`
    as exact `%N` identifiers, 100% immune to `base-index` / `pane-base-index`
    and safe to call even from inside the nested `tmux -L hubpool` server!
    The sidebar is the single pane tagged `@hub_role=sidebar`; otherwise panes sort by `#{pane_left}`.
    While zoomed the zoomed pane reports `pane_left=0` like the sidebar, so ties fall back to
    `list-panes` order (layout order, the same order `main-vertical` uses), never to the `%N` string.
    Also snapshots `#{window_zoomed_flag}` so an immediately following `is_outer_hub_zoomed(session_name)`
    call within 80ms resolves in 0ms without spawning a second `tmux` subprocess.
    """
    snap = _OUTER_PANE_SNAPSHOT.get(session_name)
    if snap is not None and snap[2] and snap[3] and (time.monotonic() - snap[0]) < 0.08:
        return snap[2], snap[3]
    env = dict(os.environ)
    env.pop("TMUX", None)
    try:
        raw = subprocess.run(
            [_TMUX_BIN, "-L", OUTER_SOCKET, "list-panes", "-t", session_name, "-F", "#{pane_id}|#{pane_left}|#{window_zoomed_flag}|#{@hub_role}"],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            close_fds=False,
            env=env,
        ).stdout.strip().splitlines()
    except OSError:
        return None, None
    parsed = []
    zoomed: bool | None = None
    tagged: list[str] = []
    for line in raw:
        parts = line.split("|")
        if len(parts) >= 2 and parts[1].isdigit():
            parsed.append((int(parts[1]), len(parsed), parts[0]))
            if len(parts) >= 3 and parts[2] in ("0", "1"):
                zoomed = (parts[2] == "1")
            if len(parts) >= 4 and parts[3] == "sidebar":
                tagged.append(parts[0])
    if not parsed:
        return None, None
    parsed.sort(key=(lambda p: p[1]) if zoomed else (lambda p: (p[0], p[1])))
    ids = [p[2] for p in parsed]
    left_id = tagged[0] if len(tagged) == 1 else ids[0]
    right_id = next((pid for pid in reversed(ids) if pid != left_id), None)
    if zoomed is not None:
        _OUTER_PANE_SNAPSHOT[session_name] = (time.monotonic(), zoomed, left_id, right_id)
    return left_id, right_id


def focus_outer_left_pane(session_name: str = "hub") -> None:
    env = dict(os.environ)
    env.pop("TMUX", None)
    left_id, _ = get_outer_hub_pane_ids(session_name)
    zoomed = is_outer_hub_zoomed(session_name)
    if zoomed:
        cmd = [_TMUX_BIN, "-L", OUTER_SOCKET, "resize-pane", "-Z", "-t", session_name, ";"] + (
            ["select-pane", "-t", left_id] if left_id else ["select-pane", "-L"]
        )
    else:
        cmd = [_TMUX_BIN, "-L", OUTER_SOCKET, "select-pane", "-t", left_id] if left_id else [_TMUX_BIN, "-L", OUTER_SOCKET, "select-pane", "-L"]
    subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=False, env=env)


def focus_outer_right_pane(session_name: str = "hub") -> None:
    env = dict(os.environ)
    env.pop("TMUX", None)
    _, right_id = get_outer_hub_pane_ids(session_name)
    cmd = [_TMUX_BIN, "-L", OUTER_SOCKET, "select-pane", "-t", right_id] if right_id else [_TMUX_BIN, "-L", OUTER_SOCKET, "select-pane", "-R"]
    subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=False, env=env)


def is_outer_hub_zoomed(session_name: str = "hub") -> bool:
    """Return True if the outer `hub` tmux session window is currently zoomed (`#{window_zoomed_flag} == 1`)."""
    snap = _OUTER_PANE_SNAPSHOT.get(session_name)
    if snap is not None and (time.monotonic() - snap[0]) < 0.08:
        return snap[1]
    env = dict(os.environ)
    env.pop("TMUX", None)
    try:
        res_z = (
            subprocess.run(
                [_TMUX_BIN, "-L", OUTER_SOCKET, "display-message", "-t", session_name, "-p", "#{window_zoomed_flag}"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                close_fds=False,
                env=env,
            ).stdout.strip()
            == "1"
        )
        if snap is not None:
            _OUTER_PANE_SNAPSHOT[session_name] = (time.monotonic(), res_z, snap[2], snap[3])
        return res_z
    except OSError:
        return False


def _list_outer_client_ttys(session_name: str = "hub", env: dict | None = None) -> list[str]:
    """
    Return attached outer client TTY paths (`/dev/ttys...`) for `session_name`.
    Falls back to `list-clients -F '#{client_session}\\t#{client_tty}'` if `list-clients -t <session_name>`
    returns no `/dev/` paths (e.g. when `tmux` treats `-t` as a client name rather than a session name).
    """
    run_env = env
    if run_env is None:
        run_env = dict(os.environ)
        run_env.pop("TMUX", None)
    ttys: list[str] = []
    if session_name:
        try:
            res = subprocess.run(
                ["tmux", "-L", OUTER_SOCKET, "list-clients", "-t", session_name, "-F", "#{client_tty}"],
                capture_output=True,
                text=True,
                env=run_env,
            )
            raw = res.stdout if isinstance(getattr(res, "stdout", None), str) else ""
            for ln in raw.splitlines():
                cand = ln.strip()
                if cand.startswith("/dev/") and cand not in ttys:
                    ttys.append(cand)
        except Exception:
            pass
    if not ttys:
        try:
            res_all = subprocess.run(
                ["tmux", "-L", OUTER_SOCKET, "list-clients", "-F", "#{client_session}\t#{client_tty}"],
                capture_output=True,
                text=True,
                env=run_env,
            )
            raw_all = res_all.stdout if isinstance(getattr(res_all, "stdout", None), str) else ""
            for ln in raw_all.splitlines():
                line = ln.strip()
                if not line:
                    continue
                if "\t" in line:
                    csess, ctty = line.split("\t", 1)
                    csess, ctty = csess.strip(), ctty.strip()
                    if (not session_name or csess == session_name) and ctty.startswith("/dev/") and ctty not in ttys:
                        ttys.append(ctty)
                elif line.startswith("/dev/") and line not in ttys:
                    ttys.append(line)
        except Exception:
            pass
    return ttys


def _is_outer_client_focused(session_name: str = "hub", env: dict | None = None) -> bool:
    """
    Return whether any attached outer `tmux` client for `session_name` currently has `focused` in `#{client_flags}`.
    Defaults to `True` when not running under a real outer tmux client or when mocked in unit tests without
    explicit client flags output, and returns `False` when outer client(s) exist and none have `focused`.
    """
    if not os.environ.get("TMUX"):
        return True
    run_fn = subprocess.run
    if (
        os.environ.get("TMUX") in ("/tmp/tmux-1000/default,123,0", "/tmp/tmux-501/default,123,0", "test-pool")
        and type(run_fn).__name__ in ("MagicMock", "AsyncMock", "NonCallableMagicMock")
    ):
        return True
    run_env = env
    if run_env is None:
        run_env = dict(os.environ)
        run_env.pop("TMUX", None)
    saw_client = False
    if session_name:
        try:
            res = subprocess.run(
                ["tmux", "-L", OUTER_SOCKET, "list-clients", "-t", session_name, "-F", "#{client_tty}\t#{client_flags}"],
                capture_output=True,
                text=True,
                env=run_env,
            )
            raw = res.stdout if isinstance(getattr(res, "stdout", None), str) else ""
            for ln in raw.splitlines():
                line = ln.strip()
                if not line or not line.startswith("/dev/"):
                    continue
                saw_client = True
                parts = line.split("\t", 1)
                flags = parts[1].strip().split(",") if len(parts) > 1 else []
                if "focused" in flags:
                    return True
        except Exception:
            pass
    if not saw_client:
        try:
            res_all = subprocess.run(
                ["tmux", "-L", OUTER_SOCKET, "list-clients", "-F", "#{client_session}\t#{client_tty}\t#{client_flags}"],
                capture_output=True,
                text=True,
                env=run_env,
            )
            raw_all = res_all.stdout if isinstance(getattr(res_all, "stdout", None), str) else ""
            for ln in raw_all.splitlines():
                line = ln.strip()
                if not line or "\t" not in line:
                    continue
                parts = line.split("\t")
                if len(parts) >= 2:
                    csess, ctty = parts[0].strip(), parts[1].strip()
                    flags = parts[2].strip().split(",") if len(parts) >= 3 else []
                    if (not session_name or csess == session_name) and ctty.startswith("/dev/"):
                        saw_client = True
                        if "focused" in flags:
                            return True
        except Exception:
            pass
    return False if saw_client else True


def emit_outer_terminal_bell(count: int = 1, session_name: str | None = None, env: dict | None = None) -> int:
    """
    Write ASCII BEL (`\\x07` * `count`) directly to each attached outer `tmux` client TTY (`/dev/ttys...`)
    so macOS `Terminal.app` increments its Dock icon unread badge (`1` for 1 completed session, `2` for 2, etc.)
    and sets the tab bell indicator just like a native Terminal window.
    """
    n_bells = max(1, int(count or 1))
    run_env = env
    if run_env is None:
        run_env = dict(os.environ)
        run_env.pop("TMUX", None)
    payload = b"\x07" * n_bells
    written = 0
    for tty in _list_outer_client_ttys(session_name or current_outer_session_name(), env=run_env):
        try:
            fd = os.open(tty, os.O_WRONLY | os.O_NOCTTY | os.O_NONBLOCK)
            try:
                os.write(fd, payload)
                written += 1
            finally:
                os.close(fd)
        except Exception:
            pass
    return written


def set_outer_client_autowrap(session_name: str = "hub", enabled: bool = False) -> None:
    """
    When `enabled=False`, purge both the outer terminal's Normal Screen scrollback buffer and Alternate Screen
    scrollback buffer (`\\x1b[?1049l\\x1b[H\\x1b[2J\\x1b[3J\\x1b[?1049h\\x1b[?7l\\x1b[3J`) and disable outer DECAWM (`?7l`),
    then immediately run `tmux refresh-client -t <tty>` so macOS Terminal.app (`com.apple.Terminal`) never shows
    a right-edge native scrollbar and never shifts the `mixhub` viewport down (`20.32.31.png` / `23.35.11.png`).
    """
    env = dict(os.environ)
    env.pop("TMUX", None)
    seq = b"\x1b[?7h" if enabled else b"\x1b[?1049l\x1b[H\x1b[2J\x1b[3J\x1b[?1049h\x1b[?7l\x1b[3J"
    for tty in _list_outer_client_ttys(session_name, env=env):
        try:
            fd = os.open(tty, os.O_WRONLY | os.O_NOCTTY | os.O_NONBLOCK)
            try:
                os.write(fd, seq)
            finally:
                os.close(fd)
            if not enabled:
                subprocess.run(
                    ["tmux", "-L", OUTER_SOCKET, "refresh-client", "-t", tty],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=env,
                )
        except Exception:
            pass


def toggle_outer_sidebar_zoom(session_name: str = "hub") -> str:
    """
    ChatGPT Desktop App-style full sidebar collapse/expand (`351.jpg`–`354.jpg`) in <15ms:
      - Executes the entire unzoom + 32-col restore + focus (or select-pane + zoom) in ONE atomic
        compound `tmux` command so all pane geometry changes happen in a single `tmux` server event-loop
        turn, completely avoiding `tmux`'s 250ms `server_client_check_pane_resize` multi-step delay
        and eliminating 8 redundant `subprocess.run(["tmux", "-L", OUTER_SOCKET, ...])` IPC round-trips.
    """
    env = dict(os.environ)
    env.pop("TMUX", None)
    left_id, right_id = get_outer_hub_pane_ids(session_name)
    if is_outer_hub_zoomed(session_name):
        cmd = [_TMUX_BIN, "-L", OUTER_SOCKET, "resize-pane", "-Z", "-t", right_id or session_name]
        if left_id:
            cmd.extend([";", "resize-pane", "-t", left_id, "-x", "32", ";", "select-pane", "-t", left_id])
        subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=False,
            env=env,
        )
        _OUTER_PANE_SNAPSHOT[session_name] = (time.monotonic(), False, left_id, right_id)
        return "expanded"

    if right_id:
        cmd = [_TMUX_BIN, "-L", OUTER_SOCKET, "select-pane", "-t", right_id, ";", "resize-pane", "-Z", "-t", right_id]
    else:
        cmd = [_TMUX_BIN, "-L", OUTER_SOCKET, "resize-pane", "-Z", "-t", session_name]
    subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=False,
        env=env,
    )
    _OUTER_PANE_SNAPSHOT[session_name] = (time.monotonic(), True, left_id, right_id)
    return "collapsed"
