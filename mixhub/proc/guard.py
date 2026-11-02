#!/usr/bin/env python3
"""`mixhub.proc.guard`: live-process inspection and `safe_kill_process`, the identity triple guard (A5)."""
from __future__ import annotations

import os
import re
import signal
import subprocess
from typing import Callable

from mixhub.proc.classify import is_recognized_agent_command
from mixhub.proc.identity import (
    get_boot_id,
    normalize_boot_id,
    normalize_start_time,
    parse_lstart_epoch,
    ProcessIdentity,
    PS_ENV,
)


def inspect_live_process(
    pid: int,
    run_fn: Callable = subprocess.run,
) -> tuple[str, str] | None:
    """
    Query the OS for live process `pid` via `ps -o lstart=,args= -p <pid>`.
    Returns `(normalized_start_time, args_str)` if alive, or `None` if not found.
    """
    if not isinstance(pid, int) or pid <= 1:
        return None
    try:
        env = {**os.environ, **PS_ENV}
        res = run_fn(
            ["/bin/ps", "-o", "lstart=,args=", "-p", str(pid)],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=2,
            env=env,
        )
        if getattr(res, "returncode", 1) != 0:
            return None
        for raw_line in (res.stdout or "").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) >= 5 and parse_lstart_epoch(parts[:5]) is not None:
                start_str = " ".join(parts[:5])
                m = re.match(
                    r"^\s*[A-Za-z]{3}\s+[A-Za-z]{3}\s+\d+\s+\d{2}:\d{2}:\d{2}\s+\d{4}\s*(.*)$",
                    raw_line,
                )
                args_str = m.group(1).strip() if m else " ".join(parts[5:])
                return normalize_start_time(start_str), args_str
    except Exception:
        return None
    return None


def capture_process_identity(
    pid: int,
    start_time: str | None = None,
    boot_id: str | None = None,
    run_fn: Callable = subprocess.run,
) -> ProcessIdentity | None:
    """Capture a `ProcessIdentity(boot_id, pid, start_time)` for a live PID."""
    if not isinstance(pid, int) or pid <= 1:
        return None
    resolved_boot = normalize_boot_id(boot_id) if boot_id else get_boot_id(run_fn=run_fn)
    resolved_start = normalize_start_time(start_time) if start_time else ""
    if not resolved_start:
        live = inspect_live_process(pid, run_fn=run_fn)
        if live is None:
            return None
        resolved_start = live[0]
    if not resolved_boot or not resolved_start:
        return None
    return ProcessIdentity(boot_id=resolved_boot, pid=pid, start_time=resolved_start)


def safe_kill_process(
    identity: ProcessIdentity | dict | tuple,
    sig: int = signal.SIGTERM,
    *,
    run_fn: Callable = subprocess.run,
    kill_fn: Callable[[int, int], None] = os.kill,
    current_boot_id: str | None = None,
) -> bool:
    """
    Send `sig` to `identity.pid` ONLY IF all 4 guards pass:
      1) `pid > 1` and `pid` is neither the current process nor its parent (`os.getpid()`, `os.getppid()`).
      2) `current_boot_id == identity.boot_id` (host has not rebooted since PID was recorded).
      3) Live process `pid` exists and its `ps -o lstart=` matches `identity.start_time` (PID was not recycled).
      4) Live process command line is a recognized coding agent CLI (`claude`, `codex`, `agy`, `gemini`, `opencode`),
         never a system daemon or unrelated process.
    Returns `True` if signal was sent, `False` if refused or process already exited.
    """
    try:
        ident = ProcessIdentity.from_mapping(identity)
        if ident is None:
            return False
        pid = ident.pid
        if not isinstance(pid, int) or pid <= 1 or pid in (os.getpid(), os.getppid()):
            return False
        if not ident.boot_id or not ident.start_time:
            return False

        live_boot = normalize_boot_id(current_boot_id) if current_boot_id is not None else get_boot_id(run_fn=run_fn, use_cache=False)
        if not live_boot or live_boot != ident.boot_id:
            return False

        live_info = inspect_live_process(pid, run_fn=run_fn)
        if live_info is None:
            return False
        live_start, live_args = live_info

        # Compare both normalized string and UTC epoch (if both parse as standard lstart)
        ident_epoch = parse_lstart_epoch(ident.start_time.split())
        live_epoch = parse_lstart_epoch(live_start.split())
        if ident_epoch is not None and live_epoch is not None:
            if ident_epoch != live_epoch:
                return False
        elif live_start != ident.start_time:
            return False

        if not is_recognized_agent_command(live_args):
            return False

        kill_fn(pid, sig)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False
    except Exception:
        return False
