"""Process table: `ps` parsing into per-session bindings, the background `ps` / `lsof` snapshot, liveness checks."""
from __future__ import annotations

import errno
import os
import re
import shlex
import subprocess
import sys
import threading
import time

from mixhub.core.launch import AGY_FILTER_MODULE, python_module_invocation
from mixhub.hub.constants import TOOLS
from mixhub.hub.schema import PROVIDER_SCHEMA_ADAPTERS
from mixhub.hub.shell import run
from mixhub.proc.identity import get_boot_id, parse_lstart_epoch, ProcessIdentity
from mixhub.proc.tree import has_agent_ancestor, parse_ps_snapshot


def parse_processes(output, proc_table=None, boot_id=None, subagent_pids_out=None):
    if proc_table is None:
        proc_table = parse_ps_snapshot(output)
    resolved_boot = None
    processes = []
    for line in output.splitlines():
        raw_tokens = line.split()
        if len(raw_tokens) < 6 or not raw_tokens[0].isdigit() or not raw_tokens[1].isdigit():
            continue
        pid = int(raw_tokens[0])
        ppid = int(raw_tokens[1])
        tty = raw_tokens[2]
        if not re.fullmatch(r"ttys\d+|pts/\d+|tty\d+", tty) or not raw_tokens[3].isdigit():  # macOS ttysNNN, Linux pts/N
            continue
        rss_kb = int(raw_tokens[3])
        etime = raw_tokens[4]
        start_time = ""
        if len(raw_tokens) >= 11 and parse_lstart_epoch(raw_tokens[5:10]) is not None:
            start_time = " ".join(raw_tokens[5:10])
            m_cmd = re.match(
                r"^\s*\d+\s+\d+\s+\S+\s+\d+\s+\S+\s+"
                r"[A-Za-z]{3}\s+[A-Za-z]{3}\s+\d+\s+\d{2}:\d{2}:\d{2}\s+\d{4}\s*(.*)$",
                line,
            )
            cmd_str = m_cmd.group(1).strip() if m_cmd else " ".join(raw_tokens[10:])
        else:
            parts = line.split(None, 5)
            if len(parts) != 6:
                continue
            cmd_str = parts[5]
            node = proc_table.get(pid) if isinstance(proc_table, dict) else None
            if node and getattr(node, "start_time", ""):
                start_time = node.start_time
        try:
            args = shlex.split(cmd_str)
        except ValueError:
            continue
        if not args:
            continue
        exe = os.path.basename(args[0])
        wrapper = exe in ("node", "bun") or exe.startswith("python") or exe.startswith("Python")
        module_inv = python_module_invocation(args) if wrapper else None
        candidate = args[1] if wrapper and len(args) > 1 else args[0]
        base = os.path.basename(candidate)
        tool = base if base in TOOLS else ""
        if wrapper and ("@openai/codex" in candidate or base == "codex.js"):
            tool = "codex"
        elif wrapper and "@anthropic-ai/claude-code" in candidate:
            tool = "claude"
        elif wrapper and base in ("agy_claude_filter.py", "agy-claude"):
            tool = "agy"
        elif module_inv and module_inv[0] == AGY_FILTER_MODULE:  # `python [-P] -m mixhub.filters.agy_claude ...`
            tool = "agy"
        elif not tool and base.startswith("codex-") and "apple-darwin" in base:
            tool = "codex"
        argv = list(module_inv[1]) if module_inv and module_inv[0] == AGY_FILTER_MODULE else (args[2:] if wrapper else args[1:])
        if not tool or any(
            a in ("-p", "--print", "--headless", "--output-format", "--no-session-persistence")
            or a.startswith(("--output-format=", "--input-format="))
            for a in argv
        ):
            continue
        command_args = argv[:argv.index("--")] if "--" in argv else argv
        if tool == "opencode":
            positional = [a for a in command_args if not a.startswith("-")]
            if positional and positional[0] in PROVIDER_SCHEMA_ADAPTERS["opencode"]["non_interactive_subcommands"]:
                continue
        elif any(a in ("exec", "e", "app-server", "mcp", "mcp-server", "review", "login", "models", "update")
               for a in command_args if not a.startswith("-")):
            continue
        if proc_table and has_agent_ancestor(pid, proc_table) == "subagent_ppid":
            if isinstance(subagent_pids_out, set):
                subagent_pids_out.add(pid)
            elif isinstance(subagent_pids_out, dict):
                subagent_pids_out[pid] = "subagent_ppid"
            continue
        proc_entry = {
            "pid": pid,
            "ppid": ppid,
            "tty": tty,
            "rss_mb": round(rss_kb / 1024, 1),
            "etime": etime,
            "tool": tool,
            "args": argv,
            "wrapper": wrapper,
        }
        if start_time:
            if resolved_boot is None:
                resolved_boot = boot_id if boot_id is not None else get_boot_id()
            proc_entry["start_time"] = start_time
            if resolved_boot:
                proc_entry["boot_id"] = resolved_boot
                proc_entry["identity"] = ProcessIdentity(
                    boot_id=resolved_boot,
                    pid=pid,
                    start_time=start_time,
                )
        processes.append(proc_entry)
    deduped = []
    for p in processes:
        children = [q for q in processes if p["wrapper"] and not q["wrapper"]
                    and q["tool"] == p["tool"] and (q["tty"] == p["tty"] or q["ppid"] == p["pid"])]
        if children:
            for q in children:
                q.setdefault("wrapper_ttys", []).append(p["tty"])
        else:
            deduped.append(p)
    return deduped


