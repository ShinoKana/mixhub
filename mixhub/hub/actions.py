"""Explicit session actions: focus a terminal, rename with native title sync, and the `action` dispatcher."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys

from mixhub.hub.schema import codex_state_db_candidates, PROVIDER_SCHEMA_ADAPTERS
from mixhub.hub.shell import apple_string, focus_script, resume_command, run


class ActionsMixin:
    """Focus / rename / resume and the action dispatcher."""

    def focus(self, process, dry):
        tty, pane, client = process["tty"], None, None
        try:
            panes = run(["tmux", "list-panes", "-a", "-F", "#{session_name}:#{window_index}.#{pane_index}|#{pane_tty}"], timeout=2)
            pane = next((line.split("|", 1)[0] for line in panes.splitlines() if line.endswith("|/dev/" + tty)), None)
            if pane:
                clients = run(["tmux", "list-clients", "-F", "#{session_name}|#{client_tty}|#{client_activity}"], timeout=2)
                matches = [line.split("|") for line in clients.splitlines() if line.split("|")[0] == pane.rsplit(":", 1)[0]]
                matches.sort(key=lambda a: int(a[2] or 0) if len(a) > 2 else 0, reverse=True)
                if not matches:
                    raise ValueError("tmux session has no attached client")
                client = matches[0][1]
                tty = client.removeprefix("/dev/")
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            if pane:
                raise
        commands = [["tmux", "select-window", "-t", pane.rsplit(".", 1)[0]], ["tmux", "select-pane", "-t", pane]] if pane else []
        script = focus_script(tty)
        if not dry and sys.platform != "darwin" and not pane:
            raise ValueError("Switching to a session's terminal tab needs Terminal.app (macOS); open the session in "
                             "the MixHub workspace instead.")
        if not dry:
            for command in commands:
                run(command)
            run(["osascript", "-e", script])
        return {"ok": True, "dry_run": dry, "tty": tty, "pane": pane, "clientTty": client, "tmuxCommands": commands, "script": script}

    def _sync_native_rename(self, session: dict, new_title: str):
        """Write custom title back to the native CLI's session store so native `--resume` and MixHub stay 100% synced."""
        if not new_title:
            return
        tool = session.get("tool")
        sid = session.get("id")
        tpath = session.get("path")
        if tool == "claude" and sid and tpath and os.path.isfile(tpath):
            # Mirror Claude Code 2.1.x `/rename`: custom-title + agent-name records plus the
            # `<project_dir>/<sid>/custom-title.json` sidecar. `sessions/<pid>.json` stays untouched.
            try:
                st = os.stat(tpath)
            except OSError:
                return
            rec1 = json.dumps({"type": "custom-title", "customTitle": new_title, "sessionId": sid}, ensure_ascii=False)
            rec2 = json.dumps({"type": "agent-name", "agentName": new_title, "sessionId": sid}, ensure_ascii=False)
            try:
                lead = ""
                if st.st_size > 0:
                    with open(tpath, "rb") as f:
                        f.seek(-1, os.SEEK_END)
                        lead = "" if f.read(1) == b"\n" else "\n"
                # Both land in one write; custom-title stays the final line for readers that only check the last record.
                with open(tpath, "a", encoding="utf-8") as f:
                    f.write(lead + rec2 + "\n" + rec1 + "\n")
                os.utime(tpath, (st.st_atime, st.st_mtime))
            except OSError:
                pass
            try:
                sc_dir = os.path.join(os.path.dirname(tpath), sid)
                os.makedirs(sc_dir, exist_ok=True)
                sc_path = os.path.join(sc_dir, "custom-title.json")
                tmp = sc_path + ".tmp." + str(os.getpid())
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({"customTitle": new_title}, f, ensure_ascii=False)
                os.chmod(tmp, 0o600)
                os.replace(tmp, sc_path)
                os.utime(sc_path, (st.st_atime, st.st_mtime))
            except OSError:
                pass
        elif tool == "agy" and sid:
            db_path = self.path(PROVIDER_SCHEMA_ADAPTERS["agy"]["summary_db"])
            table = PROVIDER_SCHEMA_ADAPTERS["agy"]["summary_table"]
            if os.path.isfile(db_path):
                conn = None
                try:
                    conn = sqlite3.connect(db_path, timeout=0.5)
                    conn.execute(f"UPDATE {table} SET title = ? WHERE conversation_id = ?", (new_title, sid))
                    conn.commit()
                except sqlite3.Error:
                    pass
                finally:
                    if conn is not None:
                        try:
                            conn.close()
                        except Exception:
                            pass
            self._agy_row_memo.pop(sid, None)
            self._db_sig_cache.pop((db_path, table), None)
            if "agy:" + sid in self._session_db_meta:
                self._session_db_meta["agy:" + sid]["sourceTitle"] = new_title
        elif tool == "codex" and sid:
            table = PROVIDER_SCHEMA_ADAPTERS["codex"]["threads_table"]
            for codex_db in codex_state_db_candidates(self.path):
                if not os.path.isfile(codex_db):
                    continue
                conn = None
                try:
                    conn = sqlite3.connect(codex_db, timeout=0.5)
                    cur = conn.execute(f"UPDATE {table} SET title = ? WHERE id = ?", (new_title, sid))
                    conn.commit()
                    self._db_sig_cache.pop((codex_db, table), None)
                    if cur.rowcount:
                        break
                except sqlite3.Error:
                    pass
                finally:
                    if conn is not None:
                        try:
                            conn.close()
                        except Exception:
                            pass
            self._codex_row_memo.pop(sid, None)

    def action(self, tool, sid, action, body):
        dry = body.get("dry_run", False)
        if not isinstance(dry, bool):
            raise ValueError("dry_run must be boolean")
        s = self.get(tool, sid, force=action in ("hibernate", "focus", "close"))
        if action == "close":
            return self.close_session(tool, sid, dry_run=dry)
        if action in ("rename", "pin", "metadata"):
            updates = {}
            if "title" in body:
                if not isinstance(body["title"], str) or len(body["title"]) > 200:
                    raise ValueError("title must be a string of at most 200 characters")
                updates["title"] = body["title"].strip()
            if "pinned" in body:
                if not isinstance(body["pinned"], bool):
                    raise ValueError("pinned must be boolean")
                updates["pinned"] = body["pinned"]
            if not updates:
                raise ValueError("Provide title or pinned")
            if not dry:
                saved = dict(updates)
                if updates.get("title"):
                    # Remember native titles already shown, so Claude re-appending an old title cannot undo this rename.
                    prev_stored = self.state()["sessions"].get(tool + ":" + sid, {})
                    prev_seen = prev_stored.get("titleNativeSeen")
                    prev_seen = prev_seen if isinstance(prev_seen, list) else []
                    native_prev = [t for t in (s.get(k) for k in ("userName", "claudeAgentName", "customTitle",
                                                                    "sidecarCustomTitle", "customTitleFromTail",
                                                                    "sourceTitle", "aiTitle", "nativeSummary"))
                                   if isinstance(t, str) and t and t != updates["title"]]
                    saved["titleNativeSeen"] = list(dict.fromkeys([*prev_seen, *native_prev, updates["title"]]))[-8:]
                self.save(tool + ":" + sid, saved)
                if "title" in updates and updates["title"]:
                    self._sync_native_rename(s, updates["title"])
            return {"ok": True, "dry_run": dry, **updates}
        if action == "resume":
            cmd = resume_command(s)
            script = 'tell application "Terminal"\ndo script ' + apple_string(cmd) + '\nactivate\nend tell'
            if not dry:
                if s["isLive"] and s.get("processes"):
                    raise ValueError("Session is already live; use focus")
                if sys.platform != "darwin":
                    raise ValueError("Opening a session in a new window needs Terminal.app (macOS). Run it in a "
                                     "terminal yourself: " + cmd)
                run(["osascript", "-e", script])
                self.scanned = 0
            return {"ok": True, "dry_run": dry, "command": cmd, "script": script}
        if action in ("focus", "hibernate"):
            processes = s["processes"]
            if body.get("pid") is not None:
                processes = [p for p in processes if p["pid"] == body["pid"]]
            if action == "hibernate":
                # Sleep stops only what MixHub started; an agent running in another terminal is the user's to quit.
                if processes and not any(p.get("resident") for p in processes):
                    raise ValueError("This session runs outside MixHub; quit it in its own terminal")
                processes = [p for p in processes if p.get("resident")]
            if not processes:
                if action == "hibernate":
                    return self.close_session(tool, sid, dry_run=dry)
                raise ValueError("Session has no matching live terminal process")
            if action == "focus":
                return self.focus(processes[0], dry)
            return self.hibernate(s, processes, dry, keep_working_set=bool(body.get("keep_working_set", False)))
        if action == "move_to_recent":
            return self.close_session(tool, sid, dry_run=dry)
        if action == "handoff":
            return self.handoff({**body, "id": sid, "from_tool": tool})
        if action == "send":
            return self.send(s, body, dry_run=dry)
        raise KeyError("Unknown action")

    def send(self, session, body, dry_run=False):
        """Type `body["text"]` into the session's tmux pane, or resume it headless (see `mixhub.hub.send`)."""
        from mixhub.hub.send import send_to_session
        text = body.get("text", "")
        attachments = body.get("attachments") or []
        if not isinstance(text, str) or not isinstance(attachments, list):
            raise ValueError("text must be a string and attachments a list")
        return send_to_session(
            session,
            text,
            attachments,
            home=self.home,
            allow_headless_exec=bool(body.get("allow_headless_exec", True)),
            dry_run=dry_run,
        )
