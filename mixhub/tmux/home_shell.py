#!/usr/bin/env python3
"""`mixhub.tmux.home_shell`: The plain login shell under the reader on Home, parked in a hidden `hubpool` window
while a session is shown.
"""
from __future__ import annotations

import os
import subprocess

from mixhub.proc.classify import _is_headless_invocation, classify_proc_args
from mixhub.tui import paths as _paths

HOME_SHELL_MARK = "MIXHUB_HOME_SHELL=1"
# Set on a window an agent was moved into from the Home shell: `<tool> <new-N id>`
ADOPTED_OPT = "@mixhub_adopted"
_AGENT_TOOLS = {"claude": "claude", "codex": "codex", "agy": "agy", "opencode": "opencode"}
HOME_SHELL_PARKED = "mh-shell"  # hidden `pool` window holding the Home shell while a session is shown
HOME_SHELL_DEFAULT_SIZE = "50%"  # Home and the shell split the height until the divider is dragged


def _home_shell_rows() -> str:
    try:
        with open(_paths.HOME_SHELL_HEIGHT_PATH, encoding="utf-8") as f:
            return str(max(3, int(f.read().strip())))
    except (OSError, ValueError):
        return HOME_SHELL_DEFAULT_SIZE


def sync_home_shell(home: bool, reader_pane: str, socket: str = "hubpool") -> None:
    """Home shows a plain login shell in the lower half of the reader window (keep a normal
    command line on Home). Leaving Home parks that same pane in a hidden window, so the shell and its history live on."""
    if not reader_pane:
        return
    env = dict(os.environ)
    env.pop("TMUX", None)

    def tmux(*args):
        return subprocess.run(["tmux", "-L", socket, *args], capture_output=True, text=True, env=env, timeout=3)

    try:
        rows = tmux("list-panes", "-a", "-F",
                    f"#{{pane_id}}\t#{{window_id}}\t#{{pane_height}}\t#{{{ADOPTED_OPT}}}\t#{{pane_start_command}}").stdout.splitlines()
        reader_win = next((r.split("\t")[1] for r in rows if r.split("\t")[0] == reader_pane), None)
        shell = next((r.split("\t") for r in rows if HOME_SHELL_MARK in r and not r.split("\t")[3]), None)
        if not reader_win:
            return
        if home:
            if shell is None:
                cmd = f'exec env {HOME_SHELL_MARK} "${{SHELL:-/bin/zsh}}" -l'
                shell_pane = tmux("split-window", "-d", "-P", "-F", "#{pane_id}", "-v", "-l", str(_home_shell_rows()),
                                  "-t", reader_pane, "-c", os.path.expanduser("~"), cmd).stdout.strip()
            else:
                shell_pane = shell[0]
                if shell[1] != reader_win:
                    tmux("join-pane", "-d", "-v", "-l", str(_home_shell_rows()), "-s", shell_pane, "-t", reader_pane)
            # Ready to type on arrival; the reader above is click-only and mouse clicks reach it regardless of focus
            tmux("select-pane", "-t", shell_pane or reader_pane)
        elif shell is not None and shell[1] == reader_win:
            try:
                os.makedirs(os.path.dirname(_paths.HOME_SHELL_HEIGHT_PATH), exist_ok=True)
                with open(_paths.HOME_SHELL_HEIGHT_PATH, "w", encoding="utf-8") as f:
                    f.write(shell[2])
            except OSError:
                pass
            tmux("break-pane", "-d", "-s", shell[0], "-n", HOME_SHELL_PARKED)
    except (OSError, subprocess.SubprocessError):
        pass


def _tmux(socket: str, *args) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("TMUX", None)
    return subprocess.run(["tmux", "-L", socket, *args], capture_output=True, text=True, env=env, timeout=3)


def foreground_agent(ps_out: str) -> str | None:
    """The agent (`claude` / `codex` / `agy` / `opencode`) running interactively in the foreground of a tty, from
    `ps -o pgid=,tpgid=,args=` lines. One-shot runs (`claude -p`, `codex exec`) don't count."""
    for ln in ps_out.splitlines():
        parts = ln.split(None, 2)
        if len(parts) < 3 or parts[0] != parts[1]:
            continue
        fam, kind, tail = classify_proc_args(parts[2])
        if kind == "agent" and fam in _AGENT_TOOLS and not _is_headless_invocation(fam, tail):
            return _AGENT_TOOLS[fam]
    return None


def _tty_agent(tty: str) -> str | None:
    if not tty:
        return None
    res = subprocess.run(["ps", "-o", "pgid=,tpgid=,args=", "-t", tty.removeprefix("/dev/")],
                         capture_output=True, text=True, timeout=3)
    return foreground_agent(res.stdout)


def home_shell_agent(socket: str = "hubpool") -> tuple[str, str, str] | None:
    """`(pane_id, tool, cwd)` when an agent was started from the Home shell and is running there, else None."""
    try:
        rows = _tmux(socket, "list-panes", "-a", "-F",
                     f"#{{pane_id}}\t#{{pane_tty}}\t#{{pane_current_path}}\t#{{{ADOPTED_OPT}}}\t#{{pane_start_command}}")
        for r in rows.stdout.splitlines():
            pane, tty, cwd, adopted, start = (r.split("\t", 4) + [""] * 5)[:5]
            if HOME_SHELL_MARK in start and not adopted:
                tool = _tty_agent(tty)
                return (pane, tool, cwd) if tool else None
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def adopt_home_shell_pane(pane: str, wname: str, tool: str, sid: str, socket: str = "hubpool") -> bool:
    """Move the Home shell pane, running agent and all, into its own `pool` window `wname`, the way `✎ New session`
    opens one, so Home keeps a free command line: the next trip Home starts a fresh shell."""
    try:
        if _tmux(socket, "break-pane", "-d", "-s", pane, "-n", wname).returncode:
            return False
        _tmux(socket, "set-option", "-w", "-t", pane, ADOPTED_OPT, f"{tool} {sid}")
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def finished_adopted_windows(socket: str = "hubpool") -> list[tuple[str, str, str]]:
    """`(window, tool, new-N id)` for windows moved out of the Home shell whose agent has exited: the shell is back in
    the foreground (whatever the agent's process is called)."""
    out = []
    try:
        rows = _tmux(socket, "list-panes", "-a", "-F",
                     f"#{{window_name}}\t#{{pane_pid}}\t#{{{ADOPTED_OPT}}}\t#{{pane_start_command}}")
        for r in rows.stdout.splitlines():
            wname, pid, adopted, start = (r.split("\t", 3) + [""] * 4)[:4]
            tool, _, sid = adopted.partition(" ")
            if not (tool and HOME_SHELL_MARK in start and pid.isdigit()):
                continue
            res = subprocess.run(["ps", "-o", "tpgid=", "-p", pid], capture_output=True, text=True, timeout=3)
            if res.stdout.strip() == pid:
                out.append((wname, tool, sid))
    except (OSError, subprocess.SubprocessError):
        pass
    return out
