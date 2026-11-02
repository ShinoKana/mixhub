"""Claude Code / Codex hook configuration that sends agent events through `mixhub notify --hook`, and the parsing
of what those hooks hand over."""
from __future__ import annotations

import json
import os
import shlex

from mixhub.core.launch import mixhub_executable

# How much of a reply goes into a notification.
REPLY_EXCERPT_CHARS = 600


def generate_official_hook_config(hub_root: str | None = None) -> dict:
    """
    Hook configuration for the agents' own settings files:
      - Claude Code (`~/.claude/settings.json`): `Notification` (it needs you) and `Stop` (its turn finished)
      - Codex (`~/.codex/config.toml`): `notify`, which runs after each turn with the event JSON as last argument
    """
    hub_bin = mixhub_executable(hub_root)
    claude_cmd = f"{shlex.quote(hub_bin)} notify --hook claude"

    def claude_hook() -> list[dict]:
        return [{"matcher": "", "hooks": [{"type": "command", "command": claude_cmd}]}]

    return {
        "claude_settings_json": {"hooks": {"Notification": claude_hook(), "Stop": claude_hook()}},
        "codex_config_toml": "notify = " + json.dumps([hub_bin, "notify", "--hook", "codex"]),
    }


def _last_assistant_text(transcript_path: str) -> str:
    """The text of the last assistant message in a Claude Code transcript (JSONL)."""
    last = ""
    try:
        with open(os.path.expanduser(transcript_path), encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(row, dict) or row.get("type") != "assistant":
                    continue
                content = (row.get("message") or {}).get("content")
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    text = "\n".join(str(c.get("text") or "") for c in content
                                     if isinstance(c, dict) and c.get("type") == "text")
                else:
                    text = ""
                if text.strip():
                    last = text.strip()
    except OSError:
        pass
    return last


def _excerpt(text: str) -> str:
    text = str(text or "").strip()
    return text if len(text) <= REPLY_EXCERPT_CHARS else text[: REPLY_EXCERPT_CHARS - 1].rstrip() + "…"


def parse_hook_event(agent: str, raw: str) -> tuple[str, dict] | None:
    """`(message, session)` for a hook payload, or None when the event is not worth a notification."""
    try:
        data = json.loads(raw or "{}")
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    if agent == "claude":
        cwd = str(data.get("cwd") or "")
        session = {"tool": "claude", "id": str(data.get("session_id") or ""), "cwd": cwd,
                   "title": os.path.basename(cwd.rstrip("/")) or "Claude Code"}
        event = data.get("hook_event_name")
        if event == "Notification":
            return _excerpt(data.get("message") or "Claude Code needs your input"), session
        if event == "Stop":
            reply = _last_assistant_text(str(data.get("transcript_path") or ""))
            return "Finished: " + (_excerpt(reply) if reply else "turn complete"), session
        return None
    if agent == "codex":
        if data.get("type") != "agent-turn-complete":
            return None
        cwd = str(data.get("cwd") or "")
        session = {"tool": "codex", "id": str(data.get("thread-id") or ""), "cwd": cwd,
                   "title": os.path.basename(cwd.rstrip("/")) or "Codex"}
        reply = str(data.get("last-assistant-message") or "")
        return "Finished: " + (_excerpt(reply) if reply else "turn complete"), session
    return None