def _proc_start_epoch(proc):
    tokens = str(proc.get("start_time") or "").split()
    wall = parse_lstart_epoch(tokens) if len(tokens) == 5 else None
    # `ps lstart` is local wall time and parse_lstart_epoch keeps it as-if-UTC (fine as an identity key): undo that
    return time.mktime(time.gmtime(wall)) if wall is not None else None


PROC_ROOT = "/proc"


def _proc_open_files(processes):
    """`lsof`'s cwd / executable / open-file answer read from /proc on Linux, where lsof is often not installed."""
    files = {}
    for p in processes:
        base = os.path.join(PROC_ROOT, str(p["pid"]))
        try:
            cwd = os.readlink(os.path.join(base, "cwd"))
        except OSError:
            continue  # gone, or another user's process
        paths = []
        try:
            paths.append(os.readlink(os.path.join(base, "exe")))
        except OSError:
            pass
        try:
            fds = sorted((n for n in os.listdir(os.path.join(base, "fd")) if n.isdigit()), key=int)
        except OSError:
            fds = []
        for n in fds:
            try:
                target = os.readlink(os.path.join(base, "fd", n))
            except OSError:
                continue
            if target.startswith("/"):  # skip socket:[…], pipe:[…], anon_inode:…
                paths.append(target)
        files[p["pid"]] = {"cwd": cwd, "paths": paths}
    return files


