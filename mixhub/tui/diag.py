#!/usr/bin/env python3
"""`mixhub.tui.diag`: Structured JSONL diagnostic log (`data/tui_diag.log`) shared by sidebar, reader and headers."""
from __future__ import annotations

import json
import os
import time

from mixhub.tui.paths import _effective_tui_diag_log_path


def log_tui_diag(event: str, **fields: object) -> None:
    """Append a structured JSONL diagnostic event to `TUI_DIAG_LOG_PATH`, auto-truncating to 128KB when > 256KB."""
    path = _effective_tui_diag_log_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        rec: dict[str, object] = {"ts": round(time.time(), 3), "pid": os.getpid(), "event": str(event)}
        rec.update(fields)
        line = json.dumps(rec, ensure_ascii=False, default=str) + "\n"
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
        st = os.stat(path)
        if st.st_size > 262144:
            with open(path, "rb") as rf:
                rf.seek(max(0, st.st_size - 131072))
                tail = rf.read()
            nl = tail.find(b"\n")
            if 0 <= nl < len(tail) - 1:
                tail = tail[nl + 1 :]
            tmp = f"{path}.tmp.{os.getpid()}"
            with open(tmp, "wb") as wf:
                wf.write(tail)
            os.replace(tmp, path)
    except OSError:
        pass
