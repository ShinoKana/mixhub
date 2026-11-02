"""OpenCode storage: read sessions and messages from its service SQLite DB (read-only)."""
from __future__ import annotations

from contextlib import closing
import json
import os
import sqlite3
import urllib.parse

from mixhub.hub.text import clean_prompt, printable


_OPENCODE_SESSIONS_SQL = """
SELECT s.id, s.directory, s.title, s.parent_id, s.model, s.agent, s.time_created, s.time_updated, s.time_idle,
  s.time_viewed,
  (SELECT substr(json_extract(m.data, '$.text'), 1, 4000) FROM session_message m
     WHERE m.session_id = s.id AND m.type = 'user' ORDER BY m.seq ASC LIMIT 1) AS first_user,
  (SELECT substr(json_extract(m.data, '$.text'), 1, 4000) FROM session_message m
     WHERE m.session_id = s.id AND m.type = 'user' ORDER BY m.seq DESC LIMIT 1) AS last_user,
  (SELECT m.type FROM session_message m WHERE m.session_id = s.id ORDER BY m.seq DESC LIMIT 1) AS last_type,
  (SELECT count(*) FROM session_message m WHERE m.session_id = s.id AND m.type IN ('user', 'assistant')) AS turns
FROM session_v2 s WHERE s.time_archived IS NULL
"""


def _opencode_connect(path):
    connection = sqlite3.connect("file:" + urllib.parse.quote(path) + "?mode=ro", uri=True, timeout=0.5)
    connection.row_factory = sqlite3.Row
    return connection


def opencode_session_rows(path):
    """All non-archived OpenCode sessions with first/last prompt and the last message kind; [] if unreadable."""
    if not os.path.isfile(path):
        return []
    try:
        with closing(_opencode_connect(path)) as connection:
            return [dict(r) for r in connection.execute(_OPENCODE_SESSIONS_SQL)]
    except sqlite3.Error:
        return []


def opencode_entries(path, sid):
    """Transcript items for one OpenCode session, in the shape `entries()` yields."""
    if not os.path.isfile(path):
        return
    try:
        with closing(_opencode_connect(path)) as connection:
            rows = connection.execute(
                "SELECT type, time_created, data FROM session_message WHERE session_id = ? "
                "AND type IN ('user', 'assistant') ORDER BY seq", (sid,)).fetchall()
    except sqlite3.Error:
        return
    for row in rows:
        try:
            data = json.loads(row["data"])
        except ValueError:
            continue
        ts = row["time_created"]

        def item(role, text, kind="message", name="", call_id=""):
            result = {"role": role, "kind": kind, "text": text or "", "timestamp": ts}
            if name:
                result["name"] = name
            if call_id:
                result["callId"] = call_id
            return result

        if row["type"] == "user":
            text = clean_prompt(data.get("text") or "", "opencode")
            if text:
                yield item("user", text)
            continue
        for block in data.get("content") or []:
            if not isinstance(block, dict):
                continue
            kind = block.get("type")
            if kind == "text" and block.get("text"):
                yield item("assistant", block["text"])
            elif kind == "reasoning" and block.get("text"):
                yield item("thinking", block["text"], "thinking")
            elif kind == "tool":
                name, cid = block.get("name") or "tool", block.get("id") or ""
                state = block.get("state") or {}
                yield item("tool", printable(state.get("input", {})), "tool_call", name, cid)
                output = state.get("content")
                if isinstance(output, list):
                    output = "\n".join(b.get("text", "") for b in output if isinstance(b, dict))
                if output:
                    yield item("tool", printable(output), "tool_result", name, cid)
