"""Session lifecycle: hibernate, close, idle GC of resident processes, and storage slimming."""
from __future__ import annotations

import glob
import json
import os
import signal
import subprocess
import time

from mixhub.hub.shell import _DEFAULT_RUN, run
from mixhub.hub.util import read_json, stamp
from mixhub.i18n import t
from mixhub.proc.guard import safe_kill_process
from mixhub.proc.identity import ProcessIdentity
from mixhub.proc.tree import parse_ps_snapshot


class LifecycleMixin:
    """Hibernate / close / idle GC / storage slimming."""

    def hibernate(self, session, processes, dry, keep_working_set=False):
        plan_procs = []
        for p in processes:
            pp = {"pid": p["pid"], "tty": p["tty"]}
            if p.get("boot_id"):
                pp["boot_id"] = p["boot_id"]
            if p.get("start_time"):
                pp["start_time"] = p["start_time"]
            plan_procs.append(pp)
        plan = {"cwd": session["cwd"], "tty": processes[0]["tty"], "lastPid": processes[0]["pid"], "hibernatedAt": time.time(),
                "processes": plan_procs,
                # True = Hub auto-hibernated on the user's behalf (stays in Active); False = user closed it (Recent).
                "hibernatedByHub": bool(keep_working_set)}
        # A dry run is entirely side-effect free, including persistent state.
        if not dry:
            if keep_working_set:
                try:
                    hib_dir = os.path.join(self.root, "data", "hibernating")
                    os.makedirs(hib_dir, exist_ok=True)
                    with open(os.path.join(hib_dir, str(session["id"])), "w", encoding="utf-8") as hf:
                        hf.write(str(time.time()))
                except OSError:
                    pass
            else:
                self._remove_from_working_set(session["tool"], session["id"])
            self.save(session["tool"] + ":" + session["id"], plan)
            if "processes" not in self.__dict__:
                win_name = f"w-{session['tool']}-{session['id'][:12]}"
                subprocess.run(["tmux", "-L", "hubpool", "kill-window", "-t", f"pool:{win_name}"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            remaining = []
            unsignalled = []
            ident_by_pid = {}
            for p in processes:
                pid = p["pid"]
                ident = p.get("identity")
                if not isinstance(ident, ProcessIdentity) and p.get("boot_id") and p.get("start_time"):
                    ident = ProcessIdentity(
                        boot_id=str(p["boot_id"]),
                        pid=int(pid),
                        start_time=str(p["start_time"]),
                    )
                if isinstance(ident, ProcessIdentity):
                    ident_by_pid[pid] = ident
                    if safe_kill_process(ident, signal.SIGTERM, kill_fn=os.kill):
                        remaining.append(pid)
                else:
                    # Without an identity the PID could belong to anything by now: leave it running.
                    unsignalled.append(pid)
            deadline = time.monotonic() + 1.5
            while remaining and time.monotonic() < deadline:
                alive = {p["pid"] for p in self.processes()}
                remaining = [pid for pid in remaining if pid in alive]
                if remaining:
                    time.sleep(0.1)
            for pid in remaining:
                safe_kill_process(ident_by_pid[pid], signal.SIGINT, kill_fn=os.kill)
            remaining += unsignalled
            self.scanned = 0
        else:
            remaining = []
        return {"ok": True, "dry_run": dry, "saved": not dry, "state": plan, "signals": ["SIGTERM", "SIGINT if still running after 1.5s"], "pendingPids": remaining}

    def close_session(self, tool: str, sid: str, dry_run: bool = False) -> dict:
        """Explicitly close a session from Hub's Active working set (`✕` / `Sleep / Idle`), moving it to `Recent` (`·`)."""
        s = self.get(tool, sid, force=True)
        # Only processes in MixHub's own windows; an agent the user started elsewhere keeps running.
        processes = [p for p in s.get("processes") or [] if p.get("resident")]
        if not dry_run:
            self._remove_from_working_set(tool, sid)
        if processes:
            res = self.hibernate(s, processes, dry=dry_run, keep_working_set=False)
            res["removedFromWorkingSet"] = True
            return res
        if not dry_run:
            self.save(f"{tool}:{sid}", {"cwd": s.get("cwd") or self.home, "hibernatedAt": time.time(), "hibernatedByHub": False})
            if "processes" not in self.__dict__:
                win_name = f"w-{tool}-{sid[:12]}"
                subprocess.run(["tmux", "-L", "hubpool", "kill-window", "-t", f"pool:{win_name}"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.scanned = 0
        return {"ok": True, "dry_run": dry_run, "saved": not dry_run, "removedFromWorkingSet": True}

    @staticmethod
    def auto_sleep_seconds() -> float | None:
        """Idle time before a resident session is put to sleep: Preferences `auto_sleep_hours` (a positive number),
        `None` (never) by default. Memory pressure doesn't shorten it: a 16 GB Mac sits at the "warn" level most of
        the day, which used to cut 12 h to 6 h and stopped sessions their user still meant to come back to."""
        from mixhub.core.prefs import load_preferences

        hours = load_preferences().get("auto_sleep_hours")
        if isinstance(hours, bool) or not isinstance(hours, (int, float)) or hours <= 0:
            return None
        return float(hours) * 3600.0

    def gc_idle_resident(self, max_idle_sec: float | None = None, dry_run: bool = False, exclude_ids: set[str] | None = None) -> list[dict]:
        """
        Put idle `hubpool` resident processes (`resident=True`) to sleep after `max_idle_sec` (default
        `auto_sleep_seconds()`, off unless the user set `auto_sleep_hours`) while preserving their entry in
        `active_working_set.json` (`keep_working_set=True`), so the session stays in `Active` and resumes on click.
        Only touches sessions whose `agentState` is empty or `done_idle` and `sleep_blockers` is empty;
        never touches `working` / `wait_input`, `activeSubagents > 0`, `notFullyIdle`, unread replies,
        in-flight transcript tasks/timers, or sessions with non-auxiliary child processes.
        """
        effective_idle = self.auto_sleep_seconds() if max_idle_sec is None else float(max_idle_sec)
        if effective_idle is None:
            return []
        now = time.time()
        excl = set(exclude_ids or ())
        results = []
        seen_data = read_json(os.path.join(self.root, "data", "seen.json"), {})
        if not isinstance(seen_data, dict):
            seen_data = {}
        proc_table_loaded = False
        proc_table: dict | None = None
        proc_table_failed = False

        for s in list(self.discover(False).values()):
            if s["id"] in excl or f"{s['tool']}:{s['id']}" in excl:
                continue
            if not s.get("resident"):
                continue
            if s.get("agentState") in ("working", "wait_input") or int(s.get("activeSubagents") or 0) > 0 or s.get("notFullyIdle"):
                continue
            if s.get("agentState") not in (None, "", "done_idle"):
                continue
            if seen_data:
                skey = f"{s['tool']}:{s['id']}"
                raw_attn = s.get("attentionAt")
                base_ts = float(raw_attn) if raw_attn is not None else float(s.get("updatedAt") or 0.0)
                attn_at = max(base_ts, float((seen_data.get("attn") or {}).get(skey, 0.0)))
                seen_at = float((seen_data.get("seen") or {}).get(skey, 0.0))
                baseline = max(float(seen_data.get("bootstrapAt", 0.0)), seen_at)
                if attn_at > baseline + 0.5:
                    continue  # unread reply: keep resident so the user can see it instantly
            res_procs = [p for p in s.get("processes", []) if p.get("resident")]
            if not res_procs:
                continue
            last_act = max(float(s.get("updatedAt") or 0.0), float(s.get("transcriptAt") or 0.0), float(s.get("lockMtime") or 0.0))
            if last_act > 0 and (now - last_act) >= effective_idle:
                if not proc_table_loaded:
                    proc_table_loaded = True
                    try:
                        if "processes" not in self.__dict__ or run is not _DEFAULT_RUN:
                            raw_ps = run(["ps", "-axo", "pid,ppid,tty,rss,etime,lstart,command"])
                            proc_table = parse_ps_snapshot(raw_ps)
                            if not proc_table:
                                proc_table_failed = True
                            else:
                                self._last_proc_table = proc_table
                        else:
                            proc_table = getattr(self, "_last_proc_table", None)
                            if proc_table is None:
                                proc_table_failed = True
                    except Exception:
                        proc_table_failed = True
                if proc_table_failed:
                    continue
                try:
                    if self.sleep_blockers(s, proc_table=proc_table):
                        continue
                    r = self.hibernate(s, res_procs, dry=dry_run, keep_working_set=True)
                    results.append({"tool": s["tool"], "id": s["id"], **r})
                except Exception:
                    continue
        return results

    def hibernate_idle(self, body):
        dry = body.get("dry_run", False)
        if not isinstance(dry, bool):
            raise ValueError("dry_run must be boolean")
        candidates = [(s["tool"], s["id"], p["pid"]) for s in self.discover(True).values()
                      for p in s["processes"] if p.get("status") == "idle" and p.get("resident")]
        results = []
        for tool, sid, pid in candidates:
            # Revalidate session/PID/status immediately before signaling each process.
            session = self.get(tool, sid, force=True)
            if any(p["pid"] == pid and p.get("status") == "idle" and p.get("resident") for p in session["processes"]):
                results.append({"tool": tool, "id": sid, **self.action(tool, sid, "hibernate", {"dry_run": dry, "pid": pid})})
        return {"ok": True, "dry_run": dry, "results": results}

