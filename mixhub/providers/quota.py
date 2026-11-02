"""Claude quota windows: statusline / usage-line observations, 5h and weekly reset inference, limit hits."""
from __future__ import annotations

from datetime import datetime
import json
import os
import re
from typing import Any, Sequence


# ───────────────────────── (5) Claude Live Quota & Post-Reset Window Tracker ─────────────────────────

CLAUDE_STATUSLINE_JSON = "/tmp/claude-statusline.json"
CLAUDE_5H_SECONDS = 5 * 3600
CLAUDE_WK_SECONDS = 7 * 86400
CLAUDE_5H_ALIGN = 600        # Anthropic 5h window aligns to 10-minute boundary + 5h (e.g. 17:14:52 -> 22:10:00)
CLAUDE_SAME_WIN_TOL = 90     # Absorb ±90s resets_at header jitter within the same window


class QuotaObs( tuple ):
    """Lightweight quota observation `(rem_fraction_0_1, reset_epoch, seen_epoch, used_flag)`."""

    __slots__ = ()

    def __new__(
        cls,
        rem: float | None,
        reset: float | None,
        seen: float | None,
        used: bool = False,
    ):
        return super().__new__(
            cls,
            (
                None if rem is None else max(0.0, min(1.0, float(rem))),
                None if reset is None else float(reset),
                None if seen is None else float(seen),
                bool(used),
            ),
        )

    @property
    def rem(self) -> float | None:
        return self[0]

    @property
    def reset(self) -> float | None:
        return self[1]

    @property
    def seen(self) -> float | None:
        return self[2]

    @property
    def used(self) -> bool:
        return self[3]


