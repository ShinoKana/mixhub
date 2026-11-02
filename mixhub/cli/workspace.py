#!/usr/bin/env python3
"""`mixhub.cli.workspace`: the outer tmux workspace (`hub workspace|tmux`), window title (`hub title`), attach / detach hooks and `hub reload`."""
import json
import re
import os
import shlex
import shutil
import subprocess
import sys
import time

from mixhub.core.launch import CLI_MODULE, module_command
from mixhub.core.paths import ROOT, STATE_ROOT  # noqa: F401  (`ROOT`: the code root, kept for importers)
from mixhub.i18n import t
from mixhub.tmux.gateway import MIN_TMUX, OUTER_SOCKET, tmux_version_tuple

def _outer_tmux_env():
    env = dict(os.environ)
    env.pop("TMUX", None)
    return env


def _sidebar_pane_id(session_name="hub"):
    result = subprocess.run(
        ["tmux", "-L", OUTER_SOCKET, "list-panes", "-t", session_name, "-F", "#{pane_id}|#{pane_left}|#{window_zoomed_flag}"],
        capture_output=True, text=True, env=_outer_tmux_env(),
    )
    if result.returncode != 0:
        return None, 0
    panes = []
    zoomed = False
    for line in result.stdout.splitlines():
        pane_id, _, rest = line.partition("|")
        left, _, flag = rest.partition("|")
        if pane_id.startswith("%") and left.isdigit():
            panes.append((int(left), len(panes), pane_id))
            zoomed = zoomed or flag == "1"
    # A zoomed pane reports pane_left=0 like the sidebar; list-panes order is layout order.
    if zoomed:
        return (panes[0][2] if panes else None), len(panes)
    return (min(panes)[2] if panes else None), len(panes)


def _selected_session(session_name="hub"):
    suffix = "" if session_name == "hub" else "_" + "".join(
        char if char.isascii() and (char.isalnum() or char in "_-") else "_"
        for char in session_name
    )
    path = os.path.join(STATE_ROOT, "data", "active_selection" + suffix + ".json")
    try:
        with open(path, encoding="utf-8") as selected_file:
            selected = json.load(selected_file)
        return selected if isinstance(selected, dict) and not selected.get("empty") else None
    except (OSError, ValueError):
        return None


def compute_window_title(session_name="hub") -> str:
    selected = _selected_session(session_name) or {}
    title_chars = []
    for char in str(selected.get("title") or ""):
        code = ord(char)
        forbidden = 0x1F300 <= code <= 0x1FAFF or code in (0x2615, 0x2693, 0x26A0, 0x2B06, 0x2B07)
        title_chars.append(char if char != "#" and char.isprintable() and not forbidden else " ")
    title_text = "".join(title_chars)
    active_title = " ".join(title_text.split())[:24].strip()
    tool_label = {"claude": "Claude", "agy": "Gemini", "codex": "Codex"}.get(selected.get("tool"), "")

    approval_count = 0
    live_count = 0
    pool_win_set = set()
    try:
        windows = subprocess.run(
            ["tmux", "-L", "hubpool", "list-windows", "-t", "pool", "-F", "#{window_name}"],
            capture_output=True, text=True, env=_outer_tmux_env(),
        )
        if windows.returncode == 0:
            pool_win_set = {name.strip() for name in windows.stdout.splitlines() if name.strip().startswith("w-")}
            live_count = len(pool_win_set)
    except OSError:
        pass

    try:
        attn_path = os.path.join(STATE_ROOT, "data", "attention.json")
        if os.path.isfile(attn_path):
            with open(attn_path, encoding="utf-8") as attn_file:
                attn_data = json.load(attn_file)
            if isinstance(attn_data, dict):
                keys = attn_data.get("keys")
                if isinstance(keys, list):
                    approval_count = len([k for k in keys if k])
    except Exception:
        pass

    if approval_count:
        return f"●{approval_count} mixhub · {active_title}" if active_title else f"●{approval_count} {t('tmux.title.pending')} · mixhub"
    if active_title:
        return f"mixhub · {active_title} ({tool_label})" if tool_label else f"mixhub · {active_title}"
    if live_count:
        return "mixhub · " + t("tmux.title.running", n=live_count)
    return "mixhub"


