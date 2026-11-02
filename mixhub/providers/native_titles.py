"""Bidirectional native title sync: read and write each CLI's own session title store (Claude / agy / Codex)."""
from __future__ import annotations

import glob
import json
import os
import re
import sqlite3
import urllib.parse

from mixhub.core.env import env
from mixhub.providers.title_overrides import (
    _clean_title_text,
    _lookup_override,
    normalize_agent_name,
    save_title_override,
)


def _resolve_claude_jsonl(session_id: str, path: str | None, home_dir: str | None) -> str:
    if path:
        ap = os.path.abspath(os.path.expanduser(path))
        if os.path.isdir(ap):
            cand = os.path.join(ap, f"{session_id}.jsonl")
            if os.path.isfile(cand):
                return cand
        return ap
    eff_home = os.path.abspath(os.path.expanduser(home_dir or env("AGENT_HOME") or "~"))
    matches = glob.glob(os.path.join(eff_home, ".claude", "projects", "*", f"{session_id}.jsonl"))
    return matches[0] if matches else ""


def _resolve_agy_locations(
    session_id: str,
    path: str | None,
    home_dir: str | None,
    db_path: str | None,
) -> tuple[str, str]:
    """Return `(summaries_db_path, brain_transcript_or_dir_path)` for AGY."""
    eff_home = os.path.abspath(os.path.expanduser(home_dir or env("AGENT_HOME") or "~"))
    resolved_db = os.path.abspath(os.path.expanduser(db_path)) if db_path else ""
    resolved_tx = ""

    if path:
        ap = os.path.abspath(os.path.expanduser(path))
        if ap.endswith((".db", ".sqlite", ".sqlite3")):
            resolved_db = resolved_db or ap
            parent_dir = os.path.dirname(ap)
            cand_brain = os.path.join(parent_dir, "brain", session_id)
            if os.path.exists(cand_brain):
                resolved_tx = cand_brain
        else:
            resolved_tx = ap
            parts = ap.split(os.sep)
            if "brain" in parts:
                idx = parts.index("brain")
                base = os.sep.join(parts[:idx])
                cand_db = os.path.join(base, "conversation_summaries.db")
                if not resolved_db and os.path.isfile(cand_db):
                    resolved_db = cand_db
            else:
                base = ap if os.path.isdir(ap) else os.path.dirname(ap)
                for cand_db in (
                    os.path.join(base, "conversation_summaries.db"),
                    os.path.join(os.path.dirname(base), "conversation_summaries.db"),
                ):
                    if not resolved_db and os.path.isfile(cand_db):
                        resolved_db = cand_db
                        break

    if not resolved_db:
        default_db = os.path.join(eff_home, ".gemini", "antigravity-cli", "conversation_summaries.db")
        if os.path.isfile(default_db):
            resolved_db = default_db

    if not resolved_tx:
        default_brain = os.path.join(eff_home, ".gemini", "antigravity-cli", "brain", session_id)
        if os.path.exists(default_brain):
            resolved_tx = default_brain

    return resolved_db, resolved_tx


def _resolve_codex_jsonl(session_id: str, path: str | None, home_dir: str | None) -> str:
    if path:
        ap = os.path.abspath(os.path.expanduser(path))
        if os.path.isdir(ap):
            cands = glob.glob(os.path.join(ap, f"*{session_id}*.jsonl"))
            if cands:
                return cands[0]
        return ap
    eff_home = os.path.abspath(os.path.expanduser(home_dir or env("AGENT_HOME") or "~"))
    matches = glob.glob(
        os.path.join(eff_home, ".codex", "sessions", "*", "*", "*", f"*{session_id}*.jsonl")
    )
    return matches[0] if matches else ""


