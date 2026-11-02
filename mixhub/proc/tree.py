#!/usr/bin/env python3
"""`mixhub.proc.tree`: parse a `ps` snapshot into a process tree and detect processes spawned by another agent (A2)."""
from __future__ import annotations

import os
import re
import subprocess
from typing import Callable

from mixhub.proc.classify import _is_same_family_wrapper, classify_proc_args, has_internal_beacon
from mixhub.proc.identity import PS_ENV, normalize_start_time, parse_lstart_epoch, ProcNode


def parse_ps_snapshot(output: str) -> dict[int, ProcNode]:
    """
    Parse `ps -axo pid=,ppid=,lstart=,args=` (or `pid=,ppid=,tty=,rss=,etime=,lstart=,args=`
    or 3-column `pid=,ppid=,args=`) into `{pid: ProcNode}`.
    """
    table: dict[int, ProcNode] = {}
    for raw_line in (output or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 3 or not parts[0].isdigit() or not parts[1].isdigit():
            continue
        pid = int(parts[0])
        ppid = int(parts[1])

        # Format A: pid ppid tty rss etime Mon Day HH:MM:SS Year args... (10+ tokens where parts[5:10] is lstart)
        if len(parts) >= 10 and parts[3].isdigit() and parse_lstart_epoch(parts[5:10]) is not None:
            tty = parts[2]
            rss_kb = int(parts[3])
            etime = parts[4]
            lstart_tokens = parts[5:10]
            start_str = " ".join(lstart_tokens)
            start_ts = parse_lstart_epoch(lstart_tokens)
            m = re.match(
                r"^\s*\d+\s+\d+\s+\S+\s+\d+\s+\S+\s+"
                r"[A-Za-z]{3}\s+[A-Za-z]{3}\s+\d+\s+\d{2}:\d{2}:\d{2}\s+\d{4}\s*(.*)$",
                raw_line,
            )
            args_str = m.group(1).strip() if m else " ".join(parts[10:])
            table[pid] = ProcNode(
                pid=pid,
                ppid=ppid,
                start_time=start_str,
                args=args_str,
                start_ts=start_ts,
                tty=tty,
                rss_kb=rss_kb,
                etime=etime,
            )
            continue

        # Format B: pid ppid DayOfWeek Mon Day HH:MM:SS Year args... (7+ tokens where parts[2:7] is lstart)
        if len(parts) >= 7 and parse_lstart_epoch(parts[2:7]) is not None:
            lstart_tokens = parts[2:7]
            start_str = " ".join(lstart_tokens)
            start_ts = parse_lstart_epoch(lstart_tokens)
            m = re.match(
                r"^\s*\d+\s+\d+\s+[A-Za-z]{3}\s+[A-Za-z]{3}\s+\d+\s+\d{2}:\d{2}:\d{2}\s+\d{4}\s*(.*)$",
                raw_line,
            )
            args_str = m.group(1).strip() if m else " ".join(parts[7:])
            table[pid] = ProcNode(
                pid=pid,
                ppid=ppid,
                start_time=start_str,
                args=args_str,
                start_ts=start_ts,
            )
            continue

        # Format B2: 6-column legacy `pid ppid tty rss etime args...`
        if len(parts) >= 6 and parts[3].isdigit() and re.match(r"^(?:\d+-)?\d{1,3}:\d{2}(?::\d{2})?$", parts[4]):
            m6 = re.match(r"^\s*\d+\s+\d+\s+\S+\s+\d+\s+\S+\s+(.*)$", raw_line)
            args_str = m6.group(1).strip() if m6 else " ".join(parts[5:])
            table[pid] = ProcNode(
                pid=pid,
                ppid=ppid,
                start_time="",
                args=args_str,
                start_ts=None,
                tty=parts[2],
                rss_kb=int(parts[3]),
                etime=parts[4],
            )
            continue

        # Format C: 3-column synthetic test snapshot `pid ppid args...`
        m3 = re.match(r"^\s*\d+\s+\d+\s+(.*)$", raw_line)
        args_str = m3.group(1).strip() if m3 else " ".join(parts[2:])
        table[pid] = ProcNode(pid=pid, ppid=ppid, start_time="", args=args_str, start_ts=None)
    return table


def build_proc_tree(
    ps_output: str | None = None,
    run_fn: Callable = subprocess.run,
) -> dict[int, ProcNode]:
    """
    Build a system-wide `{pid: ProcNode}` process table from a single `ps -axo pid=,ppid=,lstart=,args=` call.
    """
    if ps_output is None:
        try:
            env = {**os.environ, **PS_ENV}
            res = run_fn(
                ["/bin/ps", "-axo", "pid=,ppid=,lstart=,args="],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=4,
                env=env,
            )
            ps_output = res.stdout if getattr(res, "returncode", 1) == 0 else ""
        except Exception:
            ps_output = ""
    return parse_ps_snapshot(ps_output)


def _extract_node_fields(entry: ProcNode | dict) -> tuple[int, int, str, int | None, str]:
    if isinstance(entry, ProcNode):
        return entry.pid, entry.ppid, entry.args, entry.start_ts, entry.start_time
    pid = int(entry.get("pid") or 0)
    ppid = int(entry.get("ppid") or 0)
    args = str(entry.get("args") or entry.get("command") or "")
    start_time = normalize_start_time(entry.get("start_time") or entry.get("lstart") or "")
    start_ts = entry.get("start_ts")
    if start_ts is None and start_time:
        start_ts = parse_lstart_epoch(start_time.split())
    return pid, ppid, args, start_ts, start_time


def find_agent_ancestor_detail(
    pid: int,
    proc_table: dict[int, ProcNode | dict],
    max_depth: int = 64,
) -> dict[str, str | int] | None:
    """
    Walk `ppid` upward from `pid` (up to `tmux` server or `pid <= 1`, `max_depth=64`).
    If the process or any ancestor carries `MIXHUB_INTERNAL=1`, or any non-wrapper ancestor
    is a coding agent (`claude`, `codex`, `agy`, `gemini`, `opencode`) or dispatch script
    (Preferences `dispatch_scripts`), return:
      `{"reason": "subagent_ppid", "ancestor_name": <name>, "ancestor_pid": <ppid>}`
    Otherwise return `None`.
    """
    try:
        cur_entry = proc_table.get(int(pid))
        if not cur_entry:
            return None
        cur_pid, cur_ppid, cur_args, cur_start_ts, _ = _extract_node_fields(cur_entry)
        if has_internal_beacon(cur_args, cur_entry.get("env") if isinstance(cur_entry, dict) else None):
            return {"reason": "subagent_ppid", "ancestor_name": "MIXHUB_INTERNAL", "ancestor_pid": cur_pid}

        cur_fam, cur_kind, cur_tail = classify_proc_args(cur_args)
        in_contiguous_wrapper_chain = cur_kind == "agent" and cur_fam is not None
        visited = {cur_pid}

        for _ in range(max_depth):
            if cur_ppid <= 1 or cur_ppid in visited:
                return None
            par_entry = proc_table.get(cur_ppid)
            if not par_entry:
                return None
            visited.add(cur_ppid)
            par_pid, par_ppid, par_args, par_start_ts, _ = _extract_node_fields(par_entry)

            # Guard against recycled parent PID in non-atomic ps snapshot (allow 2s clock jitter)
            if (
                par_start_ts is not None
                and cur_start_ts is not None
                and par_start_ts > cur_start_ts + 2
            ):
                return None

            par_fam, par_kind, par_tail = classify_proc_args(par_args)
            if par_kind == "tmux":
                return None

            if has_internal_beacon(par_args, par_entry.get("env") if isinstance(par_entry, dict) else None):
                return {"reason": "subagent_ppid", "ancestor_name": "MIXHUB_INTERNAL", "ancestor_pid": par_pid}

            if par_kind == "dispatch":
                return {"reason": "subagent_ppid", "ancestor_name": par_fam or "dispatch", "ancestor_pid": par_pid}

            if par_kind == "agent":
                if in_contiguous_wrapper_chain and _is_same_family_wrapper(cur_fam, cur_tail, par_fam, par_tail):
                    cur_pid, cur_ppid, cur_args, cur_start_ts = par_pid, par_ppid, par_args, par_start_ts
                    cur_tail = par_tail
                    continue
                return {"reason": "subagent_ppid", "ancestor_name": par_fam or "agent", "ancestor_pid": par_pid}

            in_contiguous_wrapper_chain = False
            cur_pid, cur_ppid, cur_args, cur_start_ts = par_pid, par_ppid, par_args, par_start_ts
    except Exception:
        return None
    return None


def has_agent_ancestor(
    pid: int,
    proc_table: dict[int, ProcNode | dict],
    max_depth: int = 64,
) -> str | None:
    """
    Return `"subagent_ppid"` if `pid` was spawned by another coding agent or dispatch script
    (or carries `MIXHUB_INTERNAL=1`), else `None`.
    """
    detail = find_agent_ancestor_detail(pid, proc_table, max_depth=max_depth)
    return "subagent_ppid" if detail is not None else None
