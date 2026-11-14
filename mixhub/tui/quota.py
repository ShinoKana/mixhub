#!/usr/bin/env python3
"""`mixhub.tui.quota`: CLI quota remaining metrics (Claude / Codex / Gemini), reset tags, window freshness
judgment, and the 1-line quota summary.
"""
from __future__ import annotations

from datetime import datetime
import json
import os
import time

from mixhub.core.textwidth import disp_trunc, disp_width

_QUOTA_SUMMARY_CACHE: dict[str, object] = {"ts": 0.0, "key": "", "val": ""}


QUOTA_WIN_SECONDS = {"5h": 5 * 3600, "wk": 7 * 86400}
QUOTA_TTL_SECONDS = {"5h": 6 * 3600, "wk": 24 * 3600}
QUOTA_NO_RESET_TTL_SECONDS = {"5h": 30 * 60, "wk": 3 * 3600}


def _parse_quota_ts(v: object) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        fv = float(v)
        return fv if fv > 0 else None
    s = str(v).strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def format_quota_reset_tag(reset_at: object, kind: str = "5h") -> str:
    """Format 5h reset timestamp as local `HH:MM` (e.g. `03:00`) and weekly reset as local `MM/DD` (e.g. `10/30`)."""
    ts = _parse_quota_ts(reset_at)
    if ts is None:
        return ""
    try:
        dt = datetime.fromtimestamp(ts)
        return dt.strftime("%H:%M" if kind == "5h" else "%m/%d")
    except Exception:
        return ""


def judge_quota_window(
    remaining_pct: float | int | None,
    reset_at: object = None,
    observed_at: object = None,
    kind: str = "5h",
    now: float | None = None,
) -> tuple[int | None, str, str]:
    """
    Three-tier authenticity & stale-eviction guard for CLI quota windows (`5h` / `wk`):
      1. Window Rollover (`reset_at`):
         - If `observed_at < reset_at - win_sec`, record is from an older window -> `(None, "", "none")`.
         - If `now >= reset_at`, the expiration time (`HH:MM` or `MM/DD`) has already passed -> `(None, "", "expired")`
           so expired percentages/timestamps are never displayed as active.
      2. Freshness TTL (`QUOTA_TTL_SECONDS`, overrideable via `MIXHUB_QUOTA_TTL_SEC`):
         - If `now - observed_at > ttl`, returns `(None, "", "stale")` so stale numbers are never shown.
      3. Valid Fresh Observation:
         - Returns `(clamped_int_0_100, reset_tag, "fresh")`.
    """
    wall_now = float(now if now is not None else time.time())
    obs_ts = _parse_quota_ts(observed_at)
    rst_ts = _parse_quota_ts(reset_at)
    if remaining_pct is None or obs_ts is None:
        return None, "", "none"
    win_sec = QUOTA_WIN_SECONDS.get(kind, 5 * 3600)
    env_ttl = os.environ.get("MIXHUB_QUOTA_TTL_SEC")
    if env_ttl and env_ttl.strip().lstrip("-").isdigit():
        ttl = float(env_ttl.strip())
    elif rst_ts is not None:
        ttl = float(QUOTA_TTL_SECONDS.get(kind, 6 * 3600))
    else:
        ttl = float(QUOTA_NO_RESET_TTL_SECONDS.get(kind, 1800))
    if rst_ts is not None:
        if obs_ts < rst_ts - win_sec:
            return None, "", "none"
        if wall_now >= rst_ts:
            return None, "", "expired"
    if wall_now - obs_ts > ttl:
        return None, "", "stale"
    raw_pct = float(remaining_pct)
    pct = max(0, min(100, round(raw_pct)))
    if raw_pct < 100.0:
        pct = min(99, pct)
    tag = format_quota_reset_tag(rst_ts, kind) if rst_ts is not None else ""
    return pct, tag, "fresh"


def _quota_file_signature(path: str) -> tuple | None:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size, st.st_dev, st.st_ino


def _codex_quota_sources(base_home: str) -> tuple:
    import glob as _glob

    pattern = os.path.join(base_home, ".codex", "sessions", "*", "*", "*", "rollout-*.jsonl")
    sources = []
    for path in _glob.iglob(pattern):
        sig = _quota_file_signature(path)
        if sig is not None:
            sources.append((path, sig))
    sources.sort(key=lambda item: (item[1][0], item[0]), reverse=True)
    return tuple(sources[:10])


