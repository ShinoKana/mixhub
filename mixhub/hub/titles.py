"""Session titles: Claude title records and the reverse tail scan, agy first-prompt sender, fallback and
placeholder title detection.
"""
from __future__ import annotations

import json
import re

from mixhub.hub.constants import UUID
from mixhub.hub.text import clean_prompt, snippet


# Claude title metadata records: type -> (row field, summary key). Claude Code applies each "last-wins".
CLAUDE_TITLE_RECORDS = {"custom-title": ("customTitle", "customTitle"), "ai-title": ("aiTitle", "aiTitle"),
                        "agent-name": ("agentName", "claudeAgentName"), "summary": ("summary", "nativeSummary")}
CLAUDE_TITLE_KEYS = ("customTitle", "aiTitle", "claudeAgentName", "nativeSummary")
CLAUDE_TITLE_MARKERS = (b'"custom-title"', b'"ai-title"', b'"agent-name"', b'"customTitle"', b'"aiTitle"', b'"agentName"')
TITLE_SCAN_CHUNK_BYTES, TITLE_SCAN_OVERLAP_BYTES = 256 * 1024, 512
TITLE_SCAN_MAX_BYTES, TITLE_SCAN_CONTEXT_BYTES = 8 * 1024 * 1024, 64 * 1024


def title_fields(rows, tool) -> dict:
    """Latest title metadata per kind, applied in file order."""
    found = {}
    for row in rows:
        for source in ("customTitle", "aiTitle"):
            if row.get(source):
                found[source] = snippet(clean_prompt(row[source], tool), 220)
        # Never `agentName`: for agy that key flags subagents (is_temp_session / isSubagent).
        if tool == "claude" and row.get("type") == "agent-name" and row.get("agentName"):
            found["claudeAgentName"] = snippet(clean_prompt(row["agentName"], tool), 220)
        if row.get("type") == "summary" and row.get("summary"):
            found["nativeSummary"] = snippet(clean_prompt(row["summary"], tool), 220)
    return found


def claude_title_chunk_fields(chunk: bytes, line_aligned=True) -> dict:
    """Title fields from a raw transcript byte range; only lines carrying a title marker are JSON-parsed."""
    if not line_aligned:
        chunk = chunk[chunk.find(b"\n") + 1:]
    rows = []
    for line in chunk.splitlines():
        if not any(m in line for m in CLAUDE_TITLE_MARKERS) and b'"summary"' not in line:
            continue
        try:
            row = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if isinstance(row, dict) and row.get("type") in CLAUDE_TITLE_RECORDS:
            rows.append(row)
    return title_fields(rows, "claude")


def claude_reverse_title_scan(f, lower, upper) -> dict:
    """Cold start only: walk backward from `upper` to `lower` until a chunk holds Claude title metadata."""
    end, scanned = upper, 0
    while end > lower and scanned < TITLE_SCAN_MAX_BYTES:
        start = max(lower, end - TITLE_SCAN_CHUNK_BYTES)
        f.seek(start)
        chunk = f.read(min(upper, end + TITLE_SCAN_OVERLAP_BYTES) - start)
        scanned += end - start
        if any(m in chunk for m in CLAUDE_TITLE_MARKERS):
            # Include the preceding 64 KiB so a whole metadata re-append block is captured.
            ctx_start = max(lower, start - TITLE_SCAN_CONTEXT_BYTES)
            f.seek(ctx_start)
            found = claude_title_chunk_fields(f.read(min(upper, end + TITLE_SCAN_CONTEXT_BYTES) - ctx_start),
                                              line_aligned=ctx_start == lower)
            if any(found.get(k) for k in ("customTitle", "aiTitle", "claudeAgentName")):
                return found
        end = start
    return {}


def agy_initial(row):
    if row.get("type") == "SYSTEM_MESSAGE" and row.get("step_index") == 0:
        match = re.search(r"\[Message\].*?\bcontent=(.*)", row.get("content") or "", re.S)
        if match:
            return clean_prompt(match.group(1), "agy")
    return ""


def agy_initial_sender(row) -> str:
    if row.get("type") == "SYSTEM_MESSAGE" and row.get("step_index") == 0:
        match = re.search(r"\[Message\].*?\bsender=(" + UUID + r")\b", row.get("content") or "", re.S)
        if match:
            return match.group(1)
    return ""


FALLBACK_TITLE_RE = re.compile(r"^(?:claude|codex|agy|opencode)\s*·\s*[0-9a-fA-F]{8}$")

_GENERIC_PLACEHOLDER_TITLES = {
    "casual greeting",
    "greeting",
    "new chat",
    "new session",
    "new conversation",
    "untitled",
    "hi",
    "hello",
    "hey",
    "你好",
    "您好",
    "在吗",
    "test",
    "testing",
}


_PLACEHOLDER_REPEAT_RE = re.compile(r"(?:h+i+|h+e+l+o+|h+e+y+|y+o+|o+k+|t+e+s+t+)")


def _is_generic_placeholder_title(title: str) -> bool:
    if not title:
        return True
    s = " ".join(str(title).strip().split()).strip(".,!?:;~，。！？：；～")
    if len(s) <= 2:
        return True
    if len(s) > 20 and s[0] not in "hHyYoOtT":
        return False
    low = s.casefold()
    if low in _GENERIC_PLACEHOLDER_TITLES:
        return True
    if _PLACEHOLDER_REPEAT_RE.fullmatch(low):
        return True
    return False