def apply_window_title_and_hooks(session_name="hub") -> str:
    title = compute_window_title(session_name)
    env = _outer_tmux_env()
    pane_id, _ = _sidebar_pane_id(session_name)
    if pane_id:
        active_pane = None
        try:
            ap = subprocess.run(
                ["tmux", "-L", OUTER_SOCKET, "display-message", "-p", "-t", session_name, "#{pane_id}"],
                capture_output=True, text=True, env=env,
            )
            if ap.returncode == 0 and ap.stdout.strip().startswith("%"):
                active_pane = ap.stdout.strip()
        except OSError:
            pass
        subprocess.run(["tmux", "-L", OUTER_SOCKET, "set-option", "-p", "-t", pane_id, "@hub_role", "sidebar"],
                       check=True, env=env)
        subprocess.run(["tmux", "-L", OUTER_SOCKET, "select-pane", "-t", pane_id, "-T", title], check=True, env=env)
        if active_pane and active_pane != pane_id:
            subprocess.run(["tmux", "-L", OUTER_SOCKET, "select-pane", "-t", active_pane], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)

    exited = t("tmux.sidebar_exited").replace(",", " ").replace("}", "").replace("#", "")  # inside a tmux format
    title_format = "#{P:#{?#{==:#{@hub_role},sidebar},#{?pane_dead,◈ mixhub · " + exited + ",#{pane_title}},}}"
    for cmd in (
        ["set-option", "-g", "set-titles", "on"],
        ["set-option", "-t", session_name, "set-titles", "on"],
        ["set-option", "-g", "set-titles-string", title_format],
        ["set-option", "-t", session_name, "set-titles-string", title_format],
        ["set-option", "-t", session_name, "status-right", ""],
        ["unbind-key", "-T", "root", "MouseDown1StatusRight"],
    ):
        subprocess.run(["tmux", "-L", OUTER_SOCKET, *cmd], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)

    for event, command in (("client-detached", "on-detach"), ("client-attached", "on-attach")):
        hook = "run-shell -b " + shlex.quote(shlex.join(module_command(CLI_MODULE, command, session_name)))
        subprocess.run(["tmux", "-L", OUTER_SOCKET, "set-hook", "-g", f"{event}[90]", hook], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    ttys = []
    clients = subprocess.run(
        ["tmux", "-L", OUTER_SOCKET, "list-clients", "-t", session_name, "-F", "#{client_tty}"],
        capture_output=True, text=True, env=env,
    )
    if clients.returncode == 0:
        ttys = [line.strip() for line in clients.stdout.splitlines() if line.strip().startswith("/dev/")]
    if not ttys:
        clients_all = subprocess.run(
            ["tmux", "-L", OUTER_SOCKET, "list-clients", "-F", "#{client_session}\t#{client_tty}"],
            capture_output=True, text=True, env=env,
        )
        if clients_all.returncode == 0:
            for line in clients_all.stdout.splitlines():
                csess, _, ctty = line.strip().partition("\t")
                if csess.strip() == session_name and ctty.strip().startswith("/dev/"):
                    ttys.append(ctty.strip())
    if ttys:
        osc = f"\x1b]0;{title}\x07\x1b]2;{title}\x07"
        for tty in ttys:
            try:
                with open(tty, "w", encoding="utf-8", errors="ignore") as tty_file:
                    tty_file.write(osc)
                    tty_file.flush()
            except OSError:
                pass
            subprocess.run(["tmux", "-L", OUTER_SOCKET, "refresh-client", "-t", tty], check=False,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    return title


def _write_presence(state, session_name):
    path = os.path.join(STATE_ROOT, "data", "presence_state.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as presence_file:
        json.dump({"state": state, "updated_at": time.time(), "session": session_name}, presence_file)


def run_on_detach(session_name="hub"):
    try:
        from mixhub.tui.drafts import capture_and_save_hubpool_draft
        selected = _selected_session(session_name)
        if selected:
            capture_and_save_hubpool_draft(selected)
    except Exception:
        pass
    _write_presence("away", session_name)
    return 0


def run_on_attach(session_name="hub"):
    _write_presence("present", session_name)
    try:
        from mixhub.tmux.outer import set_outer_client_autowrap
        set_outer_client_autowrap(session_name, enabled=False)
    except Exception:
        pass
    apply_window_title_and_hooks(session_name)
    return 0


def run_reload():
    """Restart the sidebar, reader and session headers on the code installed now; agent sessions keep running."""
    from mixhub.cli.daemon import reload_workspace_panes

    respawned = reload_workspace_panes()
    if not any(p.startswith("outer:") for p in respawned):
        raise RuntimeError("MixHub sidebar pane not found")
    try:
        from mixhub.tmux.outer import set_outer_client_autowrap
        set_outer_client_autowrap("hub", enabled=False)
    except Exception:
        pass
    apply_window_title_and_hooks("hub")
    print(f"reloaded {len(respawned)} MixHub panes · agent sessions kept running")
    return 0


def _tmux_version_text() -> str:
    try:
        return subprocess.run(["tmux", "-V"], capture_output=True, text=True, timeout=2).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _workspace_unavailable() -> str:
    """Why the tmux workspace can't start here (empty when it can); `mixhub web` works everywhere."""
    hint = "brew install tmux" if sys.platform == "darwin" else "install tmux with your package manager"
    if not shutil.which("tmux"):
        return f"The MixHub workspace needs tmux ({hint}). `mixhub web` works without it."
    text = _tmux_version_text()
    version = tmux_version_tuple(text)
    if version is not None and version < MIN_TMUX:
        need = ".".join(map(str, MIN_TMUX))
        return f"The MixHub workspace needs tmux {need} or newer; this is {text} ({hint}). `mixhub web` works without it."
    return ""


def retire_legacy_outer_sessions() -> None:
    """Close outer sessions MixHub < 0.2.1 left on the user's default tmux server: only `hub` / `hub-N` sessions with a
    pane tagged `@hub_role=sidebar` and no attached client. The agents live in `hubpool` and keep running."""
    env = {k: v for k, v in os.environ.items() if k != "TMUX"}
    try:
        res = subprocess.run(["tmux", "list-sessions", "-F", "#{session_name}\t#{session_attached}"],
                             capture_output=True, text=True, env=env, timeout=2)
    except (OSError, subprocess.SubprocessError):
        return
    for line in (res.stdout or "").splitlines():
        name, _, attached = line.partition("\t")
        if not re.fullmatch(r"hub(-\d+)?", name) or attached.strip() not in ("", "0"):
            continue
        try:
            panes = subprocess.run(["tmux", "list-panes", "-s", "-t", name, "-F", "#{@hub_role}"],
                                   capture_output=True, text=True, env=env, timeout=2).stdout or ""
            if "sidebar" in panes.split():
                subprocess.run(["tmux", "kill-session", "-t", name], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, env=env, timeout=2)
        except (OSError, subprocess.SubprocessError):
            continue


def run_workspace():
    reason = _workspace_unavailable()
    if reason:
        raise RuntimeError(reason)
    from mixhub.tmux.outer import record_outer_window, set_outer_client_autowrap
    from mixhub.tmux.workspace import (
        allocate_workspace_session_names,
        configure_tmux_mouse_and_copy,
        ensure_outer_dual_pane_layout,
        outer_attach,
    )
    retire_legacy_outer_sessions()
    session_name, pool_session = allocate_workspace_session_names()
    env = _outer_tmux_env()
    exists = subprocess.run(["tmux", "-L", OUTER_SOCKET, "has-session", "-t", session_name],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env).returncode == 0
    _, pane_count = _sidebar_pane_id(session_name) if exists else (None, 0)
    if not exists or pane_count < 2:
        ensure_outer_dual_pane_layout(session_name, pool_session)
    else:
        configure_tmux_mouse_and_copy(None, env=env, show_status=False, session_target=session_name)
        set_outer_client_autowrap(session_name, enabled=False)
    title = apply_window_title_and_hooks(session_name)
    if sys.stdout.isatty():
        clear_seq = "" if os.environ.get("TMUX") else "\x1b[?7l\x1b[H\x1b[2J\x1b[3J"
        sys.stdout.write(f"{clear_seq}\x1b]0;{title}\x07\x1b]2;{title}\x07")
        sys.stdout.flush()
    tmux_bin = shutil.which("tmux")
    if not tmux_bin:
        raise RuntimeError("tmux executable not found")
    args, attach_env = outer_attach(session_name)
    record_outer_window(session_name)
    os.execve(tmux_bin, ["mixhub", *args], attach_env)


__all__ = [
    "apply_window_title_and_hooks",
    "compute_window_title",
    "run_on_attach",
    "run_on_detach",
    "run_reload",
    "run_workspace",
]
