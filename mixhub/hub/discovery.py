"""Session discovery across Claude / Codex / agy / OpenCode stores: transcripts, rollouts, lock probes, merge."""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import time
from typing import Any
import urllib.parse

try:
    import fcntl
except ImportError:  # Windows native (win32) fallback
    fcntl = None

from mixhub.hub import session_filters
from mixhub.hub.constants import (
    _OPEN_LOCK_PATTERNS,
    _OPEN_PATH_PATTERNS,
    _ROLLOUT_UUID_RE,
    _UUID_RE,
    TOOLS,
    UUID,
    WORKING_SET_IDLE_EXPIRE_SEC,
)
from mixhub.hub.index import SessionIndex
from mixhub.hub.opencode import opencode_session_rows
from mixhub.hub.schema import claude_is_headless_marker, codex_state_db_candidates, PROVIDER_SCHEMA_ADAPTERS
from mixhub.hub.session_filters import (
    _load_local_rules,
    _LOCAL_RULES_CACHE,
    is_automated_or_subagent_text,
    is_users_own_prompt,
    is_codex_automated,
    is_temp_session,
)
from mixhub.hub.shell import resume_command
from mixhub.hub.text import clean_prompt, snippet
from mixhub.hub.titles import _is_generic_placeholder_title
from mixhub.hub.transcript import json_rows
from mixhub.hub.util import _file_sig, clean_hostname, read_json, stamp
from mixhub.i18n import t
from mixhub.proc.identity import get_boot_id
from mixhub.proc.tree import has_agent_ancestor


def settle_parked_status(value: dict, jobs_dir: str) -> dict:
    """
    A Claude session that parked its turn into a background job (`parkedJobId`) keeps the `busy` it had at that
    moment in its pid file. The work runs in the job, so the session is busy only while the job is still working.
    """
    job_id = str(value.get("parkedJobId") or "")
    if job_id and value.get("status") == "busy":
        job = read_json(os.path.join(jobs_dir, job_id, "state.json"), {})
        if not isinstance(job, dict) or job.get("state") != "working":
            value["status"] = "idle"
            # Its transcript also stops mid-turn at the hand-off, which would otherwise still read as working
            value["_parkedDone"] = True
    return value


