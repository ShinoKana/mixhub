#!/usr/bin/env python3
"""`mixhub.proc.identity`: `ps` environment, the `(boot_id, pid, start_time)` identity triple, `ProcNode`, and boot id / `lstart` parsing."""
from __future__ import annotations

import calendar
from dataclasses import dataclass
import re
import subprocess
from typing import Callable


PS_ENV = {
    "PATH": "/bin:/usr/bin:/usr/sbin:/opt/homebrew/bin:/usr/local/bin",
    "LC_ALL": "C",
    "LANG": "C",
    "TZ": "UTC",
}

_MONTHS = {
    name: idx
    for idx, name in enumerate(
        ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
        start=1,
    )
}
_DOW_NAMES = {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}


_BOOT_SEC_RE = re.compile(r"\bsec\s*=\s*(\d+)")


@dataclass(frozen=True)
class ProcessIdentity:
    """Unique process identity triple `(boot_id, pid, start_time)` to prevent PID-reuse false kills."""

    boot_id: str
    pid: int
    start_time: str

    def to_dict(self) -> dict[str, str | int]:
        return {"boot_id": self.boot_id, "pid": int(self.pid), "start_time": normalize_start_time(self.start_time)}

    @classmethod
    def from_mapping(cls, data: ProcessIdentity | dict | tuple) -> ProcessIdentity | None:
        if isinstance(data, ProcessIdentity):
            return cls(
                boot_id=normalize_boot_id(data.boot_id),
                pid=int(data.pid),
                start_time=normalize_start_time(data.start_time),
            )
        if isinstance(data, tuple) and len(data) == 3:
            boot_id, pid, start_time = data
            try:
                return cls(
                    boot_id=normalize_boot_id(str(boot_id or "")),
                    pid=int(pid),
                    start_time=normalize_start_time(str(start_time or "")),
                )
            except (TypeError, ValueError):
                return None
        if isinstance(data, dict):
            boot_id = data.get("boot_id") or data.get("bootId") or ""
            pid = data.get("pid")
            start_time = data.get("start_time") or data.get("startTime") or data.get("lstart") or ""
            if pid is None:
                return None
            try:
                return cls(
                    boot_id=normalize_boot_id(str(boot_id)),
                    pid=int(pid),
                    start_time=normalize_start_time(str(start_time)),
                )
            except (TypeError, ValueError):
                return None
        return None


class ProcNode(dict):
    """Process table entry supporting both attribute (`node.ppid`) and dict (`node['ppid']`) access."""

    def __init__(
        self,
        pid: int,
        ppid: int,
        start_time: str = "",
        args: str = "",
        start_ts: int | None = None,
        tty: str = "",
        rss_kb: int = 0,
        etime: str = "",
    ):
        norm_start = normalize_start_time(start_time)
        if start_ts is None and norm_start:
            start_ts = parse_lstart_epoch(norm_start.split())
        super().__init__(
            pid=int(pid),
            ppid=int(ppid),
            start_time=norm_start,
            lstart=norm_start,
            start_ts=start_ts,
            args=str(args or ""),
            command=str(args or ""),
            tty=str(tty or ""),
            rss_kb=int(rss_kb or 0),
            etime=str(etime or ""),
        )

    @property
    def pid(self) -> int:
        return self["pid"]

    @property
    def ppid(self) -> int:
        return self["ppid"]

    @property
    def start_time(self) -> str:
        return self["start_time"]

    @property
    def start_ts(self) -> int | None:
        return self.get("start_ts")

    @property
    def args(self) -> str:
        return self["args"]


_CACHED_BOOT_ID: str | None = None


def normalize_boot_id(raw: str | None) -> str:
    """Normalize `sysctl -n kern.boottime` output so NTP human-date suffix differences never cause false mismatches."""
    if not raw:
        return ""
    s = " ".join(str(raw).strip().split())
    m = _BOOT_SEC_RE.search(s)
    if m:
        return f"bt:{m.group(1)}"
    return s


def normalize_start_time(raw: str | None) -> str:
    """Collapse variable whitespace in `ps -o lstart=` (e.g. `Sun Oct  4 03:41:30 2026` -> `Sun Oct 4 03:41:30 2026`)."""
    if not raw:
        return ""
    return " ".join(str(raw).strip().split())


def parse_lstart_epoch(tokens: list[str] | tuple[str, ...]) -> int | None:
    """
    Locale-independent parser for 5-token `ps -o lstart=` (`DayOfWeek Mon Day HH:MM:SS Year`).
    Returns integer UTC/epoch seconds, or `None` if tokens do not match `lstart` format.
    """
    if len(tokens) != 5:
        return None
    dow, mon, day_s, hms, year_s = tokens
    if dow not in _DOW_NAMES or mon not in _MONTHS:
        return None
    try:
        day = int(day_s)
        year = int(year_s)
        parts = hms.split(":")
        if len(parts) != 3:
            return None
        hour, minute, sec = int(parts[0]), int(parts[1]), int(parts[2])
        return int(calendar.timegm((year, _MONTHS[mon], day, hour, minute, sec, 0, 0, 0)))
    except (TypeError, ValueError):
        return None


LINUX_BOOT_ID = "/proc/sys/kernel/random/boot_id"


def get_boot_id(run_fn: Callable = subprocess.run, use_cache: bool = True) -> str:
    """Return normalized host `boot_id`: `sysctl -n kern.boottime` on macOS, the kernel's boot_id on Linux."""
    global _CACHED_BOOT_ID
    if use_cache and _CACHED_BOOT_ID and run_fn is subprocess.run:
        return _CACHED_BOOT_ID
    try:
        res = run_fn(
            ["/usr/sbin/sysctl", "-n", "kern.boottime"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if getattr(res, "returncode", 1) == 0 and getattr(res, "stdout", ""):
            norm = normalize_boot_id(res.stdout)
            if norm:
                if run_fn is subprocess.run:
                    _CACHED_BOOT_ID = norm
                return norm
    except Exception:
        pass
    try:
        with open(LINUX_BOOT_ID, encoding="ascii") as f:
            norm = f"linux:{f.read().strip()}"
        if norm != "linux:":
            if run_fn is subprocess.run:
                _CACHED_BOOT_ID = norm
            return norm
    except OSError:
        pass
    return _CACHED_BOOT_ID or ""


def reset_boot_id_cache() -> None:
    global _CACHED_BOOT_ID
    _CACHED_BOOT_ID = None
