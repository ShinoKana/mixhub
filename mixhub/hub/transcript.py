"""Transcript reading: JSONL row sampling (head + tail), per-tool entry extraction, and tail-state
classification (`ball_in_model` / `awaiting_user` / `done_idle`).
"""
from __future__ import annotations

import json
import os
import re

from mixhub.hub.constants import HEAD_BYTES, TAIL_BYTES
from mixhub.hub.schema import claude_is_synthetic_user, PROVIDER_SCHEMA_ADAPTERS
from mixhub.hub.text import clean_prompt, printable
from mixhub.hub.titles import agy_initial
from mixhub.i18n import t


def json_rows(path):
    with open(path, "rb") as f:
        for line in f:
            try:
                row = json.loads(line)
                if isinstance(row, dict):
                    yield row
            except (ValueError, UnicodeError):
                continue  # Partially written live records must not break the reader.


def _parse_json_lines(chunk: bytes) -> list[dict]:
    rows = []
    for line in chunk.splitlines():
        try:
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
        except (ValueError, UnicodeError):
            continue
    return rows


def sample_rows(path, size):
    """Read no more than 24 KiB + 64 KiB, with no partial/duplicate rows."""
    with open(path, "rb") as f:
        head = f.read(HEAD_BYTES)
        if size <= HEAD_BYTES:
            chunks = [head]
        elif size <= HEAD_BYTES + TAIL_BYTES:
            chunks = [head + f.read(TAIL_BYTES)]
        else:
            f.seek(-TAIL_BYTES, os.SEEK_END)
            tail = f.read(TAIL_BYTES)
            chunks = [head[:head.rfind(b"\n") + 1], tail[tail.find(b"\n") + 1:]]
    for chunk in chunks:
        yield from _parse_json_lines(chunk)


def entries(rows, tool, fallback=False):
    pending = []
    call_names = {}
    for row in rows:
        timestamp = row.get("timestamp") or row.get("created_at")
        synthetic_row = tool == "claude" and claude_is_synthetic_user(row)

        def item(role, text, kind="message", name="", call_id="", stop_reason=None):
            result = {"role": role, "kind": kind, "text": text or "", "timestamp": timestamp}
            if name:
                result["name"] = name
            if call_id:
                result["callId"] = call_id
            if stop_reason is not None:
                result["stopReason"] = stop_reason
            if row.get("truncated_fields"):
                result["sourceTruncated"] = row["truncated_fields"]
            if synthetic_row:
                # Still rendered in the transcript view, but never counts as human activity / prompt.
                result["synthetic"] = True
            return result

        if tool == "claude":
            if row.get("type") not in PROVIDER_SCHEMA_ADAPTERS["claude"]["turn_types"]:
                continue
            msg = row.get("message") or {}
            role = msg.get("role", row["type"])
            stop_reason = None
            if role == "assistant":
                stop_reason = msg.get("stop_reason")
                if stop_reason is None and "stop_reason" in msg:
                    stop_reason = "streaming"
            content = msg.get("content", [])
            if isinstance(content, str):
                content = [{"type": "text", "text": content}]
            texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
            text = "\n\n".join(texts)
            if role == "user":
                text = clean_prompt(text, tool)
            if text:
                yield item(role, text, stop_reason=stop_reason)
            for b in content:
                if not isinstance(b, dict):
                    continue
                kind = b.get("type")
                if kind == "thinking":
                    yield item("thinking", b.get("thinking", ""), "thinking")
                elif kind == "tool_use":
                    call_names[b.get("id")] = b.get("name", "tool")
                    yield item("tool", printable(b.get("input", {})), "tool_call", b.get("name", "tool"), b.get("id"))
                elif kind == "tool_result":
                    cid = b.get("tool_use_id")
                    yield item("tool", printable(b.get("content", "")), "tool_result", call_names.get(cid, t("transcript.tool_result")), cid)
        elif tool == "agy":
            kind = row.get("type")
            if kind == "USER_INPUT":
                text = clean_prompt(row.get("content"), tool)
                if text:
                    yield item("user", text)
            elif fallback and agy_initial(row):
                yield item("user", agy_initial(row))
            elif kind == "PLANNER_RESPONSE":
                if row.get("thinking"):
                    yield item("thinking", row["thinking"], "thinking")
                if row.get("content"):
                    yield item("assistant", row["content"])
                for n, call in enumerate(row.get("tool_calls") or []):
                    cid = call.get("id") or str(row.get("step_index")) + ":" + str(n)
                    name = call.get("name", "tool")
                    pending.append((name, cid))
                    yield item("tool", printable(call.get("args", {})), "tool_call", name, cid)
            elif kind == "GENERIC":
                name, cid = pending.pop(0) if pending else (t("transcript.tool_result"), "")
                yield item("tool", printable(row.get("content", "")), "tool_result", name, cid)
        elif tool == "codex" and row.get("type") == "response_item":
            msg = row.get("payload") or {}
            kind, role = msg.get("type"), msg.get("role")
            if kind == "message" and role in ("user", "assistant"):
                content = msg.get("content") or []
                if isinstance(content, str):
                    content = [{"text": content}]
                texts = [b.get("text", "") for b in content if isinstance(b, dict)]
                if role == "user":
                    texts = [clean_prompt(t, tool) for t in texts]
                text = "\n\n".join(t for t in texts if t)
                if text:
                    yield item(role, text)
            elif kind in ("custom_tool_call", "function_call"):
                cid = msg.get("call_id")
                call_names[cid] = msg.get("name", "tool")
                yield item("tool", printable(msg.get("input", msg.get("arguments", {}))), "tool_call", msg.get("name", "tool"), cid)
            elif kind in ("custom_tool_call_output", "function_call_output"):
                cid = msg.get("call_id")
                yield item("tool", printable(msg.get("output", "")), "tool_result", call_names.get(cid, t("transcript.tool_result")), cid)
            elif kind == "reasoning":
                text = "\n".join(x.get("text", "") for x in (msg.get("summary") or []) if isinstance(x, dict))
                if text:
                    yield item("thinking", text, "thinking")