def _prefer_quota_observation(candidate: tuple, best: tuple | None) -> bool:
    if best is None:
        return True
    if abs(candidate[0] - best[0]) < 600:
        # Same rolling window (absorbs ±seconds resets_at header jitter): newest observation wins, then higher used%.
        return (candidate[2], candidate[1]) > (best[2], best[1])
    return (candidate[0], candidate[2]) > (best[0], best[2])


def get_cli_quota_metrics(
    statusline_path: str | None = None,
    home_dir: str | None = None,
) -> list[dict]:
    """
    Return structured real quota remaining metrics + local expiration tags (`HH:MM` for 5h, `MM/DD` for wk):
      `[{"key": "cc", "rem_5h": 89, "rem_wk": 63, "reset_5h": "03:00", "reset_wk": "10/30"}, ...]`
    Applies `judge_quota_window` so expired (`now >= reset_time`) or stale (`now - observed_at > ttl`) windows
    never display false/outdated percentages, and tools with no valid windows are automatically omitted.
    """
    now = time.monotonic()
    wall_now = time.time()
    base_home = home_dir or os.path.expanduser("~")
    is_real_home = os.path.abspath(base_home) == os.path.abspath(os.path.expanduser("~"))
    is_custom_home = (
        home_dir is not None
        and statusline_path is None
        and not os.environ.get("MIXHUB_STATUSLINE_JSON")
        and not is_real_home
    )
    from mixhub.providers import statusline as _statusline

    # `mixhub statusline` snapshots live in the state directory; `/tmp/*-statusline.json` are older hand-rolled ones.
    state_agy, state_claude = _statusline.agy_statusline_path(), _statusline.claude_quota_path()
    st_path = (
        statusline_path
        or os.environ.get("MIXHUB_STATUSLINE_JSON")
        or (os.path.join(base_home, "agy-statusline.json") if is_custom_home else
            state_agy if os.path.isfile(state_agy) else "/tmp/agy-statusline.json")
    )
    env_ttl = os.environ.get("MIXHUB_QUOTA_TTL_SEC", "")
    quota_paths = [st_path, os.path.join(base_home, ".claude.json"), state_claude]
    if statusline_path is None and not os.environ.get("MIXHUB_STATUSLINE_JSON") and not is_custom_home:
        quota_paths += ["/tmp/claude-statusline.json", "/tmp/agy-quota-shared.json"]
    quota_sigs = tuple((path, _quota_file_signature(path)) for path in quota_paths)
    fast_key = ("metrics", st_path, base_home, env_ttl, quota_sigs)
    if (
        is_real_home
        and now - float(_QUOTA_SUMMARY_CACHE.get("ts") or 0.0) < 3.0
        and isinstance(_QUOTA_SUMMARY_CACHE.get("items"), list)
        and (
            _QUOTA_SUMMARY_CACHE.get("fast_key") == fast_key
            or (
                _QUOTA_SUMMARY_CACHE.get("base_key") == (st_path, base_home, env_ttl)
                and now - float(_QUOTA_SUMMARY_CACHE.get("ts") or 0.0) < 1.0
            )
        )
    ):
        # Real home: skip the `rollout-*.jsonl` glob + `~/.claude/projects` scan on every 0.18s animation frame.
        # The sidebar's kqueue watches on the Codex rollouts reset `ts` on change; Claude transcripts ride the 3s TTL.
        # Custom homes (tests) always re-verify sources so an in-place `.jsonl` append busts the cache immediately.
        return list(_QUOTA_SUMMARY_CACHE["items"])  # type: ignore[arg-type]
    if (
        is_real_home
        and _QUOTA_SUMMARY_CACHE.get("sources_home") == base_home
        and now - float(_QUOTA_SUMMARY_CACHE.get("ts") or 0.0) < 3.0
        and isinstance(_QUOTA_SUMMARY_CACHE.get("codex_sources_tuple"), tuple)
        and isinstance(_QUOTA_SUMMARY_CACHE.get("claude_sources_tuple"), tuple)
    ):
        codex_sources = _QUOTA_SUMMARY_CACHE["codex_sources_tuple"]
        claude_sources = _QUOTA_SUMMARY_CACHE["claude_sources_tuple"]
        from mixhub.providers import quota as _prov
    else:
        codex_sources = _codex_quota_sources(base_home)
        try:
            from mixhub.providers import quota as _prov

            claude_sources = _prov._claude_quota_sources(base_home)
        except Exception:
            _prov = None  # type: ignore[assignment]
            claude_sources = ()
    cache_key = (*fast_key, codex_sources, claude_sources)
    if (
        now - float(_QUOTA_SUMMARY_CACHE.get("ts") or 0.0) < 3.0
        and _QUOTA_SUMMARY_CACHE.get("key") == cache_key
        and isinstance(_QUOTA_SUMMARY_CACHE.get("items"), list)
    ):
        return list(_QUOTA_SUMMARY_CACHE["items"])  # type: ignore[arg-type]

    items: list[dict] = []
    try:
        quota: dict = {}
        obs_by_key: dict[str, float] = {}
        shared_p = "/tmp/agy-quota-shared.json"
        extra_paths = [state_claude]
        if statusline_path is None and not os.environ.get("MIXHUB_STATUSLINE_JSON") and not is_custom_home:
            extra_paths += ["/tmp/claude-statusline.json", shared_p]
        for extra_p in extra_paths:
            if not os.path.isfile(extra_p):
                continue
            try:
                sh_mtime = os.path.getmtime(extra_p)
                with open(extra_p, "r", encoding="utf-8") as sf:
                    s_data = json.load(sf)
                raw_extra = (s_data.get("quota") or s_data) if isinstance(s_data, dict) else {}
                if isinstance(raw_extra, dict):
                    for k, v in raw_extra.items():
                        if isinstance(v, dict) and k not in quota:
                            quota[k] = v
                            obs_by_key[k] = float(v.get("observed_at") or sh_mtime)
            except Exception:
                pass
        if os.path.isfile(st_path):
            st_mtime = os.path.getmtime(st_path)
            with open(st_path, "r", encoding="utf-8") as f:
                st_data = json.load(f)
            raw_q = (st_data.get("quota") or st_data.get("quotas") or {}) if isinstance(st_data, dict) else {}
            if isinstance(raw_q, dict) and isinstance(raw_q.get("buckets"), dict):
                raw_q = raw_q["buckets"]
            if isinstance(raw_q, dict):
                for k, v in raw_q.items():
                    if isinstance(v, dict):
                        if k not in quota:
                            quota[k] = v
                        obs_by_key[k] = max(obs_by_key.get(k, 0.0), float(v.get("observed_at") or st_mtime))

        def _judge_bucket(b_key: str, kind: str) -> tuple[int | None, str, str]:
            b_dict = quota.get(b_key)
            if not isinstance(b_dict, dict):
                return None, "", "none"
            rf = b_dict.get("remaining_fraction")
            if rf is None and b_dict.get("remaining_percentage") is not None:
                rf = float(b_dict["remaining_percentage"]) / 100.0
            if rf is None:
                return None, "", "none"
            obs_ts = _parse_quota_ts(b_dict.get("observed_at") or obs_by_key.get(b_key))
            rst_raw = b_dict.get("reset_time") or b_dict.get("resets_at")
            rst_ts = _parse_quota_ts(rst_raw)
            # Weekly observations remain authoritative within their active reset window,
            # matching cachedUsageUtilization even after >24h without a CLI turn.
            eff_obs = (
                max(obs_ts, wall_now - 3600.0)
                if (kind == "wk" and obs_ts is not None and rst_ts is not None
                    and obs_ts >= rst_ts - QUOTA_WIN_SECONDS["wk"] and wall_now < rst_ts)
                else obs_ts
            )
            return judge_quota_window(
                float(rf) * 100.0,
                rst_raw,
                eff_obs,
                kind=kind,
                now=wall_now,
            )

        cc_5h, t_cc_5h, st_cc_5h = None, "", "none"
        cc_wk, t_cc_wk, st_cc_wk = None, "", "none"
        cc_obs = 0.0
        cj_rst_5h: float | None = None
        cj_rst_wk: float | None = None
        cj_rem_5h_frac: float | None = None
        cj_rem_wk_frac: float | None = None
        cj_session_active: bool | None = None
        claude_cfg_p = os.path.join(base_home, ".claude.json")
        if os.path.isfile(claude_cfg_p):
            try:
                cc_mtime = os.path.getmtime(claude_cfg_p)
                with open(claude_cfg_p, "r", encoding="utf-8") as cf:
                    cc_cfg = json.load(cf)
                cu = (cc_cfg.get("cachedUsageUtilization") or {}) if isinstance(cc_cfg, dict) else {}
                util = (cu.get("utilization") or {}) if isinstance(cu, dict) else {}
                fetched_ms = cu.get("fetchedAtMs")
                cc_obs = (float(fetched_ms) / 1000.0) if isinstance(fetched_ms, (int, float)) and fetched_ms > 0 else cc_mtime
                for lim in util.get("limits") or []:
                    if isinstance(lim, dict) and lim.get("kind") == "session" and "is_active" in lim:
                        cj_session_active = bool(lim.get("is_active"))
                        break
                u5 = util.get("five_hour")
                if isinstance(u5, dict) and u5.get("utilization") is not None:
                    rem5 = max(0.0, min(100.0, 100.0 - float(u5["utilization"])))
                    cj_rem_5h_frac = rem5 / 100.0
                    cj_rst_5h = _parse_quota_ts(u5.get("resets_at") or u5.get("reset_time"))
                    cc_5h, t_cc_5h, st_cc_5h = judge_quota_window(
                        rem5,
                        u5.get("resets_at") or u5.get("reset_time"),
                        cc_obs,
                        kind="5h",
                        now=wall_now,
                    )
                uwk = util.get("seven_day")
                if isinstance(uwk, dict) and uwk.get("utilization") is not None:
                    rem_w = max(0.0, min(100.0, 100.0 - float(uwk["utilization"])))
                    cj_rem_wk_frac = rem_w / 100.0
                    rst_w_raw = uwk.get("resets_at") or uwk.get("reset_time")
                    rst_w_ts = _parse_quota_ts(rst_w_raw)
                    cj_rst_wk = rst_w_ts
                    # Native Claude Code 7-day subscription window remains authoritative until `resets_at`
                    # even when the user pauses native `claude` CLI sessions for >24h to preserve quota.
                    eff_obs_w = (
                        max(cc_obs, wall_now - 3600.0)
                        if (rst_w_ts is not None and cc_obs >= rst_w_ts - QUOTA_WIN_SECONDS["wk"] and wall_now < rst_w_ts)
                        else cc_obs
                    )
                    cc_wk, t_cc_wk, st_cc_wk = judge_quota_window(
                        rem_w,
                        rst_w_raw,
                        eff_obs_w,
                        kind="wk",
                        now=wall_now,
                    )
            except Exception:
                pass
        has_native_cc_cfg = cc_5h is not None or cc_wk is not None
        # AGY's `3p-*` buckets are Antigravity's allowance for Claude models inside agy, not the Claude Code plan
        k_5h = "claude-5h" if "claude-5h" in quota else ""
        k_wk = "claude-weekly" if "claude-weekly" in quota else ""

        def _merge_cc_bucket(
            cached: tuple[int | None, str, str], cached_reset: float | None, b_key: str, kind: str,
        ) -> tuple[int | None, str, str]:
            bucket = _judge_bucket(b_key, kind)
            cc_rem, cc_tag, cc_state = cached
            q_rem, _, q_state = bucket
            if q_rem is None:
                return (cc_rem, cc_tag, q_state) if cc_state == "none" else cached
            if cc_rem is None:
                return bucket
            b_dict = quota[b_key]
            q_reset = _parse_quota_ts(b_dict.get("reset_time") or b_dict.get("resets_at"))
            q_obs = _parse_quota_ts(b_dict.get("observed_at")) or obs_by_key.get(b_key, 0.0)
            if cached_reset is not None and q_reset is not None:
                if abs(cached_reset - q_reset) < 600:
                    # Usage cannot decrease within one window, even if the lower-used
                    # cache has a newer mtime/fetchedAtMs. Keep the winning value's metadata.
                    if q_rem != cc_rem:
                        return bucket if q_rem < cc_rem else cached
                else:
                    return bucket if q_reset > cached_reset else cached
            # Equal remaining or incomparable resets: prefer the newer observation.
            return bucket if q_obs >= cc_obs else cached

        if k_5h:
            cc_5h, t_cc_5h, st_cc_5h = _merge_cc_bucket(
                (cc_5h, t_cc_5h, st_cc_5h), cj_rst_5h, k_5h, "5h",
            )
        if k_wk:
            cc_wk, t_cc_wk, st_cc_wk = _merge_cc_bucket(
                (cc_wk, t_cc_wk, st_cc_wk), cj_rst_wk, k_wk, "wk",
            )

        est_cc_5h = False
        est_cc_wk = False
        if _prov is not None and cc_5h is None and (has_native_cc_cfg or "claude-5h" in quota or cj_rst_5h is not None):
            try:
                sl_b5 = quota.get("claude-5h") if isinstance(quota.get("claude-5h"), dict) else None
                sl_obs_5h = None
                if sl_b5 is not None:
                    rf5 = sl_b5.get("remaining_fraction")
                    if rf5 is None and sl_b5.get("remaining_percentage") is not None:
                        rf5 = float(sl_b5["remaining_percentage"]) / 100.0
                    sl_obs_5h = _prov.QuotaObs(
                        None if rf5 is None else float(rf5),
                        _parse_quota_ts(sl_b5.get("reset_time") or sl_b5.get("resets_at")),
                        _parse_quota_ts(sl_b5.get("observed_at")) or obs_by_key.get("claude-5h"),
                        True,
                    )
                cj_obs_5h = (
                    _prov.QuotaObs(
                        cj_rem_5h_frac,
                        cj_rst_5h,
                        cc_obs if cc_obs > 0 else None,
                        bool(cj_session_active if cj_session_active is not None else (cj_rem_5h_frac is not None and cj_rem_5h_frac < 1.0)),
                    )
                    if (cj_rst_5h is not None or cj_rem_5h_frac is not None)
                    else None
                )
                usage_ts = _prov.collect_recent_claude_usage_timestamps(
                    base_home,
                    since=wall_now - 2 * QUOTA_WIN_SECONDS["5h"],
                    sources=claude_sources,
                )
                resolved_cc = _prov._resolve_claude_quota(
                    wall_now,
                    sl_5h=sl_obs_5h,
                    cj_5h=cj_obs_5h,
                    usage_ts=usage_ts,
                )
                if resolved_cc.get("started_5h") and resolved_cc.get("rem_5h") is not None:
                    cc_5h = int(resolved_cc["rem_5h"])
                    t_cc_5h = format_quota_reset_tag(resolved_cc.get("reset_5h"), "5h")
                    st_cc_5h = str(resolved_cc.get("state_5h") or "fresh")
                    est_cc_5h = bool(resolved_cc.get("est_5h"))
            except Exception:
                pass

        # A "hit your session/weekly limit" entry newer than the last statusline reading means that bucket is full
        if _prov is not None and claude_sources:
            try:
                limit_hits = _prov.latest_claude_limit_hits(claude_sources)
            except Exception:
                limit_hits = {}
            for hit_kind, b_key, win_kind in (("session", "claude-5h", "5h"), ("weekly", "claude-weekly", "wk")):
                hit_ts = limit_hits.get(hit_kind)
                b_dict = quota.get(b_key) if isinstance(quota.get(b_key), dict) else {}
                b_obs = _parse_quota_ts(b_dict.get("observed_at")) or obs_by_key.get(b_key, 0.0)
                b_reset = _parse_quota_ts(b_dict.get("reset_time") or b_dict.get("resets_at"))
                window = QUOTA_WIN_SECONDS[win_kind]
                if not hit_ts or hit_ts <= (b_obs or 0.0) or hit_ts < wall_now - window:
                    continue
                if b_reset is not None and (b_reset <= wall_now or hit_ts < b_reset - window):
                    continue
                if win_kind == "5h":
                    cc_5h = 0
                    t_cc_5h = t_cc_5h or format_quota_reset_tag(b_reset, "5h")
                    st_cc_5h = "fresh"
                else:
                    cc_wk = 0
                    t_cc_wk = t_cc_wk or format_quota_reset_tag(b_reset, "wk")
                    st_cc_wk = "fresh"

        # The weekly window rolls over at a fixed time (the 5h one only starts on first use, so it stays `·`):
        # once the reset passes with no status line redraw since, show the new week as unused
        if cc_wk is None and st_cc_wk == "expired":
            wk_dict = quota.get(k_wk) if isinstance(quota.get(k_wk), dict) else {}
            old_rst = _parse_quota_ts(wk_dict.get("reset_time") or wk_dict.get("resets_at"))
            if old_rst is not None:
                next_rst = old_rst
                while next_rst <= wall_now:
                    next_rst += QUOTA_WIN_SECONDS["wk"]
                cc_wk, t_cc_wk, st_cc_wk = 100, format_quota_reset_tag(next_rst, "wk"), "fresh"

        started_cc_5h = bool(
            st_cc_5h == "fresh"
            and bool(t_cc_5h)
            and (
                (cc_5h is not None and cc_5h < 100)
                or k_5h == "claude-5h"
                or est_cc_5h
                or cj_session_active is not False
                or bool((quota.get(k_5h) or {}).get("started"))
            )
        )
        started_cc_wk = bool(st_cc_wk == "fresh" and bool(t_cc_wk))
        if cc_5h is not None or cc_wk is not None:
            items.append({
                "key": "cc",
                "rem_5h": cc_5h,
                "rem_wk": cc_wk,
                "reset_5h": t_cc_5h,
                "reset_wk": t_cc_wk,
                "state_5h": st_cc_5h,
                "state_wk": st_cc_wk,
                "started_5h": started_cc_5h,
                "started_wk": started_cc_wk,
                "est_5h": est_cc_5h,
                "est_wk": est_cc_wk,
            })

        agy_5h, t_agy_5h, st_agy_5h = _judge_bucket("gemini-5h", "5h")
        agy_wk, t_agy_wk, st_agy_wk = _judge_bucket("gemini-weekly", "wk")
        if agy_5h is not None or agy_wk is not None:
            items.append({
                "key": "agy",
                "rem_5h": agy_5h,
                "rem_wk": agy_wk,
                "reset_5h": t_agy_5h,
                "reset_wk": t_agy_wk,
                "state_5h": st_agy_5h,
                "state_wk": st_agy_wk,
                "started_5h": bool(
                    st_agy_5h == "fresh"
                    and bool(t_agy_5h)
                    and ((agy_5h is not None and agy_5h < 100) or bool((quota.get("gemini-5h") or {}).get("started")))
                ),
                "started_wk": bool(
                    st_agy_wk == "fresh"
                    and bool(t_agy_wk)
                    and ((agy_wk is not None and agy_wk < 100) or bool((quota.get("gemini-weekly") or {}).get("started")))
                ),
            })
    except Exception:
        pass

    try:
        files = [path for path, _ in codex_sources]
        if files:
            best_pri: tuple[float, float, float] | None = None
            best_sec: tuple[float, float, float] | None = None
            latest_limit_err_ts = 0.0
            for p in files[:10]:
                try:
                    f_mtime = os.path.getmtime(p)
                    sz = os.path.getsize(p)
                    with open(p, "rb") as f:
                        f.seek(max(0, sz - 524288))
                        chunk = f.read().decode("utf-8", errors="ignore")
                    for line in reversed(chunk.splitlines()):
                        if '"usage_limit_exceeded"' in line:
                            try:
                                obj = json.loads(line)
                                ca = float((obj.get("payload") or {}).get("completed_at") or 0.0)
                                if ca > latest_limit_err_ts:
                                    latest_limit_err_ts = ca
                            except Exception:
                                pass
                        if '"rate_limits":' not in line:
                            continue
                        try:
                            obj = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        rl = ((obj.get("payload") or {}).get("rate_limits")) or obj.get("rate_limits")
                        if not isinstance(rl, dict):
                            continue
                        obs_ts = _parse_quota_ts(obj.get("timestamp")) or f_mtime
                        pri = rl.get("primary")
                        sec = rl.get("secondary")
                        if isinstance(pri, dict) and pri.get("used_percent") is not None:
                            cand_p = (float(pri.get("resets_at") or 0.0), float(pri["used_percent"]), float(obs_ts))
                            if _prefer_quota_observation(cand_p, best_pri):
                                best_pri = cand_p
                        if isinstance(sec, dict) and sec.get("used_percent") is not None:
                            cand_s = (float(sec.get("resets_at") or 0.0), float(sec["used_percent"]), float(obs_ts))
                            if _prefer_quota_observation(cand_s, best_sec):
                                best_sec = cand_s
                        if isinstance(pri, dict) and pri.get("used_percent") is not None:
                            break
                except Exception:
                    continue
            if best_pri is not None or best_sec is not None:
                cx_5h: int | None = None
                cx_wk: int | None = None
                t_cx_5h = ""
                t_cx_wk = ""
                st_cx_5h = "none"
                st_cx_wk = "none"
                if best_pri is not None:
                    ra, used_p, obs_p = best_pri
                    if ra > wall_now and latest_limit_err_ts > (ra - 18000) and latest_limit_err_ts >= obs_p:
                        used_p = 100.0
                        obs_p = latest_limit_err_ts
                    cx_5h, t_cx_5h, st_cx_5h = judge_quota_window(
                        100.0 - used_p, ra if ra > 0 else None, obs_p, kind="5h", now=wall_now
                    )
                if best_sec is not None:
                    ra_s, used_s, obs_s = best_sec
                    cx_wk, t_cx_wk, st_cx_wk = judge_quota_window(
                        100.0 - used_s, ra_s if ra_s > 0 else None, obs_s, kind="wk", now=wall_now
                    )
                if cx_5h is not None or cx_wk is not None:
                    items.append({
                        "key": "cx",
                        "rem_5h": cx_5h,
                        "rem_wk": cx_wk,
                        "reset_5h": t_cx_5h,
                        "reset_wk": t_cx_wk,
                        "state_5h": st_cx_5h,
                        "state_wk": st_cx_wk,
                        "started_5h": st_cx_5h == "fresh" and bool(t_cx_5h),
                        "started_wk": st_cx_wk == "fresh" and bool(t_cx_wk),
                    })
    except Exception:
        pass

    order_map = {"cc": 0, "cx": 1, "agy": 2}
    items.sort(key=lambda x: order_map.get(str(x.get("key", "")), 99))
    _QUOTA_SUMMARY_CACHE["codex_sources"] = (base_home, codex_sources)
    _QUOTA_SUMMARY_CACHE["sources_home"] = base_home
    _QUOTA_SUMMARY_CACHE["codex_sources_tuple"] = codex_sources
    _QUOTA_SUMMARY_CACHE["claude_sources_tuple"] = claude_sources
    _QUOTA_SUMMARY_CACHE["ts"] = now
    _QUOTA_SUMMARY_CACHE["key"] = cache_key
    _QUOTA_SUMMARY_CACHE["fast_key"] = fast_key
    _QUOTA_SUMMARY_CACHE["base_key"] = (st_path, base_home, env_ttl)
    _QUOTA_SUMMARY_CACHE["items"] = items
    return list(items)


