#!/usr/bin/env python3
"""`mixhub.cli.daemon`: the Web daemon (`hub start|stop|status|open|web`: `health`, `start`, `stop`) and tmux pane reload helpers."""
from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import time
import urllib.request

from mixhub.core.env import env
from mixhub.core.launch import TUI_MODULE, WEB_MODULE, child_env, module_argv, module_shell, runs_module
from mixhub.core.paths import ROOT, STATE_ROOT, ensure_state_root
from mixhub.tmux.gateway import DEFAULT_TMUX_GATEWAY, TmuxGateway

URL = "http://127.0.0.1:" + env("PORT", "8765")
PID_FILE = os.path.join(STATE_ROOT, ".hub.pid")


def health():
    try:
        with urllib.request.urlopen(URL + "/api/health", timeout=0.25) as response:
            value = json.load(response)
            return value if value.get("service") in ("agent-session-hub", "mixhub") else None
    except (OSError, ValueError):
        return None


def start():
    live = health()
    if live:
        print("running · pid %s · %s" % (live["pid"], URL))
        return
    state_dir = ensure_state_root()
    log_path = os.path.join(state_dir, "hub.log")
    with open(log_path, "ab") as log:
        os.chmod(log_path, 0o600)
        python = env("PYTHON") or sys.executable  # the Python that runs `hub` (>= 3.10)
        process = subprocess.Popen(module_argv(WEB_MODULE, no_site=True, python=python), cwd=state_dir,
                                   env=child_env(python=python), stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   start_new_session=True)
    with open(PID_FILE, "w") as f:
        f.write(str(process.pid))
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Hub exited; inspect " + log_path)
        if health():
            print("started · pid %s · %s" % (process.pid, URL))
            return
        time.sleep(0.025)
    process.terminate()
    raise RuntimeError("Hub did not become ready; inspect " + log_path)


def is_web_daemon_command(command: str) -> bool:
    """`python -m mixhub.web` (any install), or the legacy `<this checkout>/server.py` daemon started before it."""
    return runs_module(command, WEB_MODULE) or os.path.join(ROOT, "server.py") in command


def stop():
    live = health()
    if not live:
        print("stopped")
        return
    try:
        with open(PID_FILE) as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        pid = int(live["pid"])
    command = subprocess.check_output(["ps", "-p", str(pid), "-o", "command="], text=True).strip()
    if pid != live["pid"] or not is_web_daemon_command(command):
        raise RuntimeError("PID identity changed; refusing to signal another process")
    os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + 3
    while health() and time.monotonic() < deadline:
        time.sleep(0.05)
    if health():
        raise RuntimeError("Hub is still stopping")
    if os.path.exists(PID_FILE):
        os.unlink(PID_FILE)
    print("stopped")


_TUI_PANE_FLAGS = ("--sidebar", "--reader", "--header")
_SESSION_ENV = ("HUB_OUTER_SESSION", "HUB_POOL_SESSION")


def current_tui_pane_command(old_cmd: str) -> str | None:
    """The pane command a TUI pane started with `old_cmd` (any launch form, old `tui.py` included) gets today: same
    flags and session env, current interpreter and `python -m mixhub.tui` form. None for other panes."""
    try:
        argv = shlex.split(old_cmd)
        if len(argv) == 1:  # tmux reports the command quoted as one word
            argv = shlex.split(argv[0])
    except ValueError:
        return None
    flag_at = next((i for i, a in enumerate(argv) if a in _TUI_PANE_FLAGS), None)
    if flag_at is None:
        return None
    extra_env = {}
    for token in argv[:flag_at]:
        key, eq, value = token.partition("=")
        if eq and key in _SESSION_ENV:
            extra_env[key] = value
    return module_shell(TUI_MODULE, *argv[flag_at:], extra_env=extra_env or None)


def reload_workspace_panes(gateway: TmuxGateway = DEFAULT_TMUX_GATEWAY) -> list[str]:
    """
    Respawn the `--sidebar`, `--reader` and `--header` panes (found by `pane_start_command`, never by hardcoded
    `%2` / `%15`) with the current launch command, so they run the code installed now.
    """
    respawned: list[str] = []
    for srv, sess in (("outer", "hub"), ("pool", "pool")):
        lines = gateway.list_panes(sess, fmt="#{pane_id}|#{pane_start_command}", server=srv, all_windows=True)
        for ln in lines:
            parts = ln.split("|", 1)
            if len(parts) != 2:
                continue
            pid, cmd = parts[0].strip(), parts[1].strip()
            new_cmd = current_tui_pane_command(cmd)
            if new_cmd:
                gateway.run(
                    ["respawn-pane", "-k", "-t", pid, new_cmd],
                    server=srv,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    text=False,
                )
                respawned.append(f"{srv}:{pid}")
    return respawned






__all__ = ["PID_FILE", "URL", "health", "is_web_daemon_command", "reload_workspace_panes", "start", "stop"]
