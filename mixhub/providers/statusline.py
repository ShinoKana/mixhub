"""`mixhub statusline claude|agy`: a statusline command that records each agent's quota for the MixHub footer.

Claude Code and AGY pipe a JSON status (model, and on Claude `rate_limits`) into their statusline command on every
refresh. Pointing that setting at `mixhub statusline <tool>` keeps the footer's 5h / weekly quota exact instead of
inferred from transcripts. The snapshot lands in the state directory (`data/claude-statusline.json`,
`data/agy-statusline.json`); the footer also reads the older `/tmp/*-statusline.json` files.
"""
from __future__ import annotations

import json
import os
import sys
import time

from mixhub.core import paths

# Snapshots whose 5h / weekly reset differs by less than this belong to the same window.
SAME_WINDOW_SEC = 90


def claude_quota_path() -> str:
    return os.path.join(paths.STATE_ROOT, "data", "claude-statusline.json")


def agy_statusline_path() -> str:
    return os.path.join(paths.STATE_ROOT, "data", "agy-statusline.json")


def _write_json_atomic(path: str, doc: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(doc, f)
    os.replace(tmp, path)


def claude_windows(status: dict, now: float) -> dict:
    """`rate_limits.five_hour` / `seven_day` as footer buckets (`claude-5h`, `claude-weekly`)."""
    limits = status.get("rate_limits")
    out = {}
    if not isinstance(limits, dict):
        return out
    for src, dst in (("five_hour", "claude-5h"), ("seven_day", "claude-weekly")):
        w = limits.get(src)
        if not isinstance(w, dict) or (w.get("used_percentage") is None and w.get("resets_at") is None):
            continue
        used = max(0.0, min(100.0, float(w.get("used_percentage") or 0.0)))
        out[dst] = {
            "remaining_fraction": (100.0 - used) / 100.0,
            "reset_time": w.get("resets_at"),
            "observed_at": now,
            "started": bool(w.get("resets_at") is not None or used > 0.0),
        }
    return out


def merge_windows(old: dict, new: dict) -> dict:
    """Several Claude windows report at once: a snapshot from an older window never overwrites a newer one, and within
    one window the lower remaining share wins (a session that hasn't refreshed yet reports a stale, higher value)."""
    merged = dict(old)
    for key, cand in new.items():
        prev = merged.get(key)
        ro, rn = (prev or {}).get("reset_time"), cand.get("reset_time")
        if isinstance(prev, dict) and isinstance(ro, (int, float)) and isinstance(rn, (int, float)):
            if rn < ro - SAME_WINDOW_SEC:
                continue
            if abs(rn - ro) <= SAME_WINDOW_SEC:
                entry = dict(cand, reset_time=max(ro, rn))
                old_rf, new_rf = prev.get("remaining_fraction"), cand.get("remaining_fraction")
                if isinstance(old_rf, (int, float)) and isinstance(new_rf, (int, float)) and old_rf < new_rf:
                    entry["remaining_fraction"] = old_rf
                merged[key] = entry
                continue
        merged[key] = cand
    return merged


def record_claude(status: dict, now: float | None = None) -> dict:
    """Merge this refresh's windows into the snapshot file; return the windows now on record."""
    now = time.time() if now is None else now
    new = claude_windows(status, now)
    path = claude_quota_path()
    try:
        with open(path, encoding="utf-8") as f:
            doc = json.load(f)
        old = doc.get("quota") if isinstance(doc, dict) and isinstance(doc.get("quota"), dict) else {}
    except (OSError, ValueError):
        old = {}
    if not new:
        return old
    merged = merge_windows(old, new)
    _write_json_atomic(path, {"quota": merged, "updated_at": now})
    return merged


def status_line(status: dict, windows: dict) -> str:
    model = status.get("model")
    name = model.get("display_name") or model.get("id") if isinstance(model, dict) else model
    parts = [str(name)] if name else []
    for key, label in (("claude-5h", "5h"), ("claude-weekly", "wk")):
        w = windows.get(key)
        if isinstance(w, dict) and isinstance(w.get("remaining_fraction"), (int, float)):
            parts.append(f"{label} {round(w['remaining_fraction'] * 100)}%")
    return " · ".join(parts)


def main(argv: list[str]) -> int:
    tool = argv[0] if argv else ""
    if tool not in ("claude", "agy"):
        print("Usage: mixhub statusline claude|agy   (reads the agent's status JSON on stdin)", file=sys.stderr)
        return 2
    raw = sys.stdin.read()
    try:
        status = json.loads(raw) if raw.strip() else {}
    except ValueError:
        status = {}
    if not isinstance(status, dict):
        status = {}
    try:
        if tool == "claude":
            windows = record_claude(status)
        else:
            os.makedirs(os.path.dirname(agy_statusline_path()), exist_ok=True)
            with open(agy_statusline_path(), "w", encoding="utf-8") as f:
                f.write(raw)
            windows = {}
    except OSError:
        windows = {}
    print(status_line(status, windows))
    return 0
