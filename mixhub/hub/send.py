"""Send text to a session: type it into the session's tmux pane, or resume a dormant session headless.

This is what the TUI and web composers (and the web approval bar) use, through `Hub.action(tool, id, "send", body)`
/ `POST /api/sessions/<tool>/<id>/send`.
"""
from __future__ import annotations

import subprocess

from mixhub.hub.shell import resume_cwd


def find_controlled_tmux_pane(session: dict, tmux_lister=None) -> str:
    """
    Locate a controlled tmux pane (`hubpool`, `hub`, or default server) matching a live process TTY
    of `session`. Never blindly targets an arbitrary foreground macOS window.
    """
    tty_set = {
        str(p.get("tty") or "").removeprefix("/dev/")
        for p in (session.get("processes") or [])
        if p.get("tty")
    }
    tty_set.discard("")
    if not tty_set:
        return ""
    if tmux_lister is not None:
        panes = tmux_lister()
        for pane_id, pane_tty in panes:
            if str(pane_tty).removeprefix("/dev/") in tty_set:
                return pane_id
        return ""
    for sock in ("hubpool", ""):
        cmd = ["tmux"]
        if sock:
            cmd.extend(["-L", sock])
        cmd.extend(["list-panes", "-a", "-F", "#{session_name}:#{window_index}.#{pane_index}\t#{pane_tty}"])
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=1.0)
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    parts = line.strip().split("\t")
                    if len(parts) == 2 and parts[1].removeprefix("/dev/") in tty_set:
                        return f"{sock}:{parts[0]}" if sock else parts[0]
        except Exception:
            pass
    return ""


def headless_resume_command(tool: str, session_id: str, prompt: str) -> list[str]:
    if tool == "claude":
        return ["claude", "--resume", session_id, "-p", prompt]
    if tool == "codex":
        return ["codex", "exec", "resume", session_id, prompt]
    return ["agy", "-p", prompt, "--conversation", session_id]


def send_to_session(
    session: dict,
    text: str,
    attachments: list[str] | None = None,
    *,
    home: str = "",
    allow_headless_exec: bool = True,
    dry_run: bool = False,
    tmux_runner=None,
    headless_runner=None,
    tmux_lister=None,
) -> dict:
    """
    Deliver `text` (plus local file paths in `attachments`, listed for the agent to read) to `session`:
      1. if the session runs in a controlled tmux pane, type it there (`delivery_mode="tmux_pane"`);
      2. otherwise resume it headless (`claude --resume <id> -p`, `codex exec resume`, `agy -p --conversation`;
         `delivery_mode="headless_resume"`). A dormant Claude session is only resumed with `allow_headless_exec`.
    """
    resolved_tool = session["tool"]
    resolved_id = session["id"]
    skey = f"{resolved_tool}:{resolved_id}"
    cwd = resume_cwd(session) if session.get("path") else (session.get("cwd") or home)

    clean_files = [str(p).strip() for p in (attachments or []) if str(p).strip()]
    prompt_parts = [str(text or "").strip()] if str(text or "").strip() else []
    if clean_files:
        prompt_parts.append("[Attached files for inspection: " + ", ".join(clean_files) + "]")
    formatted_prompt = "\n".join(prompt_parts).strip()

    pane_target = find_controlled_tmux_pane(session, tmux_lister=tmux_lister)
    if pane_target:
        sock = ""
        pane_spec = pane_target
        if ":" in pane_target and pane_target.split(":", 1)[0] == "hubpool":
            sock, pane_spec = pane_target.split(":", 1)
        base_tmux = ["tmux", "-L", sock] if sock else ["tmux"]
        cmd_literal = [*base_tmux, "send-keys", "-t", pane_spec, "-l", "--", formatted_prompt]
        cmd_enter = [*base_tmux, "send-keys", "-t", pane_spec, "Enter"]
        if not dry_run:
            runner = tmux_runner or (
                lambda argv: subprocess.run(argv, capture_output=True, text=True, timeout=3)
            )
            runner(cmd_literal)
            runner(cmd_enter)
        return {
            "ok": True,
            "delivery_mode": "tmux_pane",
            "session_key": skey,
            "pane_target": pane_target,
            "prompt": formatted_prompt,
            "attachments": clean_files,
            "commands": [cmd_literal, cmd_enter],
        }

    if not allow_headless_exec and resolved_tool == "claude":
        return {
            "ok": False,
            "tos_guard_blocked": True,
            "session_key": skey,
            "message": (
                "Session is dormant (not in a live tmux pane). Background `claude -p` polling is "
                "disabled by default; pass allow_headless_exec=True to allow it."
            ),
        }

    headless_cmd = headless_resume_command(resolved_tool, resolved_id, formatted_prompt)
    if not dry_run:
        if headless_runner is not None:
            headless_runner(headless_cmd, cwd)
        else:
            subprocess.Popen(
                headless_cmd,
                cwd=cwd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
    return {
        "ok": True,
        "delivery_mode": "headless_resume",
        "session_key": skey,
        "cwd": cwd,
        "prompt": formatted_prompt,
        "attachments": clean_files,
        "command": headless_cmd,
    }
