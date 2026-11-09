"""Per-session summaries and transcript reads: head / tail sampling, title scans, subagent links."""
from __future__ import annotations

import json
import os
import sqlite3
import time
import urllib.parse

from mixhub.hub.constants import (
    _SUBAGENT_CREATED_RE,
    _SUBAGENT_INVOKE_RE,
    HEAD_BYTES,
    TAIL_BYTES,
    TAIL_SCAN_MAX_BYTES,
    TAIL_SCAN_STEP_BYTES,
)
from mixhub.hub.opencode import opencode_entries
from mixhub.hub.schema import claude_is_headless_marker, claude_is_synthetic_user, PROVIDER_SCHEMA_ADAPTERS
from mixhub.hub.session_filters import is_automated_or_subagent_text, is_codex_automated
from mixhub.hub.text import clean_prompt, snippet
from mixhub.hub.titles import (
    _is_generic_placeholder_title,
    agy_initial,
    agy_initial_sender,
    claude_reverse_title_scan,
    claude_title_chunk_fields,
    CLAUDE_TITLE_KEYS,
    title_fields,
    TITLE_SCAN_MAX_BYTES,
)
from mixhub.hub.transcript import (
    _is_structured_wait_entry,
    _parse_json_lines,
    classify_tail_entries,
    entries,
    json_rows,
    sample_rows,
)
from mixhub.hub.util import _db_file_sig, read_json, stamp


