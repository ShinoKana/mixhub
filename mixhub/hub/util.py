"""Small shared helpers: hostname label, tolerant JSON reads, timestamp parsing, file / SQLite change signatures."""
from __future__ import annotations

import datetime
import json
import os
import re
import socket


def clean_hostname(raw: str | None = None) -> str:
    """Return local hostname with `.local` and macOS mDNS `-<digits>` suffix stripped (`Studio-4.local` -> `Studio`)."""
    h = (raw if raw is not None else socket.gethostname()) or "local"
    base = re.sub(r"-\d+$", "", str(h).split(".")[0].strip())
    return base or "local"


def read_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def stamp(value):
    if isinstance(value, (int, float)):
        return value / 1000 if value > 100000000000 else value
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return 0


def _file_sig(path: str | None) -> tuple[int, int, int, int, int] | None:
    if not path:
        return None
    try:
        st = os.stat(path)
        # st_nlink: a directory gains a link per new subdirectory even when coarse (Linux/WSL) timestamps don't move
        return (st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_nlink)
    except OSError:
        return None


def _db_file_sig(path: str) -> tuple:
    return (_file_sig(path), _file_sig(path + "-wal"), _file_sig(path + "-journal"))
