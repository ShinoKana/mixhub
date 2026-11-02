#!/usr/bin/env python3
"""`mixhub.tmux.commands`: Command lines for `hubpool` windows: interactive resume argv, window names, the
`--header` respawn command, and the `--on-exit` CLI wrapper.
"""
from __future__ import annotations

import os
import shlex

from mixhub.core.launch import AGY_FILTER_MODULE, TUI_MODULE, module_command, module_shell


def interactive_resume_cmd(session: dict) -> list[str]:
    """Return argv to resume a session interactively (never launching a bare nested picker)."""
    tool, sid = session["tool"], session["id"]
    cwd = session.get("cwd") or os.path.expanduser("~")
    if not os.path.isdir(cwd):
        cwd = os.path.expanduser("~")
    if session.get("synthetic"):
        return module_command(AGY_FILTER_MODULE) if tool == "agy" else [tool]
    if tool == "claude":
        return ["claude", "--resume", sid]
    if tool == "agy":
        return module_command(AGY_FILTER_MODULE, "--conversation", sid)
    if tool == "opencode":
        return ["opencode", "-s", sid, cwd]
    return ["codex", "--no-daemon", "-C", cwd, "resume", sid]


def hubpool_win_name(session: dict) -> str:
    if session.get("hubpoolWindow"):
        return str(session["hubpoolWindow"])
    sid = str(session.get("id") or "")
    if sid.startswith("new-"):
        return f"w-{session['tool']}-{sid}"
    return f"w-{session['tool']}-{sid[:12]}"


def _header_cmd(tool: str, sid: str) -> str:
    """Pane command of a `hubpool` window's 2-row header: `exec [env PYTHONPATH=..] python -P -m mixhub.tui --header <tool> <sid>`."""
    return module_shell(TUI_MODULE, "--header", str(tool), str(sid))


def _reader_cmd() -> str:
    """Pane command of the `hubpool` `reader` window."""
    return module_shell(TUI_MODULE, "--reader")


def _sidebar_cmd(extra_env: dict[str, str] | None = None) -> str:
    """Pane command of the outer workspace sidebar (`extra_env`: `HUB_OUTER_SESSION` / `HUB_POOL_SESSION`)."""
    return module_shell(TUI_MODULE, "--sidebar", extra_env=extra_env)


_DEEPSEEK_ENV_SOURCE = '[ -f "$HOME/.config/deepseek/env" ] && . "$HOME/.config/deepseek/env"; '


def _deepseek_window_env_args(env: dict) -> list[str]:
    """
    Export a resolvable `DEEPSEEK_API_KEY` for a `hubpool` CC·DS launch: always into the tmux client `env`, and as
    `new-window -e` only when `~/.config/deepseek/env` (sourced by the launch command itself) can't supply it,
    so the key stays out of argv whenever possible.
    """
    from mixhub.providers.deepseek import load_deepseek_api_key

    key = load_deepseek_api_key()
    if not key:
        return []
    env["DEEPSEEK_API_KEY"] = key
    if os.path.isfile(os.path.expanduser("~/.config/deepseek/env")):
        return []
    return ["-e", f"DEEPSEEK_API_KEY={key}"]


def _build_hubpool_cli_wrapper_cmd(tool: str, sid: str, wname: str, cwd: str, inner_cmd: str) -> str:
    """
    Wrap a native CLI launch for a `hubpool` window so that when the CLI exits (`/exit`, `Ctrl-D`, crash)
    the pane runs `python -m mixhub.tui --on-exit <tool> <sid> <wname> "$rc"` instead of leaving a dead pane.
    `trap : INT QUIT` keeps the wrapper shell alive on `Ctrl-C` while the CLI itself still receives it.
    `trap 'sig_rc=129' HUP; trap 'sig_rc=143' TERM` ensures that even if the child CLI catches `SIGHUP`/`SIGTERM`
    during macOS shutdown or `tmux` teardown and exits 0, the wrapper still reports signal exit (`>= 128`)
    to `--on-exit` so the session is preserved in `Active` (`active_working_set.json`).
    """
    return (
        f"cd {shlex.quote(cwd)} && sig_rc=0; trap 'sig_rc=129' HUP; trap 'sig_rc=143' TERM; trap : INT QUIT; "
        f"{inner_cmd}; rc=$?; [ \"$sig_rc\" -ne 0 ] && rc=$sig_rc; "
        f"{module_shell(TUI_MODULE, '--on-exit', str(tool), str(sid), str(wname))} \"$rc\""
    )