def read_native_title(
    agent: str,
    session_id: str,
    path: str | None = None,
    *,
    home_dir: str | None = None,
    data_dir: str | None = None,
    db_path: str | None = None,
    overrides_path: str | None = None,
) -> str:
    """
    Read a session's title across Claude Code, AGY (Gemini), and Codex:
      - Claude Code: reads the latest `{"type": "custom-title", "customTitle": ...}` record
        from the `.jsonl` transcript (falling back to `title_overrides.json`, `aiTitle`,
        `summary`, or first user prompt).
      - AGY (Gemini): reads `title_overrides.json`, `conversation_summaries.db`
        (`title` / `preview`), or `brain/<cid>` (`transcript.jsonl` / `USER_INPUT` / step 0).
      - Codex: reads `title_overrides.json`, or `rollout-*.jsonl` (`session_meta` title /
        first user message).
    """
    norm_agent = normalize_agent_name(agent)
    sid = str(session_id or "").strip()

    if norm_agent == "claude":
        jsonl_path = _resolve_claude_jsonl(sid, path, home_dir)
        custom_title = ""
        ai_title = ""
        native_summary = ""
        first_prompt = ""
        if jsonl_path and os.path.isfile(jsonl_path):
            try:
                with open(jsonl_path, encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            row = json.loads(line)
                        except ValueError:
                            continue
                        if not isinstance(row, dict):
                            continue
                        if row.get("type") == "custom-title" and row.get("customTitle"):
                            if not sid or not row.get("sessionId") or row.get("sessionId") == sid:
                                custom_title = _clean_title_text(str(row["customTitle"]), "claude")
                        elif row.get("customTitle"):
                            custom_title = _clean_title_text(str(row["customTitle"]), "claude")
                        if row.get("aiTitle"):
                            ai_title = _clean_title_text(str(row["aiTitle"]), "claude")
                        if row.get("type") == "summary" and row.get("summary"):
                            native_summary = _clean_title_text(str(row["summary"]), "claude")
                        if not first_prompt and row.get("type") == "user":
                            msg = row.get("message")
                            content = msg.get("content") if isinstance(msg, dict) else row.get("content")
                            if isinstance(content, str):
                                first_prompt = _clean_title_text(content, "claude")
                            elif isinstance(content, list):
                                for blk in content:
                                    if isinstance(blk, dict) and blk.get("type") == "text" and blk.get("text"):
                                        first_prompt = _clean_title_text(str(blk["text"]), "claude")
                                        if first_prompt:
                                            break
            except OSError:
                pass
        if custom_title:
            return custom_title
        ov_title = _lookup_override(
            "claude",
            sid,
            overrides_path=overrides_path,
            path=jsonl_path or path,
            home_dir=home_dir,
            data_dir=data_dir,
        )
        if ov_title:
            return ov_title
        return ai_title or native_summary or first_prompt or ""

    if norm_agent == "agy":
        resolved_db, resolved_tx = _resolve_agy_locations(sid, path, home_dir, db_path)
        ov_title = _lookup_override(
            "agy",
            sid,
            overrides_path=overrides_path,
            path=path or resolved_db or resolved_tx,
            home_dir=home_dir,
            data_dir=data_dir,
        )
        if ov_title:
            return ov_title

        # 1. Check SQLite conversation_summaries.db
        if resolved_db and os.path.isfile(resolved_db):
            conn = None
            try:
                conn = sqlite3.connect(
                    "file:" + urllib.parse.quote(resolved_db) + "?mode=ro",
                    uri=True,
                    timeout=2.0,
                )
                row = conn.execute(
                    "SELECT title, preview FROM conversation_summaries WHERE conversation_id = ?",
                    (sid,),
                ).fetchone()
                if row:
                    db_title = _clean_title_text(str(row[0] or ""), "agy")
                    if db_title:
                        return db_title
                    db_preview = _clean_title_text(str(row[1] or ""), "agy")
                    if db_preview:
                        return db_preview
            except sqlite3.Error:
                pass
            finally:
                if conn is not None:
                    conn.close()

        # 2. Check brain/<cid> directory or transcript.jsonl
        if resolved_tx:
            tx_candidates: list[str] = []
            if os.path.isfile(resolved_tx):
                tx_candidates.append(resolved_tx)
            elif os.path.isdir(resolved_tx):
                tx_candidates.extend(
                    [
                        os.path.join(resolved_tx, ".system_generated", "logs", "transcript.jsonl"),
                        os.path.join(resolved_tx, "transcript.jsonl"),
                        os.path.join(resolved_tx, "brain", sid, ".system_generated", "logs", "transcript.jsonl"),
                        os.path.join(resolved_tx, "brain", sid, "transcript.jsonl"),
                    ]
                )
            for tx_file in tx_candidates:
                if not os.path.isfile(tx_file):
                    continue
                try:
                    sys_fallback = ""
                    with open(tx_file, encoding="utf-8", errors="replace") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                row = json.loads(line)
                            except ValueError:
                                continue
                            if not isinstance(row, dict):
                                continue
                            if row.get("title") or row.get("customTitle"):
                                return _clean_title_text(
                                    str(row.get("customTitle") or row.get("title")), "agy"
                                )
                            if row.get("type") == "USER_INPUT" and row.get("content"):
                                cleaned = _clean_title_text(str(row["content"]), "agy")
                                if cleaned:
                                    return cleaned
                            if (
                                not sys_fallback
                                and row.get("type") == "SYSTEM_MESSAGE"
                                and row.get("step_index") == 0
                            ):
                                m = re.search(
                                    r"\[Message\].*?\bcontent=(.*)",
                                    str(row.get("content") or ""),
                                    re.S,
                                )
                                if m:
                                    sys_fallback = _clean_title_text(m.group(1), "agy")
                    if sys_fallback:
                        return sys_fallback
                except OSError:
                    pass
        return ""

    if norm_agent in ("codex", "deepseek"):
        rollout_path = _resolve_codex_jsonl(sid, path, home_dir)
        ov_title = _lookup_override(
            "codex",
            sid,
            overrides_path=overrides_path,
            path=rollout_path or path,
            home_dir=home_dir,
            data_dir=data_dir,
        )
        if ov_title:
            return ov_title

        meta_title = ""
        first_user_msg = ""
        if rollout_path and os.path.isfile(rollout_path):
            try:
                with open(rollout_path, encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            row = json.loads(line)
                        except ValueError:
                            continue
                        if not isinstance(row, dict):
                            continue
                        rtype = row.get("type")
                        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
                        meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}

                        if rtype in ("session_meta", "meta", "custom-title"):
                            for src in (row, payload, meta):
                                for k in ("customTitle", "title", "thread_name", "summary"):
                                    if src.get(k):
                                        cand = _clean_title_text(str(src[k]), "codex")
                                        if cand:
                                            meta_title = cand
                                            break
                                if meta_title:
                                    break
                        elif rtype == "response_item":
                            if payload.get("type") == "message" and payload.get("role") == "user" and not first_user_msg:
                                content = payload.get("content") or []
                                if isinstance(content, str):
                                    first_user_msg = _clean_title_text(content, "codex")
                                elif isinstance(content, list):
                                    for blk in content:
                                        if isinstance(blk, dict) and blk.get("text"):
                                            cand = _clean_title_text(str(blk["text"]), "codex")
                                            if cand:
                                                first_user_msg = cand
                                                break
                        elif rtype == "event_msg":
                            if payload.get("type") == "user_message" and payload.get("message") and not first_user_msg:
                                first_user_msg = _clean_title_text(str(payload["message"]), "codex")
                        elif (rtype == "user" or row.get("role") == "user") and not first_user_msg:
                            msg = row.get("message") if isinstance(row.get("message"), dict) else row
                            content = msg.get("content") or msg.get("text") or ""
                            if isinstance(content, str):
                                first_user_msg = _clean_title_text(content, "codex")
                            elif isinstance(content, list):
                                for blk in content:
                                    if isinstance(blk, dict) and blk.get("text"):
                                        cand = _clean_title_text(str(blk["text"]), "codex")
                                        if cand:
                                            first_user_msg = cand
                                            break
            except OSError:
                pass
        return meta_title or first_user_msg or ""

    return _lookup_override(
        norm_agent,
        sid,
        overrides_path=overrides_path,
        path=path,
        home_dir=home_dir,
        data_dir=data_dir,
    )


def write_native_title(
    agent: str,
    session_id: str,
    path: str | None = None,
    new_title: str = "",
    *,
    home_dir: str | None = None,
    data_dir: str | None = None,
    db_path: str | None = None,
    overrides_path: str | None = None,
    dry_run: bool = False,
) -> dict:
    """
    Write `new_title` back to the vendor's native store AND `title_overrides.json`:
      - Claude Code: appends `{"type": "custom-title", "customTitle": new_title, "sessionId": session_id}`
        to the `.jsonl` file and updates `title_overrides.json`.
      - AGY (Gemini): updates `title_overrides.json` and, if `conversation_summaries.db`
        has a matching row and is writable, updates `conversation_summaries.title`.
      - Codex: updates `title_overrides.json`.
    """
    norm_agent = normalize_agent_name(agent)
    sid = str(session_id or "").strip()
    clean_title = _clean_title_text(new_title, norm_agent)
    if not sid or not clean_title:
        return {
            "ok": False,
            "agent": norm_agent,
            "session_id": sid,
            "title": clean_title,
            "native_updated": False,
            "overrides_path": "",
        }

    native_updated = False
    resolved_target = path or ""

    if norm_agent == "claude":
        jsonl_path = _resolve_claude_jsonl(sid, path, home_dir)
        resolved_target = jsonl_path or (path or "")
        ov_file = save_title_override(
            "claude",
            sid,
            clean_title,
            overrides_path=overrides_path,
            path=resolved_target or None,
            home_dir=home_dir,
            data_dir=data_dir,
            dry_run=dry_run,
        )
        if jsonl_path and (os.path.isfile(jsonl_path) or os.path.isdir(os.path.dirname(jsonl_path))):
            if not dry_run:
                try:
                    prev_stat = os.stat(jsonl_path) if os.path.isfile(jsonl_path) else None
                    record = (
                        json.dumps(
                            {"type": "custom-title", "customTitle": clean_title, "sessionId": sid},
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
                    with open(jsonl_path, "a", encoding="utf-8") as f:
                        f.write(record)
                        f.flush()
                        os.fsync(f.fileno())
                    if prev_stat is not None:
                        os.utime(jsonl_path, (prev_stat.st_atime, prev_stat.st_mtime))
                    native_updated = True
                except OSError:
                    native_updated = False
            else:
                native_updated = True
        return {
            "ok": True,
            "agent": "claude",
            "session_id": sid,
            "title": clean_title,
            "path": resolved_target,
            "overrides_path": ov_file,
            "native_updated": native_updated,
        }

    if norm_agent == "agy":
        resolved_db, resolved_tx = _resolve_agy_locations(sid, path, home_dir, db_path)
        resolved_target = path or resolved_db or resolved_tx
        ov_file = save_title_override(
            "agy",
            sid,
            clean_title,
            overrides_path=overrides_path,
            path=resolved_target or None,
            home_dir=home_dir,
            data_dir=data_dir,
            dry_run=dry_run,
        )
        if resolved_db and os.path.isfile(resolved_db):
            if not dry_run:
                conn = None
                try:
                    conn = sqlite3.connect(resolved_db, timeout=1.5)
                    cur = conn.execute(
                        "UPDATE conversation_summaries SET title = ? WHERE conversation_id = ?",
                        (clean_title, sid),
                    )
                    conn.commit()
                    native_updated = (cur.rowcount or 0) > 0
                except sqlite3.Error:
                    native_updated = False
                finally:
                    if conn is not None:
                        conn.close()
            else:
                native_updated = True
        return {
            "ok": True,
            "agent": "agy",
            "session_id": sid,
            "title": clean_title,
            "path": resolved_target,
            "db_path": resolved_db,
            "overrides_path": ov_file,
            "native_updated": native_updated,
        }

    # Codex / DeepSeek / generic
    rollout_path = _resolve_codex_jsonl(sid, path, home_dir)
    resolved_target = rollout_path or (path or "")
    ov_file = save_title_override(
        norm_agent,
        sid,
        clean_title,
        overrides_path=overrides_path,
        path=resolved_target or None,
        home_dir=home_dir,
        data_dir=data_dir,
        dry_run=dry_run,
    )
    return {
        "ok": True,
        "agent": norm_agent,
        "session_id": sid,
        "title": clean_title,
        "path": resolved_target,
        "overrides_path": ov_file,
        "native_updated": True,
    }
