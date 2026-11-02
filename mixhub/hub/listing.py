"""Read API: title resolution, public session records, filtered listings, lightweight meta and id lookup."""
from __future__ import annotations

import glob
import json
import os
import sqlite3
import urllib.parse

from mixhub.hub.constants import TOOLS
from mixhub.hub.schema import codex_state_db_candidates, PROVIDER_SCHEMA_ADAPTERS
from mixhub.hub.session_filters import _load_ui_prefs
from mixhub.hub.text import clean_prompt, snippet
from mixhub.hub.titles import _is_generic_placeholder_title
from mixhub.hub.transcript import json_rows
from mixhub.hub.util import clean_hostname, read_json, stamp


class ListingMixin:
    """Session listing, lookup and title resolution."""

    def resolve_session_title(self, s, stored=None, labels=None, labels_mtime=0.0):
        """Latest explicit title wins across Hub rename, session_labels, native Claude records and sidecars."""
        if s.get("tool") != "claude" and not stored:
            for c in (s.get("aiTitle"), s.get("nativeSummary"), s.get("sourceTitle")):
                if c and not _is_generic_placeholder_title(c):
                    return c
            sub_p = s.get("substantivePrompt") or ""
            if not sub_p:
                for p_cand in (s.get("firstPrompt") or "", s.get("lastPrompt") or ""):
                    if p_cand and not _is_generic_placeholder_title(p_cand) and len(p_cand.strip()) >= 4:
                        sub_p = p_cand
                        break
            return (
                snippet(sub_p, 60)
                or s.get("sourceTitle")
                or snippet(s.get("firstPrompt"), 60)
                or s.get("snapshotSummary")
                or s["tool"] + " · " + s["id"][:8]
            )
        stored = stored or {}
        labels = labels if isinstance(labels, dict) else {}
        is_claude = s.get("tool") == "claude"
        tx_mtime = 0.0
        if is_claude and s.get("path") and os.path.isfile(s["path"]):
            try:
                tx_mtime = os.stat(s["path"]).st_mtime
            except OSError:
                pass
        # Claude custom title: transcript tail vs `<project_dir>/<sid>/custom-title.json` sidecar.
        sc_title = s.get("sidecarCustomTitle")
        sc_mtime = float(s.get("sidecarMtime") or 0.0)
        if sc_title and (sc_mtime > tx_mtime or not s.get("customTitleFromTail")):
            resolved_custom = sc_title
        else:
            resolved_custom = s.get("customTitleFromTail") or sc_title or s.get("customTitle")
        agent_title = s.get("claudeAgentName")
        if agent_title and agent_title == s.get("slug"):
            agent_title = None  # Auto-derived slug, not a user-chosen name.
        u_mtime = float(s.get("userNameMtime") or 0.0)
        if s.get("userName"):
            if u_mtime >= max(tx_mtime, sc_mtime) or not (agent_title or resolved_custom):
                native_explicit = s["userName"]
            else:
                native_explicit = agent_title or resolved_custom or s["userName"]
        else:
            native_explicit = agent_title or resolved_custom
        if not is_claude and not native_explicit:
            src_t = s.get("customTitleFromTail") or s.get("customTitle") or s.get("sourceTitle")
            if src_t and not _is_generic_placeholder_title(src_t):
                native_explicit = src_t
        label = labels.get(s.get("id")) if is_claude else None
        if label:
            native_explicit_mtime = max(tx_mtime, sc_mtime, u_mtime)
            explicit_after_labels = native_explicit if native_explicit and native_explicit_mtime > labels_mtime + 0.001 else label
        else:
            explicit_after_labels = native_explicit
        if stored.get("title"):
            # A Hub rename holds until native gets a title it has never shown (not Claude re-appending an old one).
            seen = stored.get("titleNativeSeen")
            if isinstance(seen, list) and native_explicit and native_explicit != stored["title"] and native_explicit not in seen:
                explicit_title = native_explicit
            else:
                explicit_title = stored["title"]
        else:
            explicit_title = explicit_after_labels
        if explicit_title:
            return explicit_title
        native_candidates = (
            s.get("aiTitle"),
            s.get("nativeSummary"),
            s.get("indexCustomTitle"),
            s.get("indexAiTitle"),
            s.get("indexSummary"),
            s.get("sourceTitle"),
        )
        chosen = next((c for c in native_candidates if c and not _is_generic_placeholder_title(c)), None)
        if chosen:
            return chosen
        sub_p = s.get("substantivePrompt") or ""
        if not sub_p:
            for p_cand in (s.get("firstPrompt") or "", s.get("lastPrompt") or ""):
                if p_cand and not _is_generic_placeholder_title(p_cand) and len(p_cand.strip()) >= 4:
                    sub_p = p_cand
                    break
        # snapshotSummary is an arbitrary mid-session line: only orphan snapshot rows fall back to it.
        return (
            snippet(sub_p, 60)
            or next((c for c in native_candidates if c), None)
            or snippet(s.get("firstPrompt"), 60)
            or s.get("snapshotSummary")
            or s["tool"] + " · " + s["id"][:8]
        )

    @staticmethod
    def public(session):
        return {k: v for k, v in session.items() if k not in (
            "path", "customTitle", "customTitleFromTail", "sidecarCustomTitle", "sidecarMtime", "claudeAgentName",
            "indexCustomTitle", "indexAiTitle", "indexSummary", "aiTitle", "sourceTitle", "userName", "userNameMtime",
            "snapshotSummary", "nativeSummary", "hasRowTimestamp", "substantivePrompt", "_titleScanIno", "_titleScanEnd")}

    def listing(self, query):
        sessions = self.discover()
        counts = dict.fromkeys([*TOOLS, "total", "live", "working", "wait", "wait_input", "done_idle", "pinned", "liveProcesses", "liveMemoryMB"], 0)
        tool, search = query.get("tool", ["all"])[0], query.get("q", [""])[0].casefold()
        ordered = []
        for key, s in sessions.items():
            if query.get("show_temp", ["0"])[0] != "1":
                if (
                    s.get("synthetic")
                    or (
                        (
                            s.get("isTemp")
                            or s.get("parentId")
                            or s.get("isSubagent")
                            or s.get("automated")
                            or s.get("consultant")
                        )
                        and not s.get("pinned")
                    )
                ):
                    continue
            counts[s["tool"]] += 1
            counts["total"] += 1
            counts["pinned"] += bool(s["pinned"])
            if s["isLive"]:
                counts["live"] += 1
                st = s.get("agentState", "done_idle")
                if st == "working":
                    counts["working"] += 1
                elif st == "wait_input":
                    counts["wait_input"] += 1
                    counts["wait"] += 1
                else:
                    counts["done_idle"] += 1
                counts["liveProcesses"] += len(s["processes"])
                counts["liveMemoryMB"] += s["rss_mb"]
            if (tool == "live" and not s["isLive"]) or (tool in TOOLS and s["tool"] != tool):
                continue
            if search and search not in " ".join(str(s.get(k, "")) for k in ("title", "lastPrompt", "firstPrompt", "cwd", "id", "slug")).casefold():
                continue
            ordered.append((not s["pinned"], not s["isLive"], bool(s.get("synthetic")), -int(s.get("updatedAt") or 0), s.get("id", ""), key))
        ordered.sort()
        counts["liveMemoryMB"] = round(counts["liveMemoryMB"], 1)
        return {"sessions": (self.public(sessions.get(row[-1])) for row in ordered), "counts": counts, "visible": len(ordered), "hostname": clean_hostname(), "host": str(_load_ui_prefs().get("host_label") or clean_hostname()), "warnings": self.warnings}

    def get_lightweight_session_meta(self, tool: str, sid: str) -> dict | None:
        """Resolve one native session (including an unambiguous prefix), without discovery."""
        tool, sid = (tool or "").strip().lower(), (sid or "").strip()
        if not sid:
            return None
        if not tool:
            for candidate in ("claude", "agy", "codex"):
                meta = self.get_lightweight_session_meta(candidate, sid)
                if meta is not None:
                    return meta
            return None

        state = self.state().get("sessions", {})
        stored = state.get(f"{tool}:{sid}", {})
        s = {"id": sid, "tool": tool, "badge": TOOLS.get(tool, tool),
             "cwd": "", "updatedAt": 0, "messageCount": 0, "messageCountExact": False,
             "isLive": True, "processes": [], "lastPrompt": "", "firstPrompt": "",
             "pinned": bool(stored.get("pinned"))}
        found = False
        labels, labels_mtime = {}, 0.0

        def native_row(db_path, table, id_col, wanted):
            if not os.path.isfile(db_path):
                return None
            connection = None
            try:
                connection = sqlite3.connect("file:" + urllib.parse.quote(db_path) + "?mode=ro", uri=True, timeout=0.5)
                connection.execute("PRAGMA cache_size=-256")
                columns = {r[1] for r in connection.execute("PRAGMA table_info(" + table + ")")}
                if id_col not in columns:
                    return None
                selected = [c for c in wanted if c in columns]
                sql = "SELECT " + ",".join('"' + c + '"' for c in selected) + " FROM " + table
                rows = connection.execute(sql + ' WHERE "' + id_col + '" = ?', (sid,)).fetchall()
                if not rows:
                    # Escape LIKE metacharacters: a session ID prefix is literal text.
                    prefix = sid.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                    rows = connection.execute(sql + ' WHERE "' + id_col + '" LIKE ? ESCAPE \'\\\' LIMIT 2', (prefix + "%",)).fetchall()
                return dict(zip(selected, rows[0])) if len(rows) == 1 else None
            except sqlite3.Error:
                return None
            finally:
                if connection is not None:
                    connection.close()

        if tool == "agy":
            adapter = PROVIDER_SCHEMA_ADAPTERS["agy"]
            row = native_row(self.path(adapter["summary_db"]), adapter["summary_table"], "conversation_id",
                             ["conversation_id", "title", "preview", "workspace_uris", "last_modified_time",
                              "step_count", "status", "parent_conversation_id", "agent_name"])
            if row:
                found = True
                sid = s["id"] = row["conversation_id"]
                try:
                    workspaces = json.loads(row.get("workspace_uris") or "[]")
                    cwd = urllib.parse.unquote(urllib.parse.urlparse(workspaces[0]).path) if workspaces else ""
                except (ValueError, TypeError, IndexError, KeyError):
                    cwd = ""
                s.update(sourceTitle=snippet(row.get("title") or "", 200),
                         lastPrompt=snippet(clean_prompt(row.get("preview") or "", "agy")), cwd=cwd,
                         updatedAt=stamp(row.get("last_modified_time")), stepCount=row.get("step_count", 0),
                         status=row.get("status"), agyStatus=row.get("status"),
                         parentId=row.get("parent_conversation_id") or "", agentName=row.get("agent_name") or "")
                s["isSubagent"] = bool(s["parentId"] or s["agentName"])
            tpath = self.path(f".gemini/antigravity-cli/brain/{sid}/.system_generated/logs/transcript.jsonl")
            if os.path.isfile(tpath):
                found = True
                s["path"] = tpath
                if not s.get("sourceTitle") or not s.get("firstPrompt"):
                    s.update(self.summary(tpath, "agy"))
        elif tool == "codex":
            for db_path in codex_state_db_candidates(self.path):
                row = native_row(db_path, "threads", "id",
                                 ["id", "title", "cwd", "first_user_message", "updated_at", "rollout_path",
                                  "model", "reasoning_effort", "agent_role", "source"])
                if row:
                    found = True
                    sid = s["id"] = row["id"]
                    s.update(sourceTitle=snippet(row.get("title") or "", 200),
                             firstPrompt=snippet(clean_prompt(row.get("first_user_message") or "", "codex")),
                             cwd=row.get("cwd") or "", updatedAt=stamp(row.get("updated_at")),
                             model=row.get("model"), reasoningEffort=row.get("reasoning_effort"))
                    if row.get("rollout_path") and os.path.isfile(row["rollout_path"]):
                        s["path"] = row["rollout_path"]
                    break
            if not found or not s.get("sourceTitle") or not s.get("path"):
                matches = {}
                index_path = self.path(".codex/session_index.jsonl")
                for row in (json_rows(index_path) if os.path.isfile(index_path) else ()):
                    rid = row.get("id")
                    if isinstance(rid, str) and (rid == sid or (not found and rid.startswith(sid))):
                        matches[rid] = row
                row = matches.get(sid) or (next(iter(matches.values())) if len(matches) == 1 else None)
                if row:
                    found = True
                    sid = s["id"] = row["id"]
                    s["sourceTitle"] = s.get("sourceTitle") or snippet(row.get("thread_name") or "", 200)
                    s["updatedAt"] = max(s["updatedAt"], stamp(row.get("updated_at")))
                    if not s.get("path") and row.get("rollout_path") and os.path.isfile(row["rollout_path"]):
                        s["path"] = row["rollout_path"]
        elif tool == "claude":
            labels_path = self.path(".claude/session_labels.json")
            labels = read_json(labels_path, {})
            try:
                labels_mtime = os.stat(labels_path).st_mtime
            except OSError:
                pass
            paths = []
            for path in glob.iglob(self.path(f".claude/projects/*/{glob.escape(sid)}*.jsonl")):
                if os.path.basename(path) == sid + ".jsonl":
                    paths = [path]
                    break
                paths.append(path)
            if len(paths) == 1:
                found = True
                path = paths[0]
                sid = s["id"] = os.path.basename(path)[:-6]
                s.update(self.summary(path, "claude"), path=path)
                project_dir = os.path.dirname(path)
                sidecar_path = os.path.join(project_dir, sid, "custom-title.json")
                sc = read_json(sidecar_path, {})
                if isinstance(sc, dict) and sc.get("customTitle"):
                    s["sidecarCustomTitle"] = snippet(clean_prompt(sc["customTitle"], "claude"), 220)
                    try:
                        s["sidecarMtime"] = os.stat(sidecar_path).st_mtime
                    except OSError:
                        s["sidecarMtime"] = 0.0
                idx = self._claude_project_index_entry(project_dir, sid)
                for native, dest in (("customTitle", "indexCustomTitle"), ("aiTitle", "indexAiTitle"), ("summary", "indexSummary")):
                    if idx.get(native):
                        s[dest] = snippet(clean_prompt(idx[native], "claude"), 220)
                if not s.get("firstPrompt") and idx.get("firstPrompt"):
                    s["firstPrompt"] = snippet(clean_prompt(idx["firstPrompt"], "claude"))
            for path in glob.iglob(self.path(PROVIDER_SCHEMA_ADAPTERS["claude"]["pid_state_glob"])):
                value = read_json(path, {})
                if not isinstance(value, dict) or value.get("sessionId") != sid:
                    continue
                found = True
                if value.get("cwd"):
                    s["cwd"] = value["cwd"]
                if value.get("nameSource") == "user":
                    s["userName"] = value.get("name")
                    try:
                        s["userNameMtime"] = os.stat(path).st_mtime
                    except OSError:
                        s["userNameMtime"] = 0.0
                else:
                    s["slug"] = value.get("name", "")
        stored = state.get(f"{tool}:{sid}", {})
        if not found and f"{tool}:{sid}" not in state and not sid.startswith("new-"):
            return None
        s["pinned"] = bool(stored.get("pinned"))
        s["title"] = self.resolve_session_title(s, stored=stored, labels=labels, labels_mtime=labels_mtime)
        cwd = s.get("cwd", "")
        s["shortCwd"] = "~" + cwd[len(self.home):] if cwd == self.home or cwd.startswith(self.home + "/") else cwd
        return s

    def get(self, tool, sid, force=False):
        session = self.discover(force).get(tool + ":" + sid)
        if session is None:
            raise KeyError("Session not found")
        return dict(session)

    def resolve_session_id(self, sid_or_prefix, tool=""):
        sessions = self.discover()
        matched = []
        for s in sessions.values():
            if tool and s["tool"] != tool:
                continue
            if s["id"] == sid_or_prefix or s["id"].startswith(sid_or_prefix):
                matched.append(s)
        if not matched:
            raise KeyError("Session not found: " + str(sid_or_prefix))
        matched.sort(key=lambda x: (-int(bool(x.get("isLive"))), -float(x.get("updatedAt") or 0)))
        return dict(matched[0])