class TranscriptMixin:
    """Session summary and transcript windows."""

    def rows(self, path, table, wanted):
        if not os.path.exists(path):
            return
        cache_key = (path, table)
        cur_sig = _db_file_sig(path)
        sig_key = (cur_sig, tuple(wanted)) if cur_sig is not None else None
        if sig_key is not None and self._db_sig_cache.get(cache_key) == sig_key and cache_key in self._db_rows_cache:
            yield from self._db_rows_cache[cache_key]
            return
        last_exc = None
        for attempt in range(3):
            connection = None
            try:
                connection = sqlite3.connect("file:" + urllib.parse.quote(path) + "?mode=ro", uri=True, timeout=0.5)
                connection.execute("PRAGMA cache_size=-256")
                columns = {r[1] for r in connection.execute("PRAGMA table_info(" + table + ")")}
                selected = [c for c in wanted if c in columns]
                if not selected:
                    # Table missing / renamed upstream: yield nothing instead of a SQL error.
                    self._db_rows_cache[cache_key] = []
                    if sig_key is not None:
                        self._db_sig_cache[cache_key] = sig_key
                    self._db_fail_counts[cache_key] = 0
                    return
                _TRUNC_COLS = {"first_user_message", "preview", "title"}
                select_exprs = [
                    (f'CASE WHEN typeof("{c}") = \'text\' THEN SUBSTR("{c}", 1, 512) ELSE "{c}" END AS "{c}"' if c in _TRUNC_COLS else f'"{c}"')
                    for c in selected
                ]
                fetched = [
                    {col: (val[:512] if col in _TRUNC_COLS and isinstance(val, str) else val)
                     for col, val in zip(selected, row)}
                    for row in connection.execute("SELECT " + ",".join(select_exprs) + " FROM " + table)
                ]
                self._db_rows_cache[cache_key] = fetched
                if sig_key is not None:
                    self._db_sig_cache[cache_key] = sig_key
                self._db_fail_counts[cache_key] = 0
                for item in fetched:
                    yield dict(item)
                return
            except sqlite3.Error as exc:
                last_exc = exc
                if attempt < 2:
                    time.sleep(0.03)
            finally:
                if connection is not None:
                    try:
                        connection.close()
                    except Exception:
                        pass
        fail_n = self._db_fail_counts.get(cache_key, 0) + 1
        self._db_fail_counts[cache_key] = fail_n
        cached_rows = self._db_rows_cache.get(cache_key)
        if cached_rows is not None:
            if fail_n >= 3 and last_exc is not None and "unable to open database file" not in str(last_exc).lower():
                self.warnings.append(table + ": " + str(last_exc))
            for item in cached_rows:
                yield dict(item)
            return
        if last_exc is not None and ("unable to open database file" not in str(last_exc).lower() or fail_n >= 3):
            self.warnings.append(table + ": " + str(last_exc))

    def _extract_invoked_subagent_parents(self, transcript_paths: list[str]) -> dict[str, str]:
        parent_map: dict[str, str] = {}
        for path in transcript_paths:
            parent_sid = path.split(os.sep)[-4]
            try:
                st = os.stat(path)
            except OSError:
                continue
            sig = (st.st_mtime_ns, st.st_size)
            cached = self._subagent_links_cache.get(path)
            if cached is not None and cached[0] == sig:
                child_ids = cached[1]
            else:
                prev_size = cached[0][1] if (cached is not None and isinstance(cached[0], tuple) and len(cached[0]) == 2) else 0
                child_ids = list(cached[1]) if (cached is not None and st.st_size >= prev_size > 0) else []
                seek_pos = max(0, prev_size - 4096) if (cached is not None and st.st_size >= prev_size > 0) else 0
                try:
                    with open(path, "rb") as f:
                        if seek_pos > 0:
                            f.seek(seek_pos)
                        raw = f.read()
                    if b"invoke_subagent" in raw or b"Created the following subagent" in raw:
                        for m in _SUBAGENT_CREATED_RE.finditer(raw):
                            cid = m.group(1).decode("ascii", "ignore")
                            if cid and cid != parent_sid and cid not in child_ids:
                                child_ids.append(cid)
                        for m in _SUBAGENT_INVOKE_RE.finditer(raw):
                            cid = m.group(1).decode("ascii", "ignore")
                            if cid and cid != parent_sid and cid not in child_ids:
                                child_ids.append(cid)
                except OSError:
                    pass
                self._subagent_links_cache[path] = (sig, child_ids)
                self.cache_dirty = True
            for cid in child_ids:
                parent_map[cid] = parent_sid
        return parent_map

    def _claude_project_index_entry(self, project_dir: str, sid: str) -> dict:
        """`<project_dir>/sessions-index.json` entry for `sid` (cached by mtime). Enrichment only: never creates sessions."""
        index_path = os.path.join(project_dir, "sessions-index.json")
        try:
            mtime_ns = os.stat(index_path).st_mtime_ns
        except OSError:
            self._claude_index_cache.pop(index_path, None)
            return {}
        cached = self._claude_index_cache.get(index_path)
        if cached is None or cached[0] != mtime_ns:
            data = read_json(index_path, {})
            items = data.get("entries") if isinstance(data, dict) else data
            by_sid = {e["sessionId"]: e for e in (items if isinstance(items, list) else [])
                      if isinstance(e, dict) and isinstance(e.get("sessionId"), str)}
            cached = self._claude_index_cache[index_path] = (mtime_ns, by_sid)
        return cached[1].get(sid) or {}

    def summary(self, path, tool):
        stat = os.stat(path)
        signature = (stat.st_mtime_ns, stat.st_size)
        prev_cached = self.cache.get(path)
        if prev_cached and prev_cached[0] == signature:
            return dict(prev_cached[1])
        size = stat.st_size
        # Title metadata carried forward across appends to the same inode, so a title that has
        # scrolled out of head+tail survives without rescanning the whole transcript.
        prev_end, prev_titles = 0, {}
        prev_res = prev_cached[1] if prev_cached else None
        if tool == "claude" and isinstance(prev_res, dict) and prev_res.get("_titleScanIno") == stat.st_ino:
            prev_end = int(prev_res.get("_titleScanEnd") or 0)
            prev_titles = {k: prev_res[k] for k in CLAUDE_TITLE_KEYS if prev_res.get(k)}
        result = {"messageCount": 0, "messageCountExact": size <= HEAD_BYTES + TAIL_BYTES,
                  "updatedAt": stat.st_mtime, "firstPrompt": "", "lastPrompt": ""}
        head_rows = []
        if size <= HEAD_BYTES + TAIL_BYTES:
            rows = list(sample_rows(path, size))
            tail_rows = rows
            titles = tail_titles = title_fields(rows, tool)
        else:
            with open(path, "rb") as f:
                head = f.read(HEAD_BYTES)
                head_end = head.rfind(b"\n") + 1
                head_rows = _parse_json_lines(head[:head_end])
                f.seek(-TAIL_BYTES, os.SEEK_END)
                tail_buf = f.read(TAIL_BYTES)
                cur_start = max(0, size - TAIL_BYTES)
                tail_rows = _parse_json_lines(tail_buf[tail_buf.find(b"\n") + 1:])
                # A compact summary alone (synthetic) does not count: its age would fall back to the head
                while (
                    not any(not e.get("synthetic") for e in entries(tail_rows, tool, False))
                    and cur_start > HEAD_BYTES
                    and (size - cur_start) < TAIL_SCAN_MAX_BYTES
                ):
                    new_start = max(0, cur_start - TAIL_SCAN_STEP_BYTES)
                    f.seek(new_start, os.SEEK_SET)
                    prev_chunk = f.read(cur_start - new_start)
                    tail_buf = prev_chunk + tail_buf
                    cur_start = new_start
                    usable = tail_buf[tail_buf.find(b"\n") + 1:] if cur_start > 0 else tail_buf
                    tail_rows = _parse_json_lines(usable)
                tail_row_start = cur_start + (tail_buf.find(b"\n") + 1 if cur_start > 0 else 0)
                titles, tail_titles = title_fields(head_rows, tool), title_fields(tail_rows, tool)
                if tool == "claude":
                    incremental = 0 < prev_end <= size and size - prev_end <= TITLE_SCAN_MAX_BYTES
                    if incremental:
                        # File order: head -> carried titles (covering [0, prev_end)) -> new gap -> tail.
                        if prev_end >= head_end:
                            titles.update(prev_titles)
                        gap_start = max(head_end, prev_end - 4096)
                        if tail_row_start > gap_start:
                            f.seek(gap_start)
                            titles.update(claude_title_chunk_fields(f.read(tail_row_start - gap_start),
                                                                    line_aligned=gap_start == head_end))
                        titles.update(tail_titles)
                    else:
                        titles.update(tail_titles)
                        if not any(titles.get(k) for k in ("customTitle", "aiTitle", "claudeAgentName")):
                            for k, v in claude_reverse_title_scan(f, head_end, tail_row_start).items():
                                if k not in tail_titles:
                                    titles[k] = v
                else:
                    titles.update(tail_titles)
            rows = head_rows + tail_rows
        result.update(titles)
        if tail_titles.get("customTitle"):
            result["customTitleFromTail"] = tail_titles["customTitle"]
        if tool == "claude":
            result["_titleScanIno"] = stat.st_ino
            result["_titleScanEnd"] = size
        row_ts = 0.0
        ledger_ts = 0.0
        own_sid = path.split(os.sep)[-4] if tool == "agy" and len(path.split(os.sep)) >= 4 else ""
        has_sdk_cli = False
        has_human_turn = False
        for row in rows:
            ts_val = stamp(row.get("timestamp") or row.get("created_at"))
            if ts_val > row_ts:
                row_ts = ts_val
            elif row.get("type") == "artifact-autoreact-ledger" and isinstance(row.get("artifacts"), dict):
                for av in row["artifacts"].values():
                    if isinstance(av, dict):
                        hw = stamp(av.get("stampHighWater"))
                        if hw > ledger_ts:
                            ledger_ts = hw
            if tool == "agy":
                sender_id = agy_initial_sender(row)
                if sender_id and sender_id != own_sid:
                    result["parentId"] = sender_id
                    result["isSubagent"] = True
                    result["automated"] = True
            if row.get("cwd"):
                result["cwd"] = row["cwd"]
            if tool == "claude":
                if claude_is_headless_marker(row.get("entrypoint")):
                    has_sdk_cli = True
                elif row.get("type") == "user" and not row.get("isSidechain") and not claude_is_synthetic_user(row):
                    # Fail open: any non-headless entrypoint (cli / claude-desktop / future vscode, web,
                    # remote ...) or a missing entrypoint counts as a human turn.
                    has_human_turn = True
            if row.get("type") == "session_meta":
                meta = row.get("payload") or {}
                result.update({k: meta[k] for k in ("id", "cwd") if meta.get(k)})
                if is_codex_automated(meta.get("source"), originator=meta.get("originator")):
                    result["automated"] = True
            if row.get("lastPrompt"):
                result["lastPrompt"] = snippet(clean_prompt(row["lastPrompt"], tool), 220)
        if has_sdk_cli and not has_human_turn:
            # Headless `claude -p` / SDK run: no human user turn anywhere in the sampled rows.
            result["automated"] = True
            result["isTemp"] = True
        fallback = tool == "agy" and not any(r.get("type") == "USER_INPUT" for r in rows)
        if fallback and any(agy_initial(r) for r in rows):
            result["isSubagent"] = True
            result["automated"] = True
        parsed_entries = list(entries(rows, tool, fallback))
        tail_entries = parsed_entries if size <= HEAD_BYTES + TAIL_BYTES else list(entries(tail_rows, tool, fallback))
        conv_ts = max((stamp(e.get("timestamp")) for e in parsed_entries if not e.get("synthetic")), default=0.0)
        user_ts = max((stamp(e.get("timestamp")) for e in parsed_entries if e.get("role") == "user" and not e.get("synthetic")), default=0.0)
        asst_ts = max((stamp(e.get("timestamp")) for e in parsed_entries if e.get("role") == "assistant" and not e.get("synthetic")), default=0.0)
        has_asst = any(e.get("role") == "assistant" and not e.get("synthetic") and (e.get("text") or "").strip() for e in parsed_entries)
        if conv_ts > 0:
            # Positive whitelist: only real user/assistant/tool entries define activity; metadata rows
            # (mode / permission-mode / cost-state ...) and file mtime never inflate updatedAt.
            result["updatedAt"] = conv_ts
            result["convAt"] = conv_ts
            result["hasRowTimestamp"] = True
        else:
            true_ts = row_ts or ledger_ts
            if true_ts > 0:
                result["updatedAt"] = max(float(stat.st_mtime), true_ts) if tool == "codex" else true_ts
                result["hasRowTimestamp"] = True
        if user_ts > 0:
            result["lastUserAt"] = user_ts
        substantive_prompt = ""
        for entry in parsed_entries:
            result["messageCount"] += 1
            if entry["role"] == "user" and not entry.get("synthetic"):
                prompt = snippet(entry["text"])
                if tool == "codex" and not result["firstPrompt"] and is_codex_automated(None, "", entry["text"]):
                    result["automated"] = True
                result["firstPrompt"] = result["firstPrompt"] or prompt
                if not substantive_prompt and not _is_generic_placeholder_title(prompt) and len(prompt.strip()) >= 4:
                    substantive_prompt = prompt
                result["lastPrompt"] = prompt
                if is_automated_or_subagent_text(entry["text"]):
                    result["consultant"] = True
                    result["automated"] = True
        if substantive_prompt:
            result["substantivePrompt"] = substantive_prompt
            if _is_generic_placeholder_title(result.get("firstPrompt", "")):
                result["firstPrompt"] = substantive_prompt
        # Never classify head entries as tail when the tail window has no conversation entries.
        result["tailStatus"] = classify_tail_entries(tail_entries, tool)
        result["tailStructuredWait"] = _is_structured_wait_entry(tail_entries)
        result["hasAssistantReply"] = bool(has_asst or result["tailStructuredWait"])
        # A user prompt advances recency, but only assistant output or a structured wait needs attention.
        if asst_ts > 0:
            result["attentionAt"] = asst_ts
        elif result["hasAssistantReply"] and conv_ts > 0:
            result["attentionAt"] = conv_ts
        else:
            result["attentionAt"] = 0.0
        if result["tailStatus"] == "wait_input" and not result["tailStructuredWait"]:
            result["asked"] = True
        # last-prompt is written after the actual user event, including long sessions.
        for row in rows:
            if row.get("type") == "last-prompt" and row.get("lastPrompt"):
                lp = snippet(clean_prompt(row["lastPrompt"], tool))
                result["lastPrompt"] = lp
                if _is_generic_placeholder_title(result.get("firstPrompt", "")) and not _is_generic_placeholder_title(lp) and len(lp.strip()) >= 4:
                    result["firstPrompt"] = lp
                    result["substantivePrompt"] = lp
                if is_automated_or_subagent_text(result["lastPrompt"]):
                    result["consultant"] = True
                    result["automated"] = True
        self.cache[path] = (signature, result)
        self.cache_dirty = True
        return dict(result)

    def transcript(self, session):
        if session.get("tool") == "opencode":
            yield from opencode_entries(self.path(PROVIDER_SCHEMA_ADAPTERS["opencode"]["db"]), session["id"])
            return
        path = session.get("path")
        if not path or not os.path.isfile(path):
            return
        if session["tool"] == "agy":
            with open(path, "rb") as f:
                fallback = b'"USER_INPUT"' not in f.read()
        else:
            fallback = False
        yield from entries(json_rows(path), session["tool"], fallback)

    TAIL_PROMPT_MAX_BYTES = 8 * 1024 * 1024

    def transcript_window(self, session, tail_bytes=131072):
        """Fast tail-first transcript reader: reads only last `tail_bytes` (auto-expanding once if <20 msgs)."""
        if session.get("tool") == "opencode":
            return list(self.transcript(session)), False
        path = session.get("path")
        if not path or not os.path.isfile(path):
            return [], False
        try:
            size = os.path.getsize(path)
        except OSError:
            return [], False
        if not tail_bytes or size <= tail_bytes:
            return list(self.transcript(session)), False

        cur_tail = tail_bytes
        while True:
            try:
                with open(path, "rb") as f:
                    head_chunk = f.read(16384)
                    head_end = head_chunk.rfind(b"\n")
                    head_lines = head_chunk[:head_end + 1].splitlines() if head_end != -1 else []
                    f.seek(max(0, size - cur_tail), os.SEEK_SET)
                    tail_chunk = f.read()
            except OSError:
                return [], False
            first_nl = tail_chunk.find(b"\n")
            tail_lines = tail_chunk[first_nl + 1:].splitlines() if first_nl != -1 else tail_chunk.splitlines()
            rows = []
            n_head = 0
            for i, raw_line in enumerate(head_lines[:8] + tail_lines):
                try:
                    r = json.loads(raw_line)
                    if isinstance(r, dict):
                        rows.append(r)
                        n_head += i < len(head_lines[:8])
                except (ValueError, UnicodeError):
                    continue
            fallback = session["tool"] == "agy" and not any(r.get("type") == "USER_INPUT" for r in rows)
            parsed = list(entries(rows, session["tool"], fallback))
            msg_cnt = sum(1 for e in parsed if e.get("kind") == "message")
            # Head lines always carry the first prompt; past them, a tail of pure tool output (a long agent run)
            # still has to reach back to the latest prompt, or the preview has no prompt to show or jump to
            tail_prompt = any(e.get("kind") == "message" and e.get("role") == "user"
                              for e in entries(rows[n_head:], session["tool"], fallback))
            if ((msg_cnt < 20 and cur_tail < 524288) or (not tail_prompt and cur_tail < self.TAIL_PROMPT_MAX_BYTES)) \
                    and size > cur_tail:
                cur_tail = min(size, cur_tail * 4)
                if cur_tail >= size:
                    return list(self.transcript(session)), False
                continue
            return parsed, True


