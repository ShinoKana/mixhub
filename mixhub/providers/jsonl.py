"""Tolerant JSONL row iteration shared by resume checks and readiness probes."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Iterable, Iterator


def _iter_jsonl(path_or_entries: str | Path | Iterable) -> Iterator[dict[str, Any]]:
    if isinstance(path_or_entries, (str, os.PathLike, Path)):
        p = Path(os.path.expanduser(str(path_or_entries)))
        if p.is_file():
            with p.open(encoding="utf-8", errors="replace") as f:
                for line in f:
                    if not (line := line.strip()):
                        continue
                    try:
                        o = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(o, dict):
                        yield o
            return
        for line in str(path_or_entries).splitlines():
            if not (line := line.strip()):
                continue
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if isinstance(o, dict):
                yield o
        return
    for item in path_or_entries or ():
        if isinstance(item, dict):
            yield item
        elif isinstance(item, str) and item.strip().startswith("{"):
            try:
                o = json.loads(item.strip())
                if isinstance(o, dict):
                    yield o
            except ValueError:
                continue


def _d(x: Any) -> dict[str, Any]:
    return x if isinstance(x, dict) else {}
