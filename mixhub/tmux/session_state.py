#!/usr/bin/env python3
"""`mixhub.tmux.session_state`: Per-session state files around a CLI's life in `hubpool`: hibernating markers,
last-exit records, promoted `new-N` sessions, and `active_working_set.json`.
"""
from __future__ import annotations

import json
import os
import time

from mixhub.tui import paths as _paths

_PROMOTED_NEW_SESSIONS: dict[str, tuple[str, str, str]] = {}


def _hibernating_marker_path(sid: str) -> str:
    safe = str(sid).replace(os.sep, "_").replace("\x00", "_")
    return os.path.join(_paths.HIBERNATING_MARKER_DIR, safe)


def _clear_hibernating(sid: str) -> None:
    if not sid:
        return
    try:
        os.remove(_hibernating_marker_path(sid))
    except OSError:
        pass


def _clear_last_exit(sid: str, last_exit_dir: str | None = None) -> None:
    if not sid:
        return
    try:
        os.remove(os.path.join(last_exit_dir or _paths.LAST_EXIT_DIR, f"{sid}.json"))
    except OSError:
        pass


def _record_promoted_new_session(new_tool: str, new_id: str, real_tool: str, real_id: str, real_wname: str) -> None:
    if not new_id or not real_id:
        return
    key = str(new_id)
    if not key.startswith("new-"):
        key = f"new-{key}"
    _PROMOTED_NEW_SESSIONS[key] = (str(real_tool), str(real_id), str(real_wname))
    _PROMOTED_NEW_SESSIONS[str(new_id)] = (str(real_tool), str(real_id), str(real_wname))
    try:
        os.makedirs(os.path.dirname(_paths.PROMOTED_NEW_MAP_PATH) or ".", exist_ok=True)
        data = {}
        if os.path.isfile(_paths.PROMOTED_NEW_MAP_PATH):
            try:
                with open(_paths.PROMOTED_NEW_MAP_PATH, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    data = loaded
            except (OSError, ValueError):
                data = {}
        now_ts = time.time()
        data = {k: v for k, v in data.items() if isinstance(v, dict) and (now_ts - float(v.get("ts") or 0.0)) < 86400}
        entry = {"tool": str(real_tool), "id": str(real_id), "wname": str(real_wname), "ts": now_ts}
        data[key] = entry
        data[str(new_id)] = entry
        tmp = f"{_paths.PROMOTED_NEW_MAP_PATH}.tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, _paths.PROMOTED_NEW_MAP_PATH)
    except OSError:
        pass


def _resolve_promoted_new_session(tool: str, sid: str, wname: str) -> tuple[str, str, str]:
    s_id = str(sid or "").strip()
    if not s_id:
        return str(tool or ""), s_id, str(wname or "")
    hit = _PROMOTED_NEW_SESSIONS.get(s_id)
    if hit:
        return hit
    if s_id.startswith("new-") or (wname and "-new-" in str(wname)):
        try:
            with open(_paths.PROMOTED_NEW_MAP_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                rec = data.get(s_id)
                if isinstance(rec, dict) and rec.get("id"):
                    return (
                        str(rec.get("tool") or tool or ""),
                        str(rec["id"]),
                        str(rec.get("wname") or wname or ""),
                    )
        except (OSError, ValueError):
            pass
    return str(tool or ""), s_id, str(wname or "")


def _update_working_set_file(tool: str, sid: str, path: str | None = None, *, keep: bool = False,
                             reason: str = "") -> bool:
    """Update one membership under an exclusive lock, preserving other writers' entries and order."""
    try:
        import fcntl
    except ImportError:  # native Windows: no advisory locks
        fcntl = None

    ws_path = path or os.path.join(_paths.ROOT, "active_working_set.json")
    lock_path = f"{ws_path}.lock"
    changed = False
    try:
        os.makedirs(os.path.dirname(ws_path) or ".", exist_ok=True)
        with open(lock_path, "a+", encoding="utf-8") as lock_f:
            if fcntl is not None:
                fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
            try:
                try:
                    with open(ws_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                except FileNotFoundError:
                    data = {"sessions": []}
                except (OSError, ValueError):
                    return False
                if isinstance(data, dict) and isinstance(data.get("sessions"), list):
                    items = list(data["sessions"])
                    wrap = True
                elif isinstance(data, list):
                    items = list(data)
                    wrap = False
                else:
                    return False
                drop = {str(sid), f"{tool}:{sid}"}
                kept = [x for x in items if str(x) not in drop]
                if keep:
                    if f"{tool}:{sid}" in items:
                        return False
                    kept.append(f"{tool}:{sid}")
                if kept == items:
                    return False
                out = dict(data, sessions=kept, updatedAt=time.time()) if wrap else kept
                tmp = f"{ws_path}.tmp.{os.getpid()}"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(out, f, ensure_ascii=False, indent=2)
                    f.write("\n")
                os.replace(tmp, ws_path)
                changed = True
                if not keep:
                    from mixhub.tui.diag import log_tui_diag

                    log_tui_diag("working_set_removed", session_id=str(sid), tool=str(tool), reason=reason or "unspecified")
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)
    except OSError:
        return changed
    return changed


def _remove_from_working_set_file(tool: str, sid: str, path: str | None = None, reason: str = "") -> bool:
    return _update_working_set_file(tool, sid, path, reason=reason)
