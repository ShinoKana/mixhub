"""Prompt text cleaning: strip CLI envelope / pasted-content tags, one-line snippets, printable control filtering."""
from __future__ import annotations

import json
import re


_USER_REQUEST_RE = re.compile(r"<USER_REQUEST>(.*?)</USER_REQUEST>", re.S | re.I)
_ENVELOPE_TAG_NAMES = (
    r"environment_context|INSTRUCTIONS|ADDITIONAL_METADATA|SYSTEM_MESSAGE|system-reminder|"
    r"ide_opened_file|ide_selection|command-name|command-message|command-args|"
    r"local-command-caveat|local-command-stdout|user_instructions|permissions instructions|"
    r"collaboration_mode|cross-session-message|teammate-message|task-notification|turn_aborted"
)
_ENVELOPE_TAGS_RE = re.compile(r"<(" + _ENVELOPE_TAG_NAMES + r")\b[^>]*>.*?</\1>", re.S | re.I)
_USER_TAG_STRIP_RE = re.compile(r"</?(?:USER_REQUEST|USER_INPUT)\b[^>]*>", re.I)
_PASTED_CLOSE_RE = re.compile(r"</pasted_content\s*>", re.I)
_PASTED_OUTER_RE = re.compile(r"<pasted_content\b[^>]*>.*?</pasted_content\s*>", re.S | re.I)
_PASTED_BOTH_TAGS_RE = re.compile(r"<pasted_content\b[^>]*>|</pasted_content\s*>", re.I)
_PASTED_OPEN_RE = re.compile(r"<pasted_content\b[^>]*>", re.I)
_SNIPPET_TOK_RE = re.compile(r"\S+")


def clean_prompt(value, tool=""):
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text:
        return ""
    if text.startswith("Another Claude session sent a message:"):
        return ""
    if tool == "codex" and (text.startswith("# AGENTS.md instructions") or text.startswith("<INSTRUCTIONS>")):
        return ""
    if "<" not in text:
        return text
    request = _USER_REQUEST_RE.search(text)
    if request:
        text = request.group(1)
    # Remove known transport/instruction envelopes, not arbitrary user HTML/code.
    text = _ENVELOPE_TAGS_RE.sub("", text)
    text = _USER_TAG_STRIP_RE.sub("", text)
    # Claude Code `<pasted_content>`: drop the paste when the user typed around it; keep it when it is the whole prompt.
    outer = _PASTED_OUTER_RE.sub("", text)
    if outer != text:
        text = outer if _PASTED_CLOSE_RE.sub("", outer).strip() else _PASTED_BOTH_TAGS_RE.sub("", text)
    # Unclosed (truncated `last-prompt` / `firstPrompt`): keep typed text before the tag, else the paste body.
    opened = _PASTED_OPEN_RE.search(text)
    if opened:
        before = text[:opened.start()]
        text = before if _PASTED_CLOSE_RE.sub("", before).strip() else text[opened.end():]
    text = _PASTED_CLOSE_RE.sub("", text)
    return text.strip()


def snippet(text, length=220):
    s = str(text or "")
    if not s or length <= 0:
        return ""
    if len(s) <= length * 4:
        return " ".join(s.split())[:length]
    parts = []
    acc = 0
    for m in _SNIPPET_TOK_RE.finditer(s):
        tok = m.group(0)
        parts.append(tok)
        acc += len(tok) + 1
        if acc >= length + 1:
            break
    return " ".join(parts)[:length]


def printable(value):
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