def _battery_bar_2(pct: float) -> str:
    """Return a 2-cell language-free battery gauge (`▰▰`, `▰▱`, `▱▱`) for remaining quota percentage."""
    p = max(0.0, min(100.0, float(pct)))
    if p >= 65.0:
        return "▰▰"
    if p >= 25.0:
        return "▰▱"
    return "▱▱"


def get_cli_quota_summary(
    max_w: int = 30,
    statusline_path: str | None = None,
    home_dir: str | None = None,
) -> str:
    """
    Build a language-free 1-line quota summary with battery blocks in `cc -> cx -> agy` order
    (e.g. `cc 99% ▰▰ cx 90% ▰▰ agy 96% ▰▰`).
    When 5h remaining is 100% but weekly remaining is <100% (e.g. Codex after 5h window reset),
    displays the weekly remaining % so it never misleadingly shows 100%.
    """
    items = get_cli_quota_metrics(statusline_path=statusline_path, home_dir=home_dir)
    parts: list[str] = []
    compact_parts: list[str] = []
    for it in items:
        k = it.get("key", "")
        r5 = it.get("rem_5h")
        rw = it.get("rem_wk")
        eff = rw if (r5 == 100 and rw is not None and rw < 100) else (r5 if r5 is not None else rw)
        if eff is None:
            continue
        bot = min(int(r5 if r5 is not None else 100), int(rw if rw is not None else 100))
        bar = _battery_bar_2(bot)
        parts.append(f"{k} {eff}% {bar}")
        compact_parts.append(f"{k} {eff}%{bar}")
    if not parts:
        return ""
    text = " · ".join(parts)
    if disp_width(text) > max_w and len(parts) > 1:
        text = " ".join(parts)
    if disp_width(text) > max_w and len(compact_parts) > 1:
        text = " ".join(compact_parts)
    return disp_trunc(text, max_w)