WAIT_QUESTION_RE = re.compile(
    r"(要不要|是否需要|是否继续|请确认|你选|你希望|拍板|选哪个|请选择|确认休眠|"
    r"Should I\b|Would you like|Do you want|Shall I\b|Proceed\b|\[y/N\]|\[Y/n\])",
    re.I,
)
NO_DECISION_RE = re.compile(r"(拍板的点|需拍板的分歧|未决问题)[：:\s]*\n?\s*(无|None|N/A)\b", re.I)


def _is_structured_wait_entry(entry_list: list[dict]) -> bool:
    if not entry_list:
        return False
    last = entry_list[-1]
    if last.get("kind") == "tool_call":
        return (last.get("name") or "").strip() in ("AskUserQuestion", "ExitPlanMode", "ask_question")
    return False


def classify_tail_entries(entry_list: list[dict], tool: str = "") -> str:
    """
    Classify the tail of a session's parsed entries into:
      - 'wait_input'    : waiting on user answer or tool permission approval
      - 'unclosed_tool' : last record is a tool_call with no tool_result yet
      - 'ball_in_model' : last record is user prompt / tool_result / thinking / intermediate tool_use preamble
      - 'done_idle'     : assistant turn completed with no pending tool or blocking question
    Trailing synthetic user rows (a `/compact` summary, injected meta text) are nobody's prompt and are skipped.
    """
    while entry_list and entry_list[-1].get("role") == "user" and entry_list[-1].get("synthetic"):
        entry_list = entry_list[:-1]
    if not entry_list:
        return "done_idle"
    last = entry_list[-1]
    kind = last.get("kind", "message")
    role = last.get("role", "")
    txt = (last.get("text") or "").strip()

    if kind == "tool_call":
        tname = (last.get("name") or "").strip()
        if tname in ("AskUserQuestion", "ExitPlanMode", "ask_question"):
            return "wait_input"
        return "unclosed_tool"

    if kind in ("tool_result", "thinking"):
        return "ball_in_model"

    if role == "user":
        if "[Request interrupted by user]" in txt or "interrupted by user" in txt.lower():
            return "wait_input"
        return "ball_in_model"

    if role == "assistant" and last.get("stopReason") in ("tool_use", "streaming"):
        return "ball_in_model"

    # Assistant message turn ended: ordinary trailing '?'/'？' is a completed turn ('done_idle').
    tail_chunk = txt[-400:] if len(txt) > 400 else txt
    cleaned_tail = NO_DECISION_RE.sub("", tail_chunk).strip()
    non_empty_lines = [ln.strip() for ln in cleaned_tail.splitlines() if ln.strip()]
    last_two = "\n".join(non_empty_lines[-2:]) if non_empty_lines else ""
    if WAIT_QUESTION_RE.search(last_two):
        return "wait_input"
    return "done_idle"
