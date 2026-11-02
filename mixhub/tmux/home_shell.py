#!/usr/bin/env python3
"""`mixhub.tmux.home_shell`: The plain login shell under the reader on Home, parked in a hidden `hubpool` window
while a session is shown.
"""
from __future__ import annotations

import os
import subprocess

from mixhub.tui import paths as _paths

HOME_SHELL_MARK = "MIXHUB_HOME_SHELL=1"
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
        rows = tmux("list-panes", "-a", "-F", "#{pane_id}\t#{window_id}\t#{pane_height}\t#{pane_start_command}").stdout.splitlines()
        reader_win = next((r.split("\t")[1] for r in rows if r.split("\t")[0] == reader_pane), None)
        shell = next((r.split("\t") for r in rows if HOME_SHELL_MARK in r), None)
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
