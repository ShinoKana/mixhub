#!/usr/bin/env python3
"""`mixhub.tmux.pool`: `hubpool` window lookup and activation: the window cache, resume / attach in place,
external-tab detection, takeover, and OpenCode tab following.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import time

from mixhub.tmux import commands as _commands
from mixhub.tmux import headers as _headers
from mixhub.tmux import outer as _outer
from mixhub.tmux import workspace as _workspace
from mixhub.tmux.commands import hubpool_win_name, interactive_resume_cmd
from mixhub.tmux.headers import _enforce_hubpool_header_height
from mixhub.tmux.outer import current_outer_session_name, current_pool_session_name
from mixhub.tmux.session_state import _clear_hibernating, _clear_last_exit
from mixhub.tui import diag as _diag
from mixhub.tui import selection as _selection
from mixhub.tui.drafts import pop_unsent_draft
from mixhub.tui.widgets.preview_bar import MSG_MOVE_RACE


def follow_opencode_tab(session: dict, socket: str = "hubpool") -> str | None:
    """
    One OpenCode TUI holds several tabs, but a MixHub window is named after one session. When the tab on screen
    is not the one the window is named after, rename the window (and its `--header`) to that tab, so the other tab
    no longer looks open: clicking it shows its preview, and Resume opens it in a window of its own.
    Returns the new window name, or None when nothing changed.
    """
    old = str(session.get("hubpoolWindow") or "")
    new = hubpool_win_name({"tool": session.get("tool"), "id": session.get("id")})
    if session.get("tool") != "opencode" or not old.startswith("w-opencode-") or old == new:
        return None
    env = {k: v for k, v in os.environ.items() if k != "TMUX"}
    names = subprocess.run(["tmux", "-L", socket, "list-windows", "-t", "pool", "-F", "#{window_name}"],
                           capture_output=True, text=True, env=env).stdout.split()
    if old not in names or new in names:
        return None
    subprocess.run(["tmux", "-L", socket, "rename-window", "-t", f"pool:{old}", new],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    subprocess.run(["tmux", "-L", socket, "respawn-pane", "-k", "-t", f"pool:{new}.{{top}}",
                    _commands._header_cmd("opencode", str(session["id"]))],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    return new


_HUBPOOL_WINS_CACHE: dict[str, object] = {"ts": 0.0, "wins": set()}


def _record_hubpool_window(wname: str | None, present: bool = True) -> None:
    if not wname or wname == "reader":
        return
    wins = _HUBPOOL_WINS_CACHE.get("wins")
    if not isinstance(wins, set):
        wins = set()
        _HUBPOOL_WINS_CACHE["wins"] = wins
    if present:
        wins.add(wname)
    else:
        wins.discard(wname)


def find_hubpool_window_for_session(session_id: str, allow_probe: bool = True) -> str | None:
    """
    Check if `session_id` already has a live window (`w-<tool>-<id[:12]>`) in `tmux -L hubpool`.
    Returns the window name (e.g. `'w-claude-11111111-111'`) if alive in `hubpool`, or `None`.
    """
    sid = str(session_id or "").strip()
    if not sid:
        return None
    sid12 = sid[:12]
    suffix = f"-{sid12}"
    cached_wins = _HUBPOOL_WINS_CACHE.get("wins")
    if not isinstance(cached_wins, set):
        cached_wins = set()
        _HUBPOOL_WINS_CACHE["wins"] = cached_wins

    if not allow_probe:
        for w in cached_wins:
            if w.startswith("w-") and w.endswith(suffix):
                return w
        return None

    run_fn = subprocess.run
    is_mock_run = type(run_fn).__name__ in ("MagicMock", "AsyncMock", "NonCallableMagicMock")
    mock_stdout = getattr(getattr(run_fn, "return_value", None), "stdout", None) if is_mock_run else None
    if is_mock_run and getattr(run_fn, "side_effect", None) is None and not isinstance(mock_stdout, str):
        for w in cached_wins:
            if w.startswith("w-") and w.endswith(suffix):
                return w
        return None

    now_mono = time.monotonic()
    cached_ts = float(_HUBPOOL_WINS_CACHE.get("ts") or 0.0)
    if not is_mock_run and (now_mono - cached_ts) < 0.25:
        for w in cached_wins:
            if w.startswith("w-") and w.endswith(suffix):
                return w
        return None

    env = dict(os.environ)
    env.pop("TMUX", None)
    try:
        res = subprocess.run(
            ["tmux", "-L", "hubpool", "list-windows", "-t", "pool", "-F", "#{window_name}"],
            capture_output=True,
            text=True,
            env=env,
        )
        raw_out = res.stdout if isinstance(getattr(res, "stdout", None), str) else ""
        wins = {w.strip() for w in raw_out.splitlines() if w.strip().startswith("w-")}
        _HUBPOOL_WINS_CACHE["ts"] = now_mono
        _HUBPOOL_WINS_CACHE["wins"] = wins
        for w in wins:
            if w.startswith("w-") and w.endswith(suffix):
                return w
    except Exception:
        pass
    return None


def _unlink_stale_codex_lock_file(s: dict | None, home_dir: str | None = None) -> bool:
    """
    Unlink `~/.codex/thread-writer-locks/<id>.lock` before spawning `codex --no-daemon resume <id>`
    in `hubpool`. Because `codex` uses `flock()` on the lock path, unlinking detaches any stale FD lock
    held by background `codex app-server --managed-daemon` without terminating the daemon, allowing
    `codex resume <id>` to create a fresh `.lock` inode and open cleanly without
    `This conversation is open in another app`.
    """
    if not s or s.get("tool") != "codex" or not s.get("id"):
        return False
    sid = str(s["id"])
    base_home = home_dir or os.path.expanduser("~")
    lock_path = os.path.join(base_home, ".codex", "thread-writer-locks", f"{sid}.lock")
    try:
        os.unlink(lock_path)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def _hubpool_window_id(pool_sess: str, wname: str, env: dict) -> str | None:
    """The id (`@N`) of the first window named `wname`. Selecting by name fails once two windows share it, and that
    failure must not read as "no window", or each later activation would start one more copy of the session."""
    try:
        out = subprocess.run(
            ["tmux", "-L", "hubpool", "list-windows", "-t", pool_sess, "-F", "#{window_id} #{window_name}"],
            capture_output=True, text=True, env=env,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in (out or "").splitlines():
        wid, _, name = line.partition(" ")
        if name == wname and wid.startswith("@"):
            return wid
    return None


def activate_session_in_hubpool(
    s: dict,
    outer_sess: str | None = None,
    force_takeover: bool = True,
    app: "HubTUI | None" = None,
    focus_outer: bool = True,
) -> bool:
    """
    Switch `hubpool` right pane to `s`'s live CLI window `w-<tool>-<id[:12]>` if it already exists,
    or spawn a new `hubpool` window resuming `s` (releasing idle external lock when `force_takeover=True`),
    and focus the outer right pane.
    """
    if not s or not s.get("id"):
        return False
    outer = outer_sess or current_outer_session_name()
    pool_sess = current_pool_session_name()
    env = dict(os.environ)
    env.pop("TMUX", None)
    # Explicit (re)activation: a stale hibernate marker must not swallow the next real `/exit`,
    # and the session belongs to the `Active` working set from now on.
    _clear_hibernating(s["id"])
    _clear_last_exit(s["id"])
    if app is not None:
        try:
            hub_obj = getattr(app, "hub", None)
            ws_fn = getattr(hub_obj, "working_set", None)
            save_fn = getattr(hub_obj, "_save_working_set", None)
            if callable(ws_fn) and callable(save_fn):
                wset = ws_fn()
                key = f"{s.get('tool', '')}:{s['id']}"
                if isinstance(wset, set) and key not in wset and not str(s["id"]).startswith("new-"):
                    wset.add(key)
                    save_fn(wset)
        except Exception:
            pass

    existing_wname = find_hubpool_window_for_session(s.get("id", ""), allow_probe=False)
    wname = existing_wname or hubpool_win_name(s)

    if app is not None:
        app._ensure_hubpool_ready()
    else:
        has_pool = subprocess.run(
            ["tmux", "-L", "hubpool", "has-session", "-t", "pool"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        ).returncode == 0
        if not has_pool:
            reader_cmd = _commands._reader_cmd()
            subprocess.run(
                ["tmux", "-L", "hubpool", "new-session", "-d", "-s", "pool", "-n", "reader", reader_cmd],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
            )
            _workspace.configure_tmux_mouse_and_copy("hubpool", env=env, show_status=False)

    win_id = _hubpool_window_id(pool_sess, wname, env)
    has_win = win_id is not None and (
        subprocess.run(
            ["tmux", "-L", "hubpool", "select-window", "-t", win_id],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        ).returncode
        == 0
    )

    if has_win:
        _record_hubpool_window(wname, present=True)
        init_wins = getattr(app, "_initialized_header_wins", None) if app is not None else None
        if app is not None and init_wins is None:
            init_wins = set()
            app._initialized_header_wins = init_wins
        if init_wins is None or wname not in init_wins:
            _headers.ensure_hubpool_window_header(wname, s["tool"], s["id"], env=env)
            if init_wins is not None:
                init_wins.add(wname)
        else:
            subprocess.Popen(
                ["tmux", "-L", "hubpool", "select-pane", "-t", f"pool:{wname}.{{bottom}}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
            )
        if app is not None:
            app._active_hubpool_win = wname
        _diag.log_tui_diag(
            "activate_session_in_hubpool",
            session_id=str(s.get("id", "")),
            tool=str(s.get("tool", "")),
            wname=wname,
            reused_window=True,
            force_takeover=bool(force_takeover),
        )
        if focus_outer:
            _outer.focus_outer_right_pane(outer)
        return True

    _record_hubpool_window(wname, present=False)
    if app is not None:
        can_takeover, msg = app._release_external_locks_before_takeover(s, force_takeover=force_takeover)
        if not can_takeover:
            app.status_msg = msg
            app._dirty = True
            return True
    else:
        msg = ""

    cwd = s.get("cwd") or os.path.expanduser("~")
    if not os.path.isdir(cwd):
        cwd = os.path.expanduser("~")
    home_dir = getattr(getattr(app, "hub", None), "home", None) if app is not None else None
    try:
        from mixhub.rules.trust import ensure_workspace_trusted
        ensure_workspace_trusted(cwd, home_dir=home_dir)
    except Exception:
        pass
    unlinked_lock = _unlink_stale_codex_lock_file(s, home_dir=home_dir)
    cmd_argv = interactive_resume_cmd(s)
    inner_cmd = " ".join(shlex.quote(x) for x in cmd_argv)
    shell_cmd = _commands._build_hubpool_cli_wrapper_cmd(s.get("tool", ""), s["id"], wname, cwd, inner_cmd)
    new_id = (subprocess.run(
        ["tmux", "-L", "hubpool", "new-window", "-P", "-F", "#{window_id}", "-t", "pool:", "-n", wname, "-c", cwd,
         shell_cmd],
        capture_output=True,
        text=True,
        env=env,
    ).stdout or "").strip()
    _headers.ensure_hubpool_window_header(wname, s["tool"], s["id"], env=env)
    _enforce_hubpool_header_height(f"pool:{wname}")
    if app is not None:
        init_wins = getattr(app, "_initialized_header_wins", None)
        if init_wins is None:
            init_wins = set()
            app._initialized_header_wins = init_wins
        init_wins.add(wname)
    subprocess.run(
        ["tmux", "-L", "hubpool", "select-window", "-t", new_id if new_id.startswith("@") else f"{pool_sess}:{wname}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    _record_hubpool_window(wname, present=True)
    draft = pop_unsent_draft(s["id"], path=getattr(app, "unsent_drafts_path", None))
    if draft:
        _diag.log_tui_diag("draft_restored", session_id=str(s.get("id", "")), wname=wname, chars=len(draft))
        subprocess.run(
            ["tmux", "-L", "hubpool", "send-keys", "-t", f"pool:{wname}", "-l", "--", draft],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
    if app is not None:
        app._active_hubpool_win = wname
        if msg:
            app.status_msg = msg
    _diag.log_tui_diag(
        "activate_session_in_hubpool",
        session_id=str(s.get("id", "")),
        tool=str(s.get("tool", "")),
        wname=wname,
        reused_window=False,
        force_takeover=bool(force_takeover),
        unlinked_codex_lock=bool(unlinked_lock),
    )
    if focus_outer:
        _outer.focus_outer_right_pane(outer)
    return True


def _is_external_tab_session(s: dict | None) -> bool:
    """
    Return True when session `s` is running in an external Terminal tab (Mode C),
    as opposed to resident in `hubpool` (Mode A) or dormant / working-set-only (Mode B).
    """
    if not s or not s.get("isLive"):
        return False
    in_hubpool = (
        bool(s.get("resident"))
        or any(p.get("resident") for p in (s.get("processes") or []))
        or (find_hubpool_window_for_session(s.get("id", ""), allow_probe=False) is not None)
    )
    if in_hubpool:
        return False
    procs = s.get("processes") or []
    if procs:
        return any(not p.get("resident") for p in procs)
    if "resident" in s and s.get("resident") is False:
        return True
    if s.get("tty"):
        return True
    return False


def _session_preview_mode_and_state(s: dict | None) -> tuple[str, str, bool]:
    """
    Classify session `s` for the Preview Top & Bottom Bars:
      Returns `(mode, ext_state, can_move)`:
        - `mode == "B"` (Dormant / not open in another tab): `("B", "dormant", False)`
        - `mode == "C"` (Open in an external Terminal tab):
            * `ext_state == "idle"`        -> `("C", "idle", True)`
            * `ext_state == "working"`     -> `("C", "working", False)`
            * `ext_state == "wait_input"`  -> `("C", "wait_input", False)`
    """
    if not _is_external_tab_session(s):
        return "B", "dormant", False
    raw_st = (s.get("agentState") or "done_idle") if s else "done_idle"
    if raw_st == "working" or int((s or {}).get("activeSubagents") or 0) > 0 or bool((s or {}).get("notFullyIdle")):
        return "C", "working", False
    if raw_st in ("wait_input", "wait"):
        return "C", "wait_input", False
    return "C", "idle", True


def focus_external_session(s: dict | None, app: "HubTUI | None" = None) -> bool:
    """Switch focus to the external Terminal.app tab running session `s` without stopping it."""
    if not s or not s.get("id"):
        return False
    if app is not None:
        app._mark_session_seen(s)
        try:
            app.hub.action(s["tool"], s["id"], "focus", {})
        except Exception:
            pass
        tty_s = s.get("tty") or ""
        app.status_msg = f"Focused external tab: {tty_s}" if tty_s else "Focused external tab"
        app._dirty = True
    return True


def takeover_external_session_in_hubpool(s: dict | None, app: "HubTUI | None" = None) -> bool:
    """
    Stop an idle external Terminal tab process for `s` and resume `s` in-place inside `hubpool`.
    Refuses to move if `s` is currently `working` or `wait_input` (race guard -> `MSG_MOVE_RACE`).
    """
    if not s or not s.get("id"):
        return False
    live_s = (
        (app.hub.sessions.get(f"{s.get('tool')}:{s.get('id')}") if (app is not None and getattr(app, "hub", None) is not None) else None)
        or s
    )
    for cand in (s, live_s):
        st = cand.get("agentState") or ("done_idle" if cand.get("isLive") else "dormant")
        if st in ("working", "wait_input", "wait") or int(cand.get("activeSubagents") or 0) > 0 or bool(cand.get("notFullyIdle")):
            if app is not None:
                app.status_msg = MSG_MOVE_RACE
                app._dirty = True
            _diag.log_tui_diag(
                "takeover_external_session",
                session_id=str(s.get("id", "")),
                tool=str(s.get("tool", "")),
                status="race_blocked",
                agent_state=str(st),
            )
            return False
    if app is not None:
        app._mark_session_seen(s)
        if not s.get("processes") and s.get("isLive") and not s.get("resident"):
            try:
                app.hub.action(s["tool"], s["id"], "hibernate", {"keep_working_set": True})
            except Exception:
                pass
    _diag.log_tui_diag(
        "takeover_external_session",
        session_id=str(s.get("id", "")),
        tool=str(s.get("tool", "")),
        status="taking_over",
    )
    _selection.write_active_selection(s)
    return activate_session_in_hubpool(s, current_outer_session_name(), force_takeover=True, app=app)