def _quota_epoch(v: Any) -> float | None:
    """Convert epoch seconds, epoch milliseconds, or ISO-8601 string to epoch seconds."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        fv = float(v)
        return fv / 1000.0 if fv > 1e11 else (fv if fv > 0 else None)
    s = str(v).strip()
    if not s:
        return None
    if re.fullmatch(r"\d+(\.\d+)?", s):
        fv = float(s)
        return fv / 1000.0 if fv > 1e11 else (fv if fv > 0 else None)
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _rem_pct_from_fraction(frac: float | None) -> int | None:
    """Convert remaining fraction `0.0..1.0` to integer percentage `0..100` (`<1.0` clamps to `<=99`)."""
    if frac is None:
        return None
    raw = max(0.0, min(1.0, float(frac))) * 100.0
    if raw >= 100.0:
        return 100
    return min(99, max(0, round(raw)))


def merge_quota_obs(a: QuotaObs | None, b: QuotaObs | None) -> QuotaObs | None:
    """
    Monotonically merge two quota observations for the same window type (`5h` or `wk`):
      - Later window (`reset` larger by > `CLAUDE_SAME_WIN_TOL`) always wins, preventing an idle session
        from overwriting a newly started 5h window with an expired one.
      - Within the same window (`|a.reset - b.reset| <= CLAUDE_SAME_WIN_TOL`), smaller `rem` (higher used%)
        and newer `seen` timestamp win, and `used` is OR-combined.
    """
    if a is None or b is None:
        return b if a is None else a
    if a.reset is None or b.reset is None:
        return a if (b.reset is None and a.reset is not None) else b
    if b.reset > a.reset + CLAUDE_SAME_WIN_TOL:
        return b
    if b.reset < a.reset - CLAUDE_SAME_WIN_TOL:
        return a
    rems = [x for x in (a.rem, b.rem) if x is not None]
    seens = [x for x in (a.seen, b.seen) if x is not None]
    return QuotaObs(
        min(rems) if rems else None,
        max(a.reset, b.reset),
        max(seens) if seens else None,
        a.used or b.used,
    )


def infer_claude_5h_reset(
    usage_ts: Sequence[float],
    anchor: float | None,
    now: float,
) -> float | None:
    """
    Chained 5h window inference from sorted assistant usage timestamps `usage_ts`:
      - Starting at the first usage timestamp `t >= anchor` (or `t >= now - 5h` when `anchor` is None),
        opens a 5h window ending at `end = t - (t % 600) + 5h`.
      - If `end > now`, that window is currently active -> return `end`.
      - Otherwise advance to the first usage timestamp `>= end` and repeat.
    """
    import bisect

    pts = sorted(float(x) for x in usage_ts if x is not None)
    if not pts:
        return None
    start_ts = float(anchor) if anchor is not None else (float(now) - CLAUDE_5H_SECONDS)
    idx = bisect.bisect_left(pts, start_ts)
    while idx < len(pts):
        t = pts[idx]
        end = t - (t % CLAUDE_5H_ALIGN) + CLAUDE_5H_SECONDS
        if end > now:
            return end
        idx = bisect.bisect_left(pts, end, idx + 1)
    return None


def infer_claude_wk_reset(
    usage_ts: Sequence[float],
    anchor: float | None,
    now: float,
) -> float | None:
    """Roll weekly window forward in 7-day increments from `anchor` when usage exists in the current 7d span."""
    import bisect

    if anchor is None or now < anchor:
        return None
    pts = sorted(float(x) for x in usage_ts if x is not None)
    if not pts:
        return None
    steps = int((now - anchor) // CLAUDE_WK_SECONDS) + 1
    end = float(anchor) + steps * CLAUDE_WK_SECONDS
    win_start = end - CLAUDE_WK_SECONDS
    idx = bisect.bisect_left(pts, win_start)
    return end if idx < len(pts) and pts[idx] <= now + 60.0 else None


def scan_claude_usage_lines(buf: bytes | str, since: float = 0.0) -> list[float]:
    """
    Extract assistant turn timestamps (`>= since`) with `output_tokens > 0` from a JSONL tail buffer.
    Filters out:
      - `<synthetic>` error/limit messages (`output_tokens == 0` or `model == "<synthetic>"`)
      - `isSidechain` rows
      - Borrowed non-Anthropic brains (`deepseek`, `kimi`, `qwen`, `glm`)
      - Headless `sdk-cli` one-shot runs when marked on the row (`entrypoint == "sdk-cli"`)
    """
    raw_bytes = buf.encode("utf-8", errors="replace") if isinstance(buf, str) else buf
    out: list[float] = []
    for line in raw_bytes.split(b"\n"):
        if b'"assistant"' not in line or b'"output_tokens"' not in line:
            continue
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if not isinstance(o, dict) or o.get("type") != "assistant" or o.get("isSidechain"):
            continue
        if str(o.get("entrypoint") or "").lower() == "sdk-cli":
            continue
        msg = o.get("message")
        if not isinstance(msg, dict):
            continue
        model_id = str(msg.get("model") or o.get("model") or "").strip().lower()
        if model_id.startswith("<") or (model_id and "claude" not in model_id):
            continue
        usage = msg.get("usage")
        if not isinstance(usage, dict) or int(usage.get("output_tokens") or 0) <= 0:
            continue
        ts = _quota_epoch(o.get("timestamp"))
        if ts is not None and ts >= since:
            out.append(ts)
    return out


def _claude_file_sig(path: str) -> tuple[int, int, int, int] | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_mtime_ns, st.st_size, st.st_dev, st.st_ino)


def _claude_quota_sources(base_home: str | None = None, limit: int = 10) -> tuple:
    """
    Return top-`limit` newest human Claude `.jsonl` transcript signatures `(path, (mtime_ns, size, dev, ino))`
    under `<base_home>/.claude/projects/*/*.jsonl` so `get_cli_quota_metrics` invalidates its cache immediately
    on any new Claude message without waiting for the 3s cache TTL.
    """
    eff_home = os.path.expanduser(base_home or "~")
    proj_root = os.path.join(eff_home, ".claude", "projects")
    if not os.path.isdir(proj_root):
        return ()
    sources: list[tuple[str, tuple[int, int, int, int]]] = []
    try:
        with os.scandir(proj_root) as proj_it:
            for d in proj_it:
                if not d.is_dir(follow_symlinks=False):
                    continue
                try:
                    with os.scandir(d.path) as file_it:
                        for f in file_it:
                            if not f.name.endswith(".jsonl") or not f.is_file(follow_symlinks=False):
                                continue
                            sig = _claude_file_sig(f.path)
                            if sig is not None:
                                sources.append((f.path, sig))
                except OSError:
                    continue
    except OSError:
        return ()
    sources.sort(key=lambda item: (item[1][0], item[0]), reverse=True)
    return tuple(sources[:limit])


_CLAUDE_USAGE_TAIL_CACHE: dict[str, tuple[tuple[int, int, int, int], list[float], bool]] = {}
_CLAUDE_LIMIT_HIT_CACHE: dict[str, tuple[tuple[int, int, int, int], dict[str, float]]] = {}
_CLAUDE_LIMIT_KIND_RE = re.compile(r"hit your (session|weekly) limit", re.I)


def latest_claude_limit_hits(
    sources: Sequence[tuple[str, tuple[int, int, int, int]]], tail_bytes: int = 65536
) -> dict[str, float]:
    """
    Newest "You've hit your session/weekly limit" timestamps in the tails of the newest Claude transcripts.
    Claude Code writes these as synthetic `error: rate_limit` entries; after one, the statusline stops refreshing,
    so the last quota it reported (e.g. 98% used) would otherwise stay on screen.
    """
    hits: dict[str, float] = {}
    for path, sig in sources:
        cached = _CLAUDE_LIMIT_HIT_CACHE.get(path)
        if cached is not None and cached[0] == sig:
            found = cached[1]
        else:
            found = {}
            try:
                with open(path, "rb") as fh:
                    fh.seek(max(0, sig[1] - tail_bytes))
                    chunk = fh.read().decode("utf-8", errors="ignore")
            except OSError:
                chunk = ""
            for line in chunk.splitlines():
                if '"rate_limit"' not in line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("error") != "rate_limit":
                    continue
                content = (row.get("message") or {}).get("content") or []
                text = " ".join(b.get("text", "") for b in content if isinstance(b, dict))
                m_kind = _CLAUDE_LIMIT_KIND_RE.search(text)
                ts = _quota_epoch(row.get("timestamp"))
                if m_kind and ts:
                    kind = m_kind.group(1).lower()
                    found[kind] = max(found.get(kind, 0.0), ts)
            _CLAUDE_LIMIT_HIT_CACHE[path] = (sig, found)
        for kind, ts in found.items():
            hits[kind] = max(hits.get(kind, 0.0), ts)
    return hits


def collect_recent_claude_usage_timestamps(
    base_home: str | None = None,
    *,
    since: float = 0.0,
    sources: Sequence[tuple[str, tuple[int, int, int, int]]] | None = None,
    tail_bytes: int = 131072,
) -> list[float]:
    """
    Read the tail (`tail_bytes`) of the newest Claude `.jsonl` transcripts (cached by `(mtime_ns, size, dev, ino)`)
    and return sorted assistant usage timestamps `>= since` from interactive human sessions.
    """
    src_list = _claude_quota_sources(base_home) if sources is None else sources
    all_ts: list[float] = []
    for path, sig in src_list:
        cached = _CLAUDE_USAGE_TAIL_CACHE.get(path)
        if cached is not None and cached[0] == sig:
            ts_list, is_headless = cached[1], cached[2]
        else:
            ts_list = []
            is_headless = False
            try:
                sz = sig[1]
                with open(path, "rb") as fh:
                    if sz > tail_bytes:
                        fh.seek(0)
                        head_chunk = fh.read(8192)
                        fh.seek(max(0, sz - tail_bytes))
                        tail_chunk = fh.read(tail_bytes)
                        nl = tail_chunk.find(b"\n")
                        if nl >= 0:
                            tail_chunk = tail_chunk[nl + 1 :]
                    else:
                        head_chunk = tail_chunk = fh.read()
                # Check if session is a pure headless `claude -p` (`sdk-cli`) with no human turn
                if b'"sdk-cli"' in head_chunk and b'"type":"user"' not in head_chunk and b'"type": "user"' not in head_chunk:
                    is_headless = True
                else:
                    ts_list = scan_claude_usage_lines(tail_chunk, since=0.0)
                if len(_CLAUDE_USAGE_TAIL_CACHE) > 64:
                    _CLAUDE_USAGE_TAIL_CACHE.clear()
                _CLAUDE_USAGE_TAIL_CACHE[path] = (sig, ts_list, is_headless)
            except OSError:
                continue
        if not is_headless:
            all_ts.extend(t for t in ts_list if t >= since)
    all_ts.sort()
    return all_ts


def _resolve_claude_quota(
    now: float,
    sl_5h: QuotaObs | None = None,
    sl_wk: QuotaObs | None = None,
    cj_5h: QuotaObs | None = None,
    cj_wk: QuotaObs | None = None,
    usage_ts: Sequence[float] = (),
    ttl_5h: float = 6 * 3600.0,
    ttl_wk: float = 7 * 86400.0,
) -> dict[str, Any]:
    """
    Pure resolution of Claude 5h and 7d (`wk`) quota state:
      - Case A (Confirmed Active Window: `now < reset <= now + span + 90s` and fresh observation):
        * `rem_5h` / `rem_wk` preserves exact integer remaining % (`100` when `used_percentage == 0`, so UI
          renders `0% 22:10` rather than artificially inflating `0%` to `1%`).
        * `started_5h` is `True` when `o.used` is True (e.g. from `statusline.sh` `rate_limits.five_hour`,
          or `cachedUsageUtilization` with `is_active`/`utilization > 0`) OR `rem < 100` OR `usage_ts` has
          activity inside `[reset - 5h, now]`.
      - Case B (Post-Reset `.jsonl` Live Fallback: cached `reset <= now` expired or missing, and `usage_ts`
        has assistant token activity after `anchor` within the last 5h):
        * Infers `reset = infer_claude_5h_reset(usage_ts, anchor, now)` (`floor10 + 5h`).
        * Sets `rem_5h = 100` (`0%` used), `started_5h = True`, `est_5h = True`, `state_5h = "fresh"`.
      - Case C (Unstarted / Expired with zero post-reset activity):
        * `rem_5h = None` (when expired/missing) or `100` with `started_5h = False` -> UI renders `·`.
    """
    import bisect

    pts = sorted(float(x) for x in usage_ts if x is not None)
    out: dict[str, Any] = {}
    for tag, sl, cj, span, ttl, infer in (
        ("5h", sl_5h, cj_5h, float(CLAUDE_5H_SECONDS), float(ttl_5h), infer_claude_5h_reset),
        ("wk", sl_wk, cj_wk, float(CLAUDE_WK_SECONDS), float(ttl_wk), infer_claude_wk_reset),
    ):
        o = merge_quota_obs(cj, sl)
        rem: int | None = None
        reset: float | None = None
        started = False
        est = False
        state = "none"

        if o is not None and o.reset is not None:
            if o.seen is not None and o.seen < o.reset - span:
                # Observation predates the window itself
                o = None
            elif now < o.reset <= now + span + CLAUDE_SAME_WIN_TOL:
                eff_seen = o.seen
                if tag == "wk" and eff_seen is not None and eff_seen >= o.reset - span:
                    # 7-day subscription window remains authoritative until resets_at
                    eff_seen = max(eff_seen, now - 3600.0)
                if eff_seen is not None and (now - eff_seen) <= ttl:
                    rem = 100 if o.rem is None else _rem_pct_from_fraction(o.rem)
                    win_start = o.reset - span
                    has_win_usage = bisect.bisect_left(pts, win_start) < len(pts)
                    started = bool(o.used or (rem is not None and rem < 100) or has_win_usage)
                    reset = o.reset
                    state = "fresh"
                else:
                    state = "stale"
            elif now >= o.reset:
                state = "expired"

        if state != "fresh":
            anchor = o.reset if (o is not None and o.reset is not None and o.reset <= now) else None
            if tag == "5h":
                floor_ts = max(
                    float(anchor or 0.0),
                    float((o.seen if o is not None else None) or 0.0),
                    float(now) - float(CLAUDE_5H_SECONDS),
                )
                recent_pts = [t for t in pts if t >= floor_ts]
                end = infer(recent_pts, anchor if (anchor is not None and anchor >= now - 2 * CLAUDE_5H_SECONDS) else None, now)
            else:
                end = infer(pts, anchor, now) if anchor is not None else None
            if end is not None and (o is not None or anchor is not None or bool(pts)):
                rem = 100
                reset = end
                started = True
                est = True
                state = "fresh"
            elif o is not None and o.reset is None and o.rem is not None and o.seen is not None:
                no_rst_ttl = min(ttl, 1800.0 if tag == "5h" else 21600.0)
                if (now - o.seen) <= no_rst_ttl:
                    rem = _rem_pct_from_fraction(o.rem)
                    started = bool(rem is not None and rem < 100)
                    state = "fresh"
                else:
                    state = "stale"

        out[f"rem_{tag}"] = rem
        out[f"reset_{tag}"] = reset
        out[f"started_{tag}"] = started
        out[f"est_{tag}"] = est
        out[f"state_{tag}"] = state
    return out