class ProcessMixin:
    """Live process scan and session binding."""

    def processes(self):
        raw_ps = run(["ps", "-axo", "pid,ppid,tty,rss,etime,lstart,command"])
        self._last_proc_table = parse_ps_snapshot(raw_ps)
        self._last_subagent_pids = {}
        return parse_processes(
            raw_ps,
            proc_table=self._last_proc_table,
            boot_id=get_boot_id(),
            subagent_pids_out=self._last_subagent_pids,
        )

    def open_files(self, processes):
        if not processes or sys.platform == "win32":
            return {}
        if sys.platform.startswith("linux") and os.path.isdir(PROC_ROOT):
            return _proc_open_files(processes)
        # -F avoids parsing spaces in cwd; -a makes the PID and descriptor sets intersect.
        try:
            result = subprocess.run(["lsof", "-nP", "-a", "-p", ",".join(str(p["pid"]) for p in processes),
                                     "-d", "cwd,txt,0-999", "-Fpnf"], capture_output=True, text=True, timeout=6)
        except (OSError, subprocess.TimeoutExpired) as exc:
            self.warnings.append("lsof: " + str(exc))
            return {}
        files, pid, fd = {}, None, ""
        for line in result.stdout.splitlines():
            if line.startswith("p") and line[1:].isdigit():
                pid = int(line[1:])
                files.setdefault(pid, {"paths": []})
            elif line.startswith("f"):
                fd = line[1:]
            elif line.startswith("n") and pid:
                if fd == "cwd":
                    files[pid]["cwd"] = line[1:]
                else:
                    files[pid]["paths"].append(line[1:])
        return files

    @staticmethod
    def _opencode_touched(s):
        return max(float(s.get("updatedAt") or 0), float(s.get("viewedAt") or 0))

    @staticmethod
    def _opencode_session_for_process(proc, cwd, sessions, claimed):
        """The OpenCode TUI opens no session file: pick the session in its cwd it most likely shows.

        One TUI can hold several sessions as tabs; the one last viewed or written to is taken as the tab on
        screen. `-c` continues the newest one; otherwise only a session touched since the process started
        counts, so a fresh TUI that has not sent a prompt yet never claims an older session from the project.
        """

        def touched(s):
            return max(float(s.get("updatedAt") or 0), float(s.get("viewedAt") or 0))

        if not cwd:
            return None
        started = _proc_start_epoch(proc)
        cont = any(a in ("-c", "--continue") for a in proc.get("args") or ())
        best = None
        for key, s in sessions.items():
            if not key.startswith("opencode:") or s.get("automated") or s["id"] in claimed:
                continue
            if os.path.realpath(s.get("cwd") or "") != os.path.realpath(cwd):
                continue
            if not cont and started is not None and touched(s) < started - 5:
                continue
            if best is None or touched(s) > touched(best):
                best = s
        return best["id"] if best else None

    def _scan_processes(self, use_proc_cache: bool, now_mono: float, proc_env_sig) -> tuple:
        """Run `ps` + incremental `lsof` and, with `use_proc_cache`, publish the result as the shared snapshot."""
        proc_scan_succeeded = True
        try:
            processes = self.processes()
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            processes = []
            proc_scan_succeeded = False
            self.warnings.append("Live detection: " + str(exc))
        proc_lsof_key = tuple((p["pid"], p.get("start_time", ""), tuple(p.get("args") or ())) for p in processes)
        if not use_proc_cache:
            files = self.open_files(processes)
            resident_ttys = set()
        else:
            pid_sigs = getattr(self, "_pid_lsof_sig", None)
            if not isinstance(pid_sigs, dict):
                pid_sigs = {}
                self._pid_lsof_sig = pid_sigs
            cached_files = self._files_cache if isinstance(self._files_cache, dict) else {}
            need_lsof_procs = []
            for p in processes:
                pid = p["pid"]
                psig = (p.get("start_time", ""), tuple(p.get("args") or ()))
                c_entry = cached_files.get(pid)
                if (
                    c_entry is None
                    or pid_sigs.get(pid) != psig
                    or (not c_entry.get("cwd") and not c_entry.get("paths"))
                ):
                    need_lsof_procs.append(p)
            if not need_lsof_procs and proc_lsof_key == getattr(self, "_last_lsof_proc_key", None):
                files = cached_files
                resident_ttys = getattr(self, "_resident_ttys_cache", set())
            else:
                new_files = self.open_files(need_lsof_procs) if need_lsof_procs else {}
                live_pids = {p["pid"] for p in processes}
                merged_files = {pid: entry for pid, entry in cached_files.items() if pid in live_pids}
                for p in need_lsof_procs:
                    pid = p["pid"]
                    if pid in new_files:
                        merged_files[pid] = new_files[pid]
                    else:
                        merged_files.setdefault(pid, {"paths": []})
                    pid_sigs[pid] = (p.get("start_time", ""), tuple(p.get("args") or ()))
                for dead_pid in list(pid_sigs):
                    if dead_pid not in live_pids:
                        pid_sigs.pop(dead_pid, None)
                files = merged_files
                resident_ttys = set()
                tty_windows = {}
                for sock in (("hubpool", "hub") if processes else ()):
                    try:
                        tp = subprocess.run(
                            ["tmux", "-L", sock, "list-panes", "-a", "-F", "#{pane_tty}\t#{window_name}"],
                            capture_output=True, text=True, timeout=0.6
                        )
                        if tp.returncode == 0:
                            for ln in tp.stdout.splitlines():
                                tty, _, wname = ln.strip().partition("\t")
                                if tty:
                                    resident_ttys.add(tty.removeprefix("/dev/"))
                                    if sock == "hubpool" and wname:
                                        tty_windows[tty.removeprefix("/dev/")] = wname
                    except Exception:
                        pass
                self._hubpool_tty_windows = tty_windows
                self._last_lsof_proc_key = proc_lsof_key
                self._lsof_scanned = now_mono
        if use_proc_cache:
            self._proc_cache = processes
            self._files_cache = files
            self._resident_ttys_cache = resident_ttys
            self._proc_scanned = now_mono if proc_scan_succeeded else 0.0
            self._proc_real_scanned = now_mono if proc_scan_succeeded else 0.0
            self._last_proc_env_sig = proc_env_sig if proc_scan_succeeded else None
            self._proc_snapshot = (processes, files, resident_ttys)
        return processes, files, resident_ttys

    def start_background_proc_scan(self, interval: float = 30.0) -> None:
        """Move `ps`/`lsof` off the caller's thread; `discover()` then only reads the latest snapshot."""
        if self._bg_proc_scan is not None:
            return
        bg = {"event": threading.Event(), "stop": threading.Event(), "lock": threading.Lock(), "env_sig": None}

        def loop():
            while not bg["stop"].is_set():
                with bg["lock"]:
                    env_sig = bg["env_sig"] if bg["env_sig"] is not None else getattr(self, "_last_proc_env_sig", None)
                    bg["event"].clear()
                    try:
                        self._scan_processes(True, time.monotonic(), env_sig)
                    except Exception as exc:  # keep serving the previous snapshot
                        self.warnings.append("Live detection: " + str(exc))
                with self._proc_snapshot_cond:
                    self.proc_snapshot_seq += 1
                    self._proc_snapshot_cond.notify_all()
                bg["event"].wait(interval)

        self._bg_proc_scan = bg
        bg["thread"] = threading.Thread(target=loop, name="mixhub-proc-scan", daemon=True)
        bg["thread"].start()

    def stop_background_proc_scan(self, timeout: float = 2.0) -> None:
        bg, self._bg_proc_scan = self._bg_proc_scan, None
        if bg is None:
            return
        bg["stop"].set()
        bg["event"].set()
        bg["thread"].join(timeout)

    def wait_proc_snapshot(self, seq: int, timeout: float) -> bool:
        """Block until a snapshot newer than `seq` exists (tests and startup only)."""
        with self._proc_snapshot_cond:
            return self._proc_snapshot_cond.wait_for(lambda: self.proc_snapshot_seq > seq, timeout)

    @staticmethod
    def _cached_pids_alive(processes: list[dict]) -> bool:
        """Check cached processes without sending a signal or scanning the process table."""
        for p in processes:
            pid = p.get("pid")
            if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
                return False
            try:
                os.kill(pid, 0)
            except PermissionError:
                continue
            except OSError as exc:
                if exc.errno != errno.EPERM:
                    return False
        return True
