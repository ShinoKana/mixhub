#!/usr/bin/env python3
"""`mixhub.tmux.headers`: The 2-row `--header` pane on top of every `hubpool` window: height pinning hooks and
(re)creation.
"""
from __future__ import annotations

import os
import shlex
import subprocess

from mixhub.tmux import commands as _commands
from mixhub.tmux.commands import interactive_resume_cmd

# `--header` pane in each `hubpool` window is pinned to exactly this many rows (fullscreen / resize safe)
HUB_HDR_ROWS = 2
_HUB_HOOK_IDX = 70


def _hubpool_header_hook_cmds(hdr_rows: int = HUB_HDR_ROWS) -> list[list[str]]:
    """
    tmux hooks (index `_HUB_HOOK_IDX`) that pin every unzoomed 2-pane `hubpool` window's top `--header` pane to
    `hdr_rows` rows after client / window resizes (fullscreen toggles, split resizes) and window switches.
    Session windows only (`w-*`): the `reader` window's second pane is the Home shell, not a CLI under a header.
    """
    fix = (
        f"if-shell -F \"#{{&&:#{{m:w-*,#{{window_name}}}},#{{&&:#{{==:#{{window_panes}},2}},#{{==:#{{window_zoomed_flag}},0}}}}}}\" "
        f"\"resize-pane -t '{{top}}' -y {int(hdr_rows)}\""
    )
    return [
        ["tmux", "-L", "hubpool", "set-hook", "-g", f"client-resized[{_HUB_HOOK_IDX}]", fix],
        ["tmux", "-L", "hubpool", "set-hook", "-gw", f"window-resized[{_HUB_HOOK_IDX}]", fix],
        ["tmux", "-L", "hubpool", "set-hook", "-g", f"after-split-window[{_HUB_HOOK_IDX}]", fix],
        ["tmux", "-L", "hubpool", "set-hook", "-g", f"session-window-changed[{_HUB_HOOK_IDX}]", fix],
    ]


def _enforce_hubpool_header_height(win_target: str | None = None, hdr_rows: int = HUB_HDR_ROWS) -> None:
    """Resize the `--header` pane (`<win_target>.{top}`, or this process's own `$TMUX_PANE`) to `hdr_rows`."""
    if win_target:
        tgt = f"{win_target}.{{top}}"
    else:
        tgt = os.environ.get("TMUX_PANE", "")
        if not tgt:
            return
    env = dict(os.environ)
    env.pop("TMUX", None)
    try:
        subprocess.run(
            ["tmux", "-L", "hubpool", "resize-pane", "-t", tgt, "-y", str(int(hdr_rows))],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            timeout=3,
        )
    except Exception:
        pass


def _install_hubpool_header_hooks(env: dict | None = None, hdr_rows: int = HUB_HDR_ROWS) -> None:
    run_env = env
    if run_env is None:
        run_env = dict(os.environ)
        run_env.pop("TMUX", None)
    for cmd in _hubpool_header_hook_cmds(hdr_rows):
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=run_env, timeout=3)
        except Exception:
            pass


def ensure_hubpool_window_header(wname: str, tool: str, sid: str, env: dict | None = None) -> None:
    """
    Attach a 2-row MixHub `--header <tool> <id>` top pane above a native CLI window `pool:{wname}`
    so Row 0 (`◧ ~ · id`), Row 1 (`◇ Title ... ↩ 预览  ⇄ Relay`), and Row 2 (`────` tmux pane border)
    stay visible even when interacting with a native CLI in the right pane!
    """
    if not wname or wname == "reader":
        return
    run_env = env
    if run_env is None:
        run_env = dict(os.environ)
        run_env.pop("TMUX", None)
    try:
        res = subprocess.run(
            [
                "tmux", "-L", "hubpool", "list-panes", "-t", f"pool:{wname}",
                "-F", "#{pane_id}|#{pane_top}|#{pane_height}|#{pane_start_command}|#{pane_current_path}",
            ],
            capture_output=True,
            text=True,
            env=run_env,
        )
        raw_out = res.stdout if isinstance(getattr(res, "stdout", None), str) else ""
        lines = [ln for ln in raw_out.strip().splitlines() if ln.startswith("%") and "|" in ln]
        if len(lines) == 1:
            parts = lines[0].split("|", 4)
            pane_id = parts[0]
            start_cmd = parts[3] if len(parts) >= 4 else ""
            cur_path = (parts[4] if len(parts) >= 5 else "") or os.path.expanduser("~")
            if not os.path.isdir(cur_path):
                cur_path = os.path.expanduser("~")
            if "--header" in start_cmd:
                # Bottom CLI exited leaving only the top `--header` pane: respawn the bottom CLI below `--header`
                # and resize `--header` back to 2 rows instead of splitting a duplicate `--header` pane!
                cmd_argv = interactive_resume_cmd({
                    "tool": tool,
                    "id": sid,
                    "cwd": cur_path,
                    "synthetic": str(sid).startswith("new-"),
                })
                cli_cmd = _commands._build_hubpool_cli_wrapper_cmd(
                    tool, sid, wname, cur_path, " ".join(shlex.quote(x) for x in cmd_argv)
                )
                subprocess.run(
                    ["tmux", "-L", "hubpool", "split-window", "-d", "-t", pane_id, "-v", "-c", cur_path, cli_cmd],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=run_env,
                )
                subprocess.run(
                    ["tmux", "-L", "hubpool", "resize-pane", "-t", pane_id, "-y", "2"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=run_env,
                )
            else:
                hdr_cmd = _commands._header_cmd(tool, sid)
                subprocess.run(
                    ["tmux", "-L", "hubpool", "split-window", "-d", "-t", pane_id, "-vb", "-l", "2", "-c", cur_path, hdr_cmd],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=run_env,
                )
        elif len(lines) >= 2:
            # Existing 2-pane window (e.g. after fullscreen / resize): pin `--header` back to HUB_HDR_ROWS rows
            _enforce_hubpool_header_height(f"pool:{wname}")
        subprocess.run(
            ["tmux", "-L", "hubpool", "select-pane", "-t", f"pool:{wname}.{{bottom}}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=run_env,
        )
    except Exception:
        pass