class DiscoveryMixin:
    """Discover sessions from every provider store and merge them with live processes and hub state."""

    def _discover_agy_transcripts(self) -> list[str]:
        brain_dir = self.path(".gemini/antigravity-cli/brain")
        if not os.path.isdir(brain_dir):
            self._known_agy_transcripts.clear()
            self._agy_missing_uids.clear()
            self._agy_brain_sig = None
            return []
        cur_sig = _file_sig(brain_dir)
        if cur_sig != self._agy_brain_sig:
            try:
                current_uids = {e.name for e in os.scandir(brain_dir) if e.is_dir(follow_symlinks=False)}
            except OSError:
                return list(glob.iglob(self.path(PROVIDER_SCHEMA_ADAPTERS["agy"]["transcript_glob"])))
            for gone in list(self._known_agy_transcripts):
                if gone not in current_uids:
                    self._known_agy_transcripts.pop(gone, None)
            self._agy_missing_uids = current_uids - set(self._known_agy_transcripts)
            self._agy_brain_sig = cur_sig
        for uid in list(self._agy_missing_uids):
            cand = os.path.join(brain_dir, uid, ".system_generated", "logs", "transcript.jsonl")
            if os.path.isfile(cand):
                self._known_agy_transcripts[uid] = cand
                self._agy_missing_uids.discard(uid)
        return list(self._known_agy_transcripts.values())

    def _load_agy_tui_history(self) -> dict | None:
        """Ids and (time, workspace, text) of prompts the user typed in the agy TUI; None when there is no usable history
        (fail open). Notes another agent relayed into a TUI are left out: they don't show the user was there."""
        path = self.path(PROVIDER_SCHEMA_ADAPTERS["agy"]["tui_history"])
        sig = _file_sig(path)
        if sig is None:
            return None
        if self._agy_tui_history[0] == sig:
            return self._agy_tui_history[1]
        cids, prompts, oldest = set(), [], None
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                        ts = float(entry["timestamp"]) / 1000
                    except (ValueError, TypeError, KeyError):
                        continue
                    if entry.get("type") == "slash_command":
                        continue
                    oldest = ts if oldest is None else min(oldest, ts)
                    text = str(entry.get("display") or "")
                    if not is_users_own_prompt(text):
                        continue
                    if entry.get("conversationId"):
                        cids.add(entry["conversationId"])
                    prompts.append((ts, entry.get("workspace") or "", text))
        except OSError:
            return None
        hist = {"sig": sig, "cids": cids, "prompts": prompts, "oldest": oldest}
        self._agy_tui_history = (sig, hist if oldest is not None else None)
        return self._agy_tui_history[1]

    def _agy_user_requests(self, cid: str) -> set[str] | None:
        """Whitespace-collapsed `<USER_REQUEST>` bodies of a conversation's transcript; None when it can't be read."""
        path = self.path(PROVIDER_SCHEMA_ADAPTERS["agy"]["transcript_glob"].replace("*", cid, 1))
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                bodies = set()
                for line in f:
                    if '"USER_INPUT"' not in line:
                        continue
                    try:
                        content = str(json.loads(line).get("content") or "")
                    except ValueError:
                        continue
                    m = re.search(r"<USER_REQUEST>(.*?)</USER_REQUEST>", content, re.S)
                    bodies.add(" ".join((m.group(1) if m else content).split()))
                return bodies
        except OSError:
            return None

    def _agy_tui_prompt(self, hist: dict | None, cid: str, input_at: float, cwd: str) -> bool | None:
        """Whether the row's last input was typed in the agy TUI by the user: its id is in history.jsonl, or a prompt
        there matches its time, workspace and text (time and workspace alone when the transcript can't be read).
        None when history can't tell (missing, or older than the input)."""
        if not hist or not input_at or input_at < hist["oldest"]:
            return None
        if cid in hist["cids"]:
            return True
        window = float(PROVIDER_SCHEMA_ADAPTERS["agy"]["tui_history_match_s"])
        near = [text for ts, ws, text in hist["prompts"] if abs(ts - input_at) <= window and (not ws or not cwd or ws == cwd)]
        if not near:
            return False
        bodies = self._agy_user_requests(cid)
        if bodies is None:
            return True
        return any(" ".join(text.split()) in bodies for text in near)

    def _agy_headless_continuation(self, hist: dict | None, cid: str, input_at: float, cwd: str) -> bool:
        """`agy -p --conversation <cid>` leaves a row with real user input but no TUI prompt in history.jsonl."""
        return self._agy_tui_prompt(hist, cid, input_at, cwd) is False

    def _discover_codex_rollouts(self) -> tuple[list[str], dict[str, str]]:
        sess_root = self.path(".codex/sessions")
        arch_root = self.path(".codex/archived_sessions")
        dir_sigs = []
        for root_d in (sess_root, arch_root):
            s = _file_sig(root_d)
            if s is not None:
                dir_sigs.append((root_d, s[2]))
                try:
                    for y in os.scandir(root_d):
                        if y.is_dir(follow_symlinks=False):
                            dir_sigs.append((y.path, y.stat(follow_symlinks=False).st_mtime_ns))
                            for m in os.scandir(y.path):
                                if m.is_dir(follow_symlinks=False):
                                    dir_sigs.append((m.path, m.stat(follow_symlinks=False).st_mtime_ns))
                                    for d in os.scandir(m.path):
                                        if d.is_dir(follow_symlinks=False):
                                            dir_sigs.append((d.path, d.stat(follow_symlinks=False).st_mtime_ns))
                except OSError:
                    pass
        sig_tuple = tuple(dir_sigs)
        if sig_tuple and self._codex_glob_cache[0] == sig_tuple:
            return self._codex_glob_cache[1], self._codex_glob_cache[2]
        codex_session_files = list(glob.iglob(self.path(".codex/sessions/*/*/*/rollout-*.jsonl")))
        codex_disk_paths = {}
        for path in codex_session_files + list(glob.iglob(self.path(".codex/archived_sessions/**/rollout-*.jsonl"), recursive=True)):
            match = _UUID_RE.search(os.path.basename(path))
            if match:
                codex_disk_paths.setdefault(match.group(0), path)
        self._codex_glob_cache = (sig_tuple, codex_session_files, codex_disk_paths)
        return codex_session_files, codex_disk_paths

    def _probe_active_lock_uuids(self, sessions=None):
        """
        Probe `~/.codex/thread-writer-locks/*.lock` and `~/.gemini/antigravity-cli/presence/*.lock`
        using non-blocking `fcntl.flock(LOCK_EX | LOCK_NB)` (or mtime fallback on Windows) to detect
        sessions actively held by a live CLI or daemon (such as `codex app-server --managed-daemon`).
        Returns `{"codex": {uuid: lock_mtime}, "agy": {uuid: lock_mtime}}`.
        """
        # Legacy callers passed a lock directory rather than a session mapping.
        if isinstance(sessions, (str, os.PathLike)):
            sessions = None
        active = {"codex": {}, "agy": {}}
        now_mono = time.monotonic()
        now_wall = time.time()
        specs = (
            ("codex", self.path(".codex/thread-writer-locks")),
            ("agy", self.path(".gemini/antigravity-cli/presence")),
        )
        for tool, lock_dir in specs:
            if not os.path.isdir(lock_dir):
                continue
            dir_sig = _file_sig(lock_dir)
            cold_entry = self._cold_unlocked_locks.get(tool)
            use_cold_cache = fcntl is not None and isinstance(sessions, SessionIndex)
            if (
                use_cold_cache
                and cold_entry is not None
                and (now_mono - cold_entry[0]) < 8.0
                and cold_entry[1] == dir_sig
            ):
                cold_skip = cold_entry[2]
                updating_cold = False
            else:
                cold_skip = set()
                new_cold: set[str] = set()
                updating_cold = use_cold_cache
            try:
                with os.scandir(lock_dir) as it:
                    for entry in it:
                        name = entry.name
                        if not name.endswith(".lock"):
                            continue
                        uid = name[:-5]
                        if uid in cold_skip:
                            continue
                        if not _UUID_RE.fullmatch(uid):
                            continue
                        if sessions is not None and tool in ("codex", "agy"):
                            s_existing = sessions.get(tool + ":" + uid, {})
                            if (s_existing.get("parentId") or s_existing.get("isSubagent")
                                    or (tool == "codex" and s_existing.get("automated"))):
                                continue
                        if fcntl is None:
                            try:
                                st = entry.stat()
                                if (now_wall - st.st_mtime) < 60:
                                    active[tool][uid] = st.st_mtime
                            except OSError:
                                pass
                            continue
                        fd = -1
                        try:
                            fd = os.open(entry.path, os.O_RDONLY)
                            try:
                                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                                fcntl.flock(fd, fcntl.LOCK_UN)
                                if updating_cold:
                                    st_un = os.fstat(fd)
                                    if (now_wall - st_un.st_mtime) >= 60.0:
                                        new_cold.add(uid)
                            except (BlockingIOError, PermissionError):
                                st = os.fstat(fd)
                                active[tool][uid] = st.st_mtime
                        except OSError:
                            pass
                        finally:
                            if fd >= 0:
                                try:
                                    os.close(fd)
                                except OSError:
                                    pass
                if updating_cold:
                    self._cold_unlocked_locks[tool] = (now_mono, dir_sig, new_cold)
            except OSError:
                pass
        return active

    def discover(self, force=False):
        with self.lock:
            if not force and self.scanned and time.monotonic() - self.scanned < 4:
                return self.sessions
            session_filters._DISCOVER_GEN += 1
            _load_local_rules()
            session_filters._IN_DISCOVER = True
            try:
                return self._discover_locked()
            finally:
                session_filters._IN_DISCOVER = False

    def _discover_locked(self):
        if True:
            rules_mtime = _LOCAL_RULES_CACHE.get("mtime")
            self.warnings = []
            state = self.state()["sessions"]
            labels = read_json(self.path(".claude/session_labels.json"), {})
            pid_states = {}
            sdk_cli_sids = set()
            for path in glob.iglob(self.path(PROVIDER_SCHEMA_ADAPTERS["claude"]["pid_state_glob"])):
                value = read_json(path, {})
                if isinstance(value, dict) and value.get("pid"):
                    # Positive headless set only: an unknown future interactive `kind` ("tui", "desktop",
                    # "remote-control" ...) must not mark a live human session automated / not-live.
                    if claude_is_headless_marker(value.get("entrypoint"), value.get("kind")):
                        if value.get("sessionId"):
                            sdk_cli_sids.add(value["sessionId"])
                        continue
                    try:
                        value["_mtime"] = os.stat(path).st_mtime
                    except OSError:
                        value["_mtime"] = 0
                    pid_states[int(value["pid"])] = settle_parked_status(value, self.path(".claude/jobs"))
            self.sessions.close()
            self.sessions = sessions = SessionIndex()

            def add(tool, sid, values=None):
                key = tool + ":" + sid
                session = sessions.setdefault(key, {"id": sid, "tool": tool, "badge": TOOLS[tool],
                    "cwd": "", "updatedAt": 0, "messageCount": 0, "messageCountExact": False,
                    "isLive": False, "processes": [], "lastPrompt": "", "firstPrompt": ""})
                if values:
                    session.update(values)
                return session

            for snap in read_json(self.path(".claude/session-snapshot.json"), {}).get("sessions", []):
                if snap.get("uuid"):
                    add("claude", snap["uuid"], {"cwd": snap.get("cwd", ""), "updatedAt": stamp(snap.get("mtime")),
                                               "snapshotSummary": snap.get("summary", "")})
            for path in glob.iglob(self.path(PROVIDER_SCHEMA_ADAPTERS["claude"]["transcript_glob"])):
                sid = os.path.basename(path)[:-6]
                try:
                    s = add("claude", sid, dict(self.summary(path, "claude"), path=path))
                except OSError:
                    continue
                # Sidecar and project index only enrich sessions that have a `.jsonl` transcript.
                project_dir = os.path.dirname(path)
                sidecar_path = os.path.join(project_dir, sid, "custom-title.json")
                if os.path.isfile(sidecar_path):
                    sc = read_json(sidecar_path, {})
                    if isinstance(sc, dict) and sc.get("customTitle"):
                        s["sidecarCustomTitle"] = snippet(clean_prompt(sc["customTitle"], "claude"), 220)
                        try:
                            s["sidecarMtime"] = os.stat(sidecar_path).st_mtime
                        except OSError:
                            s["sidecarMtime"] = 0.0
                idx_entry = self._claude_project_index_entry(project_dir, sid)
                if idx_entry:
                    if idx_entry.get("customTitle"):
                        s["indexCustomTitle"] = snippet(clean_prompt(idx_entry["customTitle"], "claude"), 220)
                    if idx_entry.get("aiTitle"):
                        s["indexAiTitle"] = snippet(clean_prompt(idx_entry["aiTitle"], "claude"), 220)
                    if idx_entry.get("summary"):
                        s["indexSummary"] = snippet(clean_prompt(idx_entry["summary"], "claude"), 220)
                    if not s.get("firstPrompt") and idx_entry.get("firstPrompt"):
                        s["firstPrompt"] = snippet(clean_prompt(idx_entry["firstPrompt"], "claude"))
            for pid, value in pid_states.items():
                if value.get("sessionId"):
                    s = add("claude", value["sessionId"])
                    if value.get("cwd"):
                        s["cwd"] = value["cwd"]
                    if value.get("nameSource") == "user":
                        s["userName"] = value.get("name")
                        s["userNameMtime"] = float(value.get("_mtime") or 0.0)
                    else:
                        s["slug"] = value.get("name", "")

            agy_columns = ["conversation_id", "title", "preview", "step_count", "last_modified_time", "workspace_uris", "status", "not_fully_idle", "killed", "agent_name", "parent_conversation_id", "nesting_depth", "last_user_input_time", "last_user_input_step_index"]
            # `agy -p` headless runs hold a presence lock from t=0 while `step_count == 0` and
            # `last_user_input_step_index == -1`; they must never surface as live/working-set rows.
            saw_agy_ui_col = False
            agy_db_sids = set()
            agy_adapter = PROVIDER_SCHEMA_ADAPTERS["agy"]
            agy_db_path = self.path(agy_adapter["summary_db"])
            agy_tui_hist = self._load_agy_tui_history()
            for row in self.rows(agy_db_path, agy_adapter["summary_table"], agy_columns):
                cid = row["conversation_id"]
                agy_db_sids.add(cid)
                has_ui_col = "last_user_input_step_index" in row
                if has_ui_col:
                    saw_agy_ui_col = True
                raw_prev_col = row.get("preview") or ""
                raw_ws_col = row.get("workspace_uris") or ""
                agy_sig = (
                    row.get("last_modified_time"),
                    row.get("step_count"),
                    row.get("status"),
                    row.get("not_fully_idle"),
                    row.get("killed"),
                    row.get("title"),
                    len(raw_prev_col),
                    raw_prev_col[:128],
                    len(raw_ws_col),
                    raw_ws_col[:128],
                    row.get("agent_name"),
                    row.get("parent_conversation_id"),
                    row.get("last_user_input_step_index"),
                    row.get("last_user_input_time"),
                    has_ui_col,
                    agy_tui_hist and agy_tui_hist["sig"],
                    rules_mtime,
                )
                memo = self._agy_row_memo.get(cid)
                if memo is not None and memo[0] == agy_sig:
                    agy_vals = memo[1]
                    raw_agy_title = agy_vals["sourceTitle"]
                    parent_cid = agy_vals["parentId"]
                    agent_nm = agy_vals["agentName"]
                    is_auto_agy = agy_vals["automated"]
                    s_agy = add("agy", cid, agy_vals)
                else:
                    try:
                        workspaces = json.loads(row.get("workspace_uris") or "[]")
                        cwd = urllib.parse.unquote(urllib.parse.urlparse(workspaces[0]).path) if workspaces else ""
                    except (ValueError, TypeError, IndexError):
                        cwd = ""
                    ui_idx = row.get("last_user_input_step_index")
                    try:
                        no_user_input = bool(has_ui_col and (ui_idx is None or int(ui_idx) < 0))
                    except (TypeError, ValueError):
                        no_user_input = False
                    # The TUI stays at -1 until its first turn completes (or forever, if cut off mid-reply)
                    if no_user_input and self._agy_tui_prompt(agy_tui_hist, cid, stamp(row.get("last_user_input_time")), cwd):
                        no_user_input = False
                    raw_agy_title = snippet(row.get("title", ""), 200)
                    raw_agy_preview = snippet(clean_prompt(row.get("preview", ""), "agy"))
                    if _is_generic_placeholder_title(raw_agy_preview):
                        raw_agy_preview = ""
                    parent_cid = row.get("parent_conversation_id", "") or ""
                    agent_nm = row.get("agent_name", "") or ""
                    is_auto_agy = bool(
                        no_user_input
                        or is_automated_or_subagent_text(raw_agy_title)
                        or is_automated_or_subagent_text(raw_agy_preview)
                        or (has_ui_col and not parent_cid and not agent_nm and self._agy_headless_continuation(
                            agy_tui_hist, cid, stamp(row.get("last_user_input_time")), cwd))
                    )
                    agy_vals = {"sourceTitle": raw_agy_title, "lastPrompt": raw_agy_preview,
                        "cwd": cwd, "updatedAt": stamp(row.get("last_modified_time")), "stepCount": row.get("step_count", 0),
                        "parentId": parent_cid, "agentName": agent_nm, "isSubagent": bool(parent_cid or agent_nm),
                        "status": row.get("status", ""), "agyStatus": row.get("status", ""),
                        "notFullyIdle": bool(row.get("not_fully_idle")), "killed": bool(row.get("killed")),
                        "automated": is_auto_agy}
                    self._agy_row_memo[cid] = (agy_sig, agy_vals)
                    s_agy = add("agy", cid, agy_vals)
                if is_auto_agy:
                    s_agy["isTemp"] = True
                sticky = self._session_db_meta.setdefault("agy:" + cid, {})
                if raw_agy_title:
                    sticky["sourceTitle"] = raw_agy_title
                if parent_cid:
                    sticky["parentId"] = parent_cid
                    sticky["isSubagent"] = True
                if agent_nm:
                    sticky["agentName"] = agent_nm
                    sticky["isSubagent"] = True
                # Overwrite every scan (not one-way sticky): a blank human TUI starts at ui_idx == -1
                # and must flip back to non-automated as soon as its first prompt lands.
                sticky["automated"] = bool(is_auto_agy)
            now_scan = time.time()
            agy_db_mtime = 0.0
            for db_file in (agy_db_path, agy_db_path + "-wal"):  # WAL mode: writes land in -wal first
                try:
                    agy_db_mtime = max(agy_db_mtime, os.stat(db_file).st_mtime)
                except OSError:
                    pass
            agy_grace = float(agy_adapter["db_lag_grace_s"])
            agy_transcript_paths = self._discover_agy_transcripts()
            invoked_parents = self._extract_invoked_subagent_parents(agy_transcript_paths)
            for path in agy_transcript_paths:
                sid = path.split(os.sep)[-4]
                try:
                    s = add("agy", sid)
                    s["path"] = path
                    sticky = self._session_db_meta.get("agy:" + sid)
                    if sticky:
                        for sk in ("sourceTitle", "parentId", "agentName", "isSubagent", "automated"):
                            if sticky.get(sk) and not s.get(sk):
                                s[sk] = sticky[sk]
                    if sid in invoked_parents and not s.get("parentId"):
                        s["parentId"] = invoked_parents[sid]
                        s["isSubagent"] = True
                        self._session_db_meta.setdefault("agy:" + sid, {}).update(
                            {"parentId": invoked_parents[sid], "isSubagent": True}
                        )
                    if saw_agy_ui_col and sid not in agy_db_sids and not s.get("parentId"):
                        # Transcript flushed before its DB row exists: transient headless run.
                        # Not written to sticky so the real DB row decides on the next scan.
                        # Bounded quarantine (fail open): only while the transcript is young AND the DB is
                        # still being written. A stalled / abandoned / renamed DB must not hide new humans.
                        # NOTE: USER_INPUT presence is NOT a usable signal here - `agy -p` prompts are
                        # USER_INPUT/USER_EXPLICIT too (386/391 top-level transcripts on disk, 2026-10-25).
                        try:
                            t_stat = os.stat(path)
                            t_born = getattr(t_stat, "st_birthtime", t_stat.st_ctime)
                        except OSError:
                            t_born = now_scan
                        young = (now_scan - t_born) < agy_grace
                        db_alive = agy_db_mtime >= t_born - agy_grace
                        if young and db_alive:
                            s["automated"] = True
                            s["isTemp"] = True
                            continue
                    if s.get("parentId"):
                        s["isSubagent"] = True
                        st_mtime = os.stat(path).st_mtime
                        s["updatedAt"] = max(s.get("updatedAt", 0), st_mtime)
                        s["transcriptAt"] = st_mtime
                        if (now_scan - st_mtime) < 1800 or s.get("notFullyIdle") or "running" in str(s.get("status") or "").lower():
                            sub_meta = self.summary(path, "agy")
                            s["tailStatus"] = sub_meta.get("tailStatus")
                            if sub_meta.get("updatedAt", 0) > s["updatedAt"]:
                                s["updatedAt"] = sub_meta["updatedAt"]
                        continue
                    if s.get("automated"):
                        continue
                    meta = dict(self.summary(path, "agy"))
                    if not meta.get("lastPrompt"):
                        meta.pop("lastPrompt", None)
                    if meta.get("convAt") or meta.get("attentionAt"):
                        # Positive-whitelist conversation time wins: an empty `resume` bumping SQLite
                        # last_modified_time must not inflate activity.
                        meta["updatedAt"] = meta.get("convAt") or meta["attentionAt"]
                    else:
                        meta["updatedAt"] = max(s["updatedAt"], meta["updatedAt"])
                    s.update(meta, path=path)
                    if (
                        is_automated_or_subagent_text(s.get("firstPrompt", ""))
                        or is_automated_or_subagent_text(s.get("lastPrompt", ""))
                        or is_automated_or_subagent_text(s.get("sourceTitle", ""))
                        or is_automated_or_subagent_text(s.get("title", ""))
                    ):
                        s["automated"] = True
                        s["isTemp"] = True
                    if s.get("parentId") or s.get("isSubagent") or s.get("automated"):
                        st_meta = self._session_db_meta.setdefault("agy:" + sid, {})
                        for sk in ("parentId", "isSubagent", "automated"):
                            if s.get(sk):
                                st_meta[sk] = s[sk]
                except OSError:
                    continue

            index_path = self.path(".codex/session_index.jsonl")
            codex_index_sids = set()
            idx_sig = _file_sig(index_path)
            if idx_sig is not None:
                if self._codex_index_cache[0] == idx_sig:
                    idx_entries = self._codex_index_cache[1]
                else:
                    idx_entries = [
                        (row["id"], snippet(row.get("thread_name", ""), 200), stamp(row.get("updated_at")))
                        for row in json_rows(index_path)
                        if row.get("id")
                    ]
                    self._codex_index_cache = (idx_sig, idx_entries)
                for sid_i, title_i, up_i in idx_entries:
                    codex_index_sids.add(sid_i)
                    add("codex", sid_i, {"sourceTitle": title_i, "updatedAt": up_i})
            # Rollouts actually on disk, keyed by the thread id right after the `rollout-<timestamp>-` prefix.
            # archived_sessions/ only resolves rows whose recorded rollout_path moved; it never adds new rows.
            codex_session_files, codex_disk_paths = self._discover_codex_rollouts()
            codex_disk_paths_set = set(codex_session_files) | set(codex_disk_paths.values())
            codex_columns = ["id", "title", "cwd", "first_user_message", "updated_at", "created_at", "model", "reasoning_effort", "rollout_path", "agent_role", "source", "originator"]
            known_codex_paths = set()
            # Follow Codex's state DB schema bumps (state_5 -> state_6 ...): newest file that actually
            # has a `threads` table wins; otherwise fall back to older candidates.
            codex_thread_rows = []
            for codex_db in codex_state_db_candidates(self.path):
                codex_thread_rows = list(self.rows(codex_db, PROVIDER_SCHEMA_ADAPTERS["codex"]["threads_table"], codex_columns))
                if codex_thread_rows:
                    break
            # Rows whose recorded rollout `.jsonl` is gone, plus session_index-only orphans: dropped below
            # unless live or hibernated. A row without a rollout_path column carries no such evidence.
            codex_ghost_sids = set(codex_index_sids)
            for row in codex_thread_rows:
                rid = row["id"]
                raw_title = row.get("title") or ""
                raw_first = row.get("first_user_message") or ""
                csrc = row.get("source")
                codex_sig = (
                    row.get("updated_at"),
                    len(raw_title),
                    raw_title[:128],
                    len(raw_first),
                    raw_first[:128],
                    csrc,
                    row.get("originator"),
                    row.get("agent_role"),
                    rules_mtime,
                )
                cmemo = self._codex_row_memo.get(rid)
                if cmemo is not None and cmemo[0] == codex_sig:
                    parsed_title, parsed_first, row_is_auto, row_up_ts = cmemo[1]
                else:
                    parsed_title = snippet(raw_title, 200)
                    parsed_first = snippet(clean_prompt(raw_first, "codex"))
                    row_is_auto = bool(is_codex_automated(csrc, raw_title, raw_first, row.get("originator")) or row.get("agent_role"))
                    row_up_ts = stamp(row.get("updated_at"))
                    self._codex_row_memo[rid] = (codex_sig, (parsed_title, parsed_first, row_is_auto, row_up_ts))
                path = row.get("rollout_path")
                resolved_path = None
                if path:
                    if path in codex_disk_paths_set:
                        resolved_path = path
                    else:
                        alt_path = codex_disk_paths.get(rid)
                        if alt_path:
                            resolved_path = alt_path
                        elif not row_is_auto and os.path.isfile(path):
                            resolved_path = path
                if path and resolved_path is None and row_is_auto and rid not in codex_index_sids and ("codex:" + rid) not in state:
                    continue
                s = add("codex", rid)
                is_auto = bool(s.get("automated") or row_is_auto)
                s.update({"cwd": row.get("cwd") or s["cwd"], "updatedAt": max(s["updatedAt"], row_up_ts),
                    "sourceTitle": s.get("sourceTitle") or parsed_title,
                    "firstPrompt": parsed_first,
                    "model": row.get("model"), "reasoningEffort": row.get("reasoning_effort"),
                    "automated": is_auto})
                if resolved_path:
                    s["path"] = resolved_path
                    known_codex_paths.add(resolved_path)
                    codex_ghost_sids.discard(rid)
                elif path:
                    codex_ghost_sids.add(rid)
                else:
                    codex_ghost_sids.discard(rid)
            for path in codex_session_files:
                if path in known_codex_paths:
                    continue
                match = _ROLLOUT_UUID_RE.search(path)
                try:
                    meta = self.summary(path, "codex")
                    sid = meta.get("id") or (match.group(1) if match else None)
                    if sid:
                        existing = sessions.get("codex:" + sid)
                        if existing is not None and existing.get("path"):
                            prev_ts = float(existing.get("updatedAt") or 0.0)
                            new_ts = float(meta.get("updatedAt") or 0.0)
                            if new_ts > prev_ts:
                                meta_copy = dict(meta)
                                meta_copy["updatedAt"] = max(prev_ts, new_ts)
                                add("codex", sid, meta_copy)["path"] = path
                            else:
                                existing["updatedAt"] = max(prev_ts, new_ts)
                                if not existing.get("firstPrompt") and meta.get("firstPrompt"):
                                    existing["firstPrompt"] = meta["firstPrompt"]
                        else:
                            if existing is not None:
                                meta = dict(meta)
                                meta["updatedAt"] = max(float(existing.get("updatedAt") or 0.0), float(meta.get("updatedAt") or 0.0))
                            add("codex", sid, meta)["path"] = path
                except OSError:
                    continue
            for sid in codex_ghost_sids:
                s = sessions.get("codex:" + sid)
                if s is not None and not s.get("path") and codex_disk_paths.get(sid):
                    s["path"] = codex_disk_paths[sid]
            for s in sessions.values():
                if s["tool"] == "codex" and s.get("path") and not s.get("automated"):
                    try:
                        meta = dict(self.summary(s["path"], "codex"))
                        if meta.get("convAt") or meta.get("attentionAt"):
                            meta["updatedAt"] = meta.get("convAt") or meta["attentionAt"]
                        else:
                            meta["updatedAt"] = max(s["updatedAt"], meta["updatedAt"])
                        for key in ("firstPrompt", "lastPrompt"):
                            if not meta.get(key):
                                meta.pop(key, None)
                        s.update(meta)
                    except OSError:
                        s.pop("path", None)
                s["transcriptAt"] = s.get("updatedAt", 0)

            oc_adapter = PROVIDER_SCHEMA_ADAPTERS["opencode"]
            for row in opencode_session_rows(self.path(oc_adapter["db"])):
                # `opencode run` leaves no agent and is never viewed; a TUI tab has an agent even before
                # (or without ever) being marked viewed
                headless = oc_adapter["headless_unviewed"] and not row.get("time_viewed") and not row.get("agent")
                idle = row.get("last_type") == "idle" or bool(
                    row.get("time_idle") and row["time_idle"] >= row["time_updated"])
                updated = stamp(row["time_updated"])
                add("opencode", row["id"], {
                    "cwd": row.get("directory") or "", "updatedAt": updated, "transcriptAt": updated,
                    "createdAt": stamp(row["time_created"]), "hasRowTimestamp": True,
                    "viewedAt": stamp(row["time_viewed"]) if row.get("time_viewed") else 0.0,
                    "sourceTitle": snippet(row.get("title") or "", 200),
                    "firstPrompt": snippet(clean_prompt(row.get("first_user") or "", "opencode")),
                    "lastPrompt": snippet(clean_prompt(row.get("last_user") or "", "opencode")),
                    "messageCount": int(row.get("turns") or 0), "messageCountExact": True,
                    "model": row.get("model"), "parentId": row.get("parent_id") or None,
                    "automated": bool(headless or row.get("parent_id")),
                    "status": "idle" if idle else "working", "tailStatus": "done_idle" if idle else "ball_in_model",
                })

            active_locks = self._probe_active_lock_uuids(sessions)
            claimed_locks = {"codex": set(), "agy": set()}

            def interactive_agy_candidate(s):
                # Turn 1 can still have last_user_input_step_index == -1. Promotion
                # also requires a matching interactive process; a lock alone is insufficient.
                return bool(
                    (int(s.get("stepCount") or 0) > 0 or s.get("firstPrompt") or s.get("lastPrompt"))
                    and not s.get("parentId") and not s.get("isSubagent") and not s.get("agentName")
                    and not any(is_automated_or_subagent_text(s.get(f) or "")
                                for f in ("title", "sourceTitle", "firstPrompt", "lastPrompt"))
                )

            use_proc_cache = ("processes" not in self.__dict__ and "open_files" not in self.__dict__)
            now_mono = time.monotonic()
            proc_env_sig = (
                tuple(sorted(pid_states.keys())),
                tuple(sorted(active_locks["codex"].keys())),
                tuple(sorted(active_locks["agy"].keys())),
                _file_sig(os.path.join(self.root, "data", "last_exit")),
            )
            real_proc_ts = float(getattr(self, "_proc_real_scanned", 0.0) or 0.0)
            cache_fresh = bool(
                use_proc_cache
                and (self._proc_cache or real_proc_ts)
                and (
                    (self._proc_scanned and (now_mono - self._proc_scanned < 15.0))
                    or (
                        real_proc_ts
                        and (now_mono - real_proc_ts < 60.0)
                        and getattr(self, "_last_proc_env_sig", None) == proc_env_sig
                        and self._cached_pids_alive(self._proc_cache)
                    )
                )
            )
            bg = self._bg_proc_scan
            if bg is not None and use_proc_cache and real_proc_ts:
                # Background scanner owns `ps`/`lsof`: serve the last snapshot, ask for a rescan off-thread.
                if not cache_fresh:
                    bg["env_sig"] = proc_env_sig
                    bg["event"].set()
                processes, files, resident_ttys = self._proc_snapshot
                self._proc_scanned = real_proc_ts
            elif cache_fresh:
                processes = self._proc_cache
                files = self._files_cache
                resident_ttys = getattr(self, "_resident_ttys_cache", set())
                self._proc_scanned = real_proc_ts or now_mono
            elif bg is not None and use_proc_cache:
                with bg["lock"]:
                    processes, files, resident_ttys = self._scan_processes(True, now_mono, proc_env_sig)
            else:
                processes, files, resident_ttys = self._scan_processes(use_proc_cache, now_mono, proc_env_sig)
            # Two-pass process->session resolution (Opus 5.5 High verified):
            # Pass 1 collects authoritative claims per PID:
            #   Priority 1: exclusive process state / lock (`pid_states` for claude, `/presence/<uuid>.lock` for agy,
            #               `/thread-writer-locks/<uuid>.lock` for codex), preferring non-child (`parentId == ""`) over subagents.
            #   Priority 2: explicit CLI UUID argument (`--resume <uuid>`, `--conversation <uuid>`, `resume <uuid>`).
            # Pass 2 resolves remaining processes from general open-file paths (`/brain/`, `/conversations/`, `rollout-`,
            # `/projects/`), excluding any session ID already authoritatively claimed by another process in Pass 1 so
            # cross-session artifact/transcript reads never hijack a process's session binding.
            auth_by_pid = {}
            authoritative_claimed = {t: set() for t in TOOLS}
            for p in processes:
                pid, tool = p["pid"], p["tool"]
                opened = files.get(pid, {})
                info = pid_states.get(pid, {}) if tool == "claude" else {}
                lock_sid = info.get("sessionId")
                if not lock_sid and tool in _OPEN_LOCK_PATTERNS:
                    lock_rx = _OPEN_LOCK_PATTERNS[tool]
                    lock_matches = []
                    for path in opened.get("paths", []):
                        m_lock = lock_rx.search(path)
                        if m_lock and m_lock.group(1) not in lock_matches:
                            lock_matches.append(m_lock.group(1))
                    if lock_matches:
                        lock_matches.sort(
                            key=lambda x: (
                                bool(sessions.get(tool + ":" + x, {}).get("parentId")),
                                -sessions.get(tool + ":" + x, {}).get("updatedAt", 0),
                            )
                        )
                        lock_sid = lock_matches[0]
                cli_sid = None
                args = p["args"]
                for option in ("--resume", "--conversation", "resume"):
                    if option in args and args.index(option) + 1 < len(args):
                        candidate = args[args.index(option) + 1]
                        if _UUID_RE.fullmatch(candidate):
                            cli_sid = candidate
                            break
                        elif tool == "claude":
                            cli_sid = next((k for k, v in labels.items() if v == candidate), None)
                            if cli_sid:
                                break
                if tool == "opencode":
                    for option in ("-s", "--session"):
                        if option in args and args.index(option) + 1 < len(args):
                            cli_sid = args[args.index(option) + 1]
                auth_sid = lock_sid or cli_sid
                if auth_sid:
                    auth_by_pid[pid] = auth_sid
                    authoritative_claimed.setdefault(tool, set()).add(auth_sid)
                    if tool in claimed_locks:
                        claimed_locks[tool].add(auth_sid)

            for p in processes:
                pid, tool = p["pid"], p["tool"]
                opened = files.get(pid, {})
                info = pid_states.get(pid, {}) if tool == "claude" else {}
                sid = auth_by_pid.get(pid)
                if sid and tool == "opencode":
                    # `opencode -s X` only says which tab the TUI opened on: a tab used since then wins
                    later = self._opencode_session_for_process(p, opened.get("cwd") or "", sessions,
                                                               authoritative_claimed.setdefault(tool, set()))
                    cur = sessions.get("opencode:" + sid) or {}
                    if later and self._opencode_touched(sessions.get("opencode:" + later) or {}) > self._opencode_touched(cur):
                        authoritative_claimed[tool].discard(sid)
                        authoritative_claimed[tool].add(later)
                        sid = later
                candidates = opened.get("_matched_" + tool)
                if candidates is None:
                    candidates = []
                    path_rx = _OPEN_PATH_PATTERNS[tool]
                    for path in opened.get("paths", []):
                        match = path_rx.search(path)
                        if match and match.group(1) not in candidates:
                            candidates.append(match.group(1))
                    if isinstance(opened, dict) and "paths" in opened:
                        opened["_matched_" + tool] = list(candidates)
                else:
                    candidates = list(candidates)
                if not sid and candidates:
                    auth_set = authoritative_claimed.get(tool, set()) | claimed_locks.get(tool, set())
                    unclaimed_cands = [c for c in candidates if c not in auth_set]
                    pool_cands = unclaimed_cands if unclaimed_cands else candidates
                    pool_cands.sort(key=lambda x: (bool(sessions.get(tool + ":" + x, {}).get("parentId")), -sessions.get(tool + ":" + x, {}).get("updatedAt", 0)))
                    sid = pool_cands[0]
                    authoritative_claimed.setdefault(tool, set()).add(sid)
                if not sid and tool == "opencode":
                    sid = self._opencode_session_for_process(p, opened.get("cwd") or "", sessions,
                                                             authoritative_claimed.setdefault(tool, set()))
                    if sid:
                        authoritative_claimed[tool].add(sid)
                if sid and tool in claimed_locks:
                    claimed_locks[tool].add(sid)
                if not sid and tool in active_locks and active_locks[tool]:
                    lock_cands = [
                        uid for uid in active_locks[tool]
                        if (tool + ":" + uid) in sessions
                        and uid not in claimed_locks[tool]
                        and (not sessions[tool + ":" + uid].get("automated")
                             or (tool == "agy" and interactive_agy_candidate(sessions[tool + ":" + uid])))
                    ]
                    if lock_cands:
                        lock_cands.sort(
                            key=lambda x: (
                                bool(sessions[tool + ":" + x].get("parentId")),
                                -active_locks[tool][x],
                                -sessions[tool + ":" + x].get("updatedAt", 0),
                            )
                        )
                        sid = lock_cands[0]
                        claimed_locks[tool].add(sid)
                synthetic = not sid
                sid = sid or "live-" + tool + "-" + str(pid)
                s = add(tool, sid)
                if tool == "agy" and not synthetic and interactive_agy_candidate(s):
                    s["automated"] = False
                    s["isTemp"] = False
                s.update(isLive=True, synthetic=synthetic)
                s["cwd"] = info.get("cwd") or opened.get("cwd") or s["cwd"]
                s["status"] = info.get("status") or s.get("status", "unknown")
                if info.get("_parkedDone"):
                    s["parkedDone"] = True
                if tool == "claude" and info:
                    lock_ts = max(float(info.get("_mtime") or 0), stamp(info.get("updatedAt")), stamp(info.get("startedAt")))
                    s["lockMtime"] = lock_ts
                    if info.get("version"):
                        s["cliVersion"] = str(info["version"])
                    if lock_ts > 0 and not s.get("updatedAt"):
                        s["updatedAt"] = lock_ts
                elif tool in active_locks and sid in active_locks[tool]:
                    s["lockMtime"] = active_locks[tool][sid]
                    if not s.get("hasRowTimestamp"):
                        s["updatedAt"] = max(s.get("updatedAt", 0), active_locks[tool][sid])
                proc = {k: p[k] for k in ("pid", "ppid", "tty", "rss_mb", "etime")}
                for id_k in ("boot_id", "start_time"):
                    if p.get(id_k):
                        proc[id_k] = p[id_k]
                if p.get("wrapper_ttys"):
                    proc["wrapper_ttys"] = list(p["wrapper_ttys"])
                proc["status"] = info.get("status") or s.get("agyStatus") or s.get("status", "unknown")
                proc["resident"] = bool(
                    p.get("resident")
                    or p["tty"] in resident_ttys
                    or any(wt in resident_ttys for wt in p.get("wrapper_ttys", ()))
                )
                if proc["resident"]:
                    s["resident"] = True
                if tool == "opencode":
                    # One OpenCode TUI holds several tabs: the session on screen lives in whichever MixHub window
                    # the TUI was opened in, whose name follows the tab it started on
                    wname = (getattr(self, "_hubpool_tty_windows", None) or {}).get(p["tty"], "")
                    if wname.startswith("w-opencode-"):
                        s["hubpoolWindow"] = wname
                s["processes"].append(proc)
                s.commit()
                if len(s["processes"]) == 1:
                    s.update(proc)
                if synthetic:
                    s["sourceTitle"] = tool + " · " + t("session.pick_waiting")
                    s["updatedAt"] = time.time()

            proc_table = getattr(self, "_last_proc_table", None) or {}
            subagent_pids = dict(getattr(self, "_last_subagent_pids", None) or {})
            for sub_pid in list(subagent_pids):
                pnode = proc_table.get(sub_pid)
                if pnode and pnode.args:
                    for m_uid in re.finditer(UUID, pnode.args):
                        cand_uid = m_uid.group(0)
                        for t_prefix in ("claude:", "codex:", "agy:"):
                            s_cand = sessions.get(t_prefix + cand_uid)
                            if s_cand is not None and not s_cand.get("processes"):
                                s_cand["isSubagent"] = True
                                s_cand["automated"] = True
                                s_cand["hidden_reason"] = "subagent_ppid"
                                s_cand["isLive"] = False

            for tool in ("codex", "agy"):
                for uid, lock_mtime in active_locks[tool].items():
                    if (tool + ":" + uid) in sessions:
                        s = sessions[tool + ":" + uid]
                        if not s.get("automated") and not s.get("parentId"):
                            s["isLive"] = True
                            s["lockMtime"] = lock_mtime
                            if not s.get("hasRowTimestamp"):
                                s["updatedAt"] = max(s.get("updatedAt", 0), lock_mtime)

            ghosts = []
            for sid in codex_ghost_sids:
                s = sessions.get("codex:" + sid)
                stored = state.get("codex:" + sid, {})
                if (s is not None and not s.get("path") and not s.get("isLive") and sid not in active_locks["codex"]
                        and not stored.get("hibernatedAt") and not stored.get("hibernatedByHub")):
                    ghosts.append(sid)
            live_windows = self._live_tmux_window_names() if ghosts else set()
            for sid in ghosts:
                if live_windows & {"w-codex-" + sid, "w-codex-" + sid[:12], "w-codex-" + sid[:8],
                                   "hub-codex-" + sid, "hub-codex-" + sid[:12]}:
                    sessions["codex:" + sid]["isLive"] = True
                else:
                    sessions.pop("codex:" + sid)

            if use_proc_cache:
                for pid, info in pid_states.items():
                    sid = info.get("sessionId")
                    if sid and ("claude:" + sid) in sessions:
                        if pid in subagent_pids or (proc_table and has_agent_ancestor(pid, proc_table) == "subagent_ppid"):
                            s_sub = sessions["claude:" + sid]
                            s_sub["isSubagent"] = True
                            s_sub["automated"] = True
                            s_sub["hidden_reason"] = "subagent_ppid"
                            s_sub["isLive"] = False
                            s_sub["processes"] = []
                            continue
                        try:
                            os.kill(pid, 0)
                        except OSError:
                            continue
                        s = sessions["claude:" + sid]
                        if s.get("consultant") or s.get("automated") or s.get("parentId") or is_temp_session(s):
                            continue
                        s["isLive"] = True
                        lock_ts = max(float(info.get("_mtime") or 0), stamp(info.get("updatedAt")), stamp(info.get("startedAt")))
                        s["lockMtime"] = lock_ts
                        if info.get("version"):
                            s["cliVersion"] = str(info["version"])
                        if lock_ts > 0 and not s.get("updatedAt"):
                            s["updatedAt"] = lock_ts
                        if not any(pr.get("pid") == pid for pr in s["processes"]):
                            res_flag = bool(str(info.get("tmux") or "").startswith("pool:"))
                            proc = {"pid": pid, "ppid": 0, "tty": "??", "rss_mb": 0.0, "etime": "00:00",
                                    "status": info.get("status", "unknown"), "resident": res_flag}
                            pnode = proc_table.get(pid)
                            if pnode and pnode.start_time:
                                proc["start_time"] = pnode.start_time
                                b_id = get_boot_id()
                                if b_id:
                                    proc["boot_id"] = b_id
                            if res_flag:
                                s["resident"] = True
                            s["processes"].append(proc)
                            if len(s["processes"]) == 1:
                                s.update(proc)

            for sid in sdk_cli_sids:
                if ("claude:" + sid) in sessions:
                    sessions["claude:" + sid]["automated"] = True
                    sessions["claude:" + sid]["consultant"] = True
                    sessions["claude:" + sid]["isLive"] = False

            for key, stored in state.items():
                tool, _, sid = key.partition(":")
                if tool in TOOLS and sid and key not in sessions and stored.get("hibernatedAt"):
                    add(tool, sid, {"cwd": stored.get("cwd", ""), "synthetic": sid.startswith("live-"), "updatedAt": stored["hibernatedAt"]})
            try:
                labels_mtime = os.stat(self.path(".claude/session_labels.json")).st_mtime
            except OSError:
                labels_mtime = 0.0
            default_hostname = clean_hostname(None)
            # A probe older than its 5 s shelf life is redone here, before use: the web refreshes every 30 s, so the
            # background pass from its previous refresh is always stale and an on-screen approval read as "working"
            if (
                use_proc_cache
                and not self._pane_probe_running
                and now_mono - self._pane_probe_done_at > 4.0
                and self._hubpool_reachable()
                and type(subprocess.run).__name__ not in ("MagicMock", "AsyncMock", "NonCallableMagicMock")
            ):
                self._pane_probe_last_sched = self._pane_probe_done_at = now_mono
                try:
                    self.refresh_pane_probes_sync(sessions)
                except Exception:
                    pass
            for key, s in sessions.items():
                stored = state.get(key, {})
                s["pinned"] = bool(stored.get("pinned"))
                s["hibernatedByHub"] = bool(stored.get("hibernatedByHub") and not s.get("processes"))
                s["title"] = snippet(self.resolve_session_title(s, stored, labels, labels_mtime), 200)
                s["cwd"] = s["cwd"] or stored.get("cwd") or self.home
                s["shortCwd"] = "~" + s["cwd"][len(self.home):] if s["cwd"] == self.home or s["cwd"].startswith(self.home + "/") else s["cwd"]
                s["isSubagent"] = bool(s.get("parentId") or s.get("isSubagent") or s.get("agentName"))
                s["isTemp"] = is_temp_session(s)
                if s.get("consultant") or s.get("automated"):
                    s["isLive"] = False
                    s["processes"] = []
                s["hibernatedAt"] = stored.get("hibernatedAt")
                if stored.get("handoffChain"):
                    s["handoffChain"] = list(stored["handoffChain"])
                    s["relayTools"] = list(stored["handoffChain"])
                elif stored.get("relayTools"):
                    s["handoffChain"] = list(stored["relayTools"])
                    s["relayTools"] = list(stored["relayTools"])
                if stored.get("taskId"):
                    s["taskId"] = stored["taskId"]
                if stored.get("handoffTaskId"):
                    s["handoffTaskId"] = stored["handoffTaskId"]
                if stored.get("handoffFrom"):
                    s["handoffFrom"] = stored["handoffFrom"]
                if stored.get("handoffTo"):
                    s["handoffTo"] = stored["handoffTo"]
                if stored.get("handoffMode"):
                    s["handoffMode"] = stored["handoffMode"]
                if stored.get("handoffProvider"):
                    s["handoffProvider"] = stored["handoffProvider"]
                if stored.get("handoffBundlePath"):
                    s["handoffBundlePath"] = stored["handoffBundlePath"]
                if stored.get("handoffYamlPath"):
                    s["handoffYamlPath"] = stored["handoffYamlPath"]
                s["controlMode"] = stored.get("controlMode") or "HUB_MANAGED"
                s["hasTranscript"] = bool(s.get("path"))
                s["rss_mb"] = round(sum(p["rss_mb"] for p in s["processes"]), 1)
                s["resumeCommand"] = resume_command(s)
                raw_host = s.get("host")
                if raw_host and raw_host != "local":
                    ch = clean_hostname(raw_host)
                    s["host"] = ch
                    s["hostname"] = ch
                else:
                    s["host"] = "local"
                    s["hostname"] = default_hostname
                s["activeSubagents"] = 0
                raw_status = str(s.get("agyStatus") or s.get("status") or "").lower()
                has_procs = bool(s.get("processes"))
                tstat = s.get("tailStatus") or ""
                if not s.get("isLive"):
                    s["agentState"] = "dormant"
                    if tstat in ("ball_in_model", "unclosed_tool"):
                        s["interrupted"] = True
                else:
                    tx_ts = s.get("transcriptAt") or s.get("updatedAt", 0)
                    age = max(0.0, time.time() - tx_ts)
                    not_idle = bool(s.get("notFullyIdle") and not s.get("killed") and age < 1800)
                    if raw_status in ("working", "running", "busy") or ("running" in raw_status and age < 1800) or not_idle:
                        s["agentState"] = "working"
                    elif s.get("parkedDone"):
                        s["agentState"] = "done_idle"
                    elif not has_procs:
                        s["agentState"] = "done_idle"
                        if tstat in ("ball_in_model", "unclosed_tool"):
                            s["interrupted"] = True
                    elif tstat == "wait_input":
                        s["agentState"] = "wait_input" if s.get("tailStructuredWait") else "done_idle"
                    elif tstat == "unclosed_tool":
                        unclosed_limit = 900 if s["tool"] == "agy" and "idle" not in raw_status else 25
                        s["agentState"] = "working" if age < unclosed_limit else "wait_input"
                    elif tstat == "ball_in_model":
                        if any(p.get("resident") for p in s.get("processes", [])) or age < 300:
                            s["agentState"] = "working"
                        elif not s.get("hasRowTimestamp") and s.get("lockMtime"):
                            s["agentState"] = "wait_input"
                        else:
                            s["agentState"] = "done_idle"
                            s["interrupted"] = True
                    elif tstat == "done_idle":
                        s["agentState"] = "working" if age < 8 else "done_idle"
                    else:
                        s["agentState"] = "working" if age < 15 else "done_idle"
                    self._apply_pane_probe_state(key, s, age, now_mono)
            now_ts = time.time()
            _state_prio = {"dormant": 0, "done_idle": 1, "working": 2, "wait": 3, "wait_input": 3}
            for cs in list(sessions.values()):
                parent_id = cs.get("parentId")
                if not parent_id:
                    continue
                ps = (
                    sessions.get(cs["tool"] + ":" + parent_id)
                    or sessions.get("agy:" + parent_id)
                    or sessions.get("claude:" + parent_id)
                    or sessions.get("codex:" + parent_id)
                )
                if not ps:
                    continue
                cs_ts = cs.get("transcriptAt") or cs.get("updatedAt") or 0
                if cs_ts > ps.get("updatedAt", 0):
                    ps["updatedAt"] = cs_ts
                if cs_ts > ps.get("transcriptAt", 0):
                    ps["transcriptAt"] = cs_ts
                cs_status = str(cs.get("agyStatus") or cs.get("status") or "").lower()
                cs_age = max(0.0, now_ts - cs_ts) if cs_ts else 999999.0
                cs_not_idle = bool(cs.get("notFullyIdle") and not cs.get("killed") and cs_age < 1800.0)
                cs_tail_busy = cs.get("tailStatus") in ("unclosed_tool", "ball_in_model") and "idle" not in cs_status and cs_age < 1800.0
                cs_is_wait = cs.get("agentState") in ("wait_input", "wait") or (
                    cs.get("tailStatus") == "wait_input" and cs.get("tailStructuredWait") and "idle" not in cs_status and cs_age < 1800.0
                )
                if cs_is_wait or (
                    cs.get("agentState") == "working"
                    or "running" in cs_status
                    or cs_status in ("working", "busy")
                    or cs_not_idle
                    or cs_tail_busy
                    or (cs_age < 45.0 and "idle" not in cs_status)
                ):
                    ps["isLive"] = True
                    cand_state = "wait_input" if cs_is_wait else "working"
                    cur_ps_state = str(ps.get("agentState") or "dormant")
                    if _state_prio.get(cand_state, 0) > _state_prio.get(cur_ps_state, 0):
                        ps["agentState"] = cand_state
                    ps.pop("interrupted", None)
                    ps["activeSubagents"] = int(ps.get("activeSubagents") or 0) + 1
            if (
                use_proc_cache
                and self._hubpool_reachable()
                and type(subprocess.run).__name__ not in ("MagicMock", "AsyncMock", "NonCallableMagicMock")
            ):
                self._maybe_schedule_pane_probes(sessions)
            wset = self.working_set()
            wset_dirty = False
            last_exit_dir = os.path.join(self.root, "data", "last_exit")
            open_windows_box = []

            def expiry_open_windows():
                # A still-open hubpool window keeps its entry even when process matching missed it
                if not open_windows_box:
                    open_windows_box.append(self._live_tmux_window_names() if "processes" not in self.__dict__ else set())
                return open_windows_box[0]

            wset_removed = []
            for key, s in sessions.items():
                if (
                    s.get("isTemp")
                    or s.get("synthetic")
                    or s.get("consultant")
                    or s.get("automated")
                    or s.get("parentId")
                    or s.get("isSubagent")
                ):
                    s["inWorkingSet"] = False
                    if (
                        s.get("isTemp")
                        or s.get("automated")
                        or s.get("isSubagent")
                        or s.get("parentId")
                        or s.get("consultant")
                    ) and (key in wset or s["id"] in wset):
                        wset.discard(key)
                        wset.discard(s["id"])
                        wset_dirty = True
                        wset_removed.append((key, "not a user session"))
                    continue
                # A stale hub-hibernate flag must not hide a later /exit: `sess_ts` below already includes `hibernatedAt`
                if not s.get("processes") and int(s.get("activeSubagents") or 0) == 0:
                    exit_file = os.path.join(last_exit_dir, f"{s['id']}.json")
                    if os.path.isfile(exit_file):
                        exit_info = read_json(exit_file, {})
                        exit_ts = float(exit_info.get("ts") or 0.0) if isinstance(exit_info, dict) else 0.0
                        sess_ts = max(float(s.get("updatedAt") or 0.0), float(s.get("hibernatedAt") or 0.0))
                        try:
                            exit_rc = int(exit_info.get("rc", 0))
                        except (TypeError, ValueError, AttributeError):
                            exit_rc = 0
                        signal_exit = exit_rc < 0 or exit_rc >= 128 or bool(
                            isinstance(exit_info, dict) and exit_info.get("signal_exit")
                        )
                        if not signal_exit and exit_ts > 0.0 and exit_ts >= sess_ts - 5.0:
                            s["isLive"] = False
                            s["inWorkingSet"] = False
                            s["agentState"] = "dormant"
                            if key in wset or s["id"] in wset:
                                wset.discard(key)
                                wset.discard(s["id"])
                                wset_dirty = True
                                wset_removed.append((key, f"exited rc={exit_rc}"))
                            continue
                if s.get("isLive"):
                    s["inWorkingSet"] = True
                    if key not in wset:
                        wset.add(key)
                        wset_dirty = True
                elif (key in wset or s["id"] in wset) and not s.get("resident") and (
                    now_ts - max(float(s.get("updatedAt") or 0.0), float(s.get("hibernatedAt") or 0.0))
                    > WORKING_SET_IDLE_EXPIRE_SEC
                ) and f"w-{s['tool']}-{s['id'][:12]}" not in expiry_open_windows():
                    # Asleep (or its terminal closed) and untouched for a day: it was finished, not paused
                    s["inWorkingSet"] = False
                    wset.discard(key)
                    wset.discard(s["id"])
                    wset_dirty = True
                    wset_removed.append((key, "idle for a day"))
                elif key in wset or s["id"] in wset:
                    s["inWorkingSet"] = True
                    s["isLive"] = True
                    if s.get("agentState") == "dormant" or not s.get("agentState"):
                        s["agentState"] = "done_idle"
                    if not s.get("processes") and s.get("tailStatus") in ("ball_in_model", "unclosed_tool") and s.get("agentState") != "working":
                        s["agentState"] = "done_idle"
                        s["interrupted"] = True
                else:
                    s["inWorkingSet"] = False
            if wset_dirty:
                self._save_working_set(wset)
            if wset_removed:
                from mixhub.tui.diag import log_tui_diag

                for removed_key, why in wset_removed:
                    log_tui_diag("working_set_removed", session=removed_key, reason=why)
            active_paths = {s.get("path") for s in sessions.values()} | set(codex_session_files)
            self.cache = {p: v for p, v in self.cache.items() if p in active_paths}
            self._save_disk_cache()
            self.sessions = sessions
            self.scanned = time.monotonic()
            return sessions


def discover(hub: Any, **kwargs: Any) -> Any:
    """Thin forwarding wrapper around `Hub.discover`."""
    return hub.discover(**kwargs)
