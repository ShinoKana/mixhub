#!/usr/bin/env python3
"""`mixhub.tui.widgets.quota_table`: Footer quota glyphs, Morandi quota color pairs, and Relay target choice by quota."""
from __future__ import annotations

from mixhub.tui.core.style import (
    P_AGENT_AGY,
    P_AGENT_CLAUDE,
    P_AGENT_CODEX,
    P_BAND_T1,
    P_BAND_T2,
    P_BAND_T3,
    P_BAND_T4,
    P_DIM,
    P_HOVER_DIM,
    P_STAT_T1,
    P_STAT_T2,
    P_STAT_T3,
    P_STAT_T4,
)


QUOTA_KEY_MAP = {"claude": "cc", "codex": "cx", "agy": "agy", "gemini": "agy"}
RELAY_CANDIDATE_ORDER = ("claude", "codex", "agy", "deepseek")

# (metrics key, display label, brand pair, x) — order: Claude -> Codex -> Gemini (no leading star/glyph)
_FOOTER_AGENTS = (
    ("cc", "claude", "✻", P_AGENT_CLAUDE, 2),
    ("cx", "codex", "◈", P_AGENT_CODEX, 12),
    ("agy", "gemini", "✦", P_AGENT_AGY, 22),
)


def _morandi_quota_pair(rem_pct: int | float | None) -> int:
    """
    Map remaining quota percentage (`0..100%`) to the 4-tier zero-bold Morandi palette
    matching `cpu` and `mem`:
      - >= 65% remaining -> P_STAT_T1 (Morandi sage moss green #87af87)
      - 40..64% remaining -> P_STAT_T2 (Morandi oat khaki #afaf87)
      - 15..39% remaining -> P_STAT_T3 (Morandi warm sand/camel #d7af87)
      - < 15% remaining -> P_STAT_T4 (Morandi dusty coral #d78787)
    """
    if rem_pct is None:
        return P_DIM
    p = float(rem_pct)
    if p >= 65.0:
        return P_STAT_T1
    if p >= 40.0:
        return P_STAT_T2
    if p >= 15.0:
        return P_STAT_T3
    return P_STAT_T4


def pick_relay_target(
    cur_tool: str,
    quota_items: list[dict] | dict | None,
    installed_tools: set[str] | list[str] | tuple[str, ...] | None = None,
) -> tuple[str | None, int | None, int | None]:
    """
    Return `(target_tool, cur_rem_wk, target_rem_wk)`:
      - `(None, None, None)` when current agent's weekly quota is healthy (`rem_wk is None or rem_wk >= 15`).
        Note: 5h quota NEVER triggers bottom-bar relay recommendation.
      - `(target_tool, cur_rem_wk, target_rem_wk)` when `cur_rem_wk < 15` (i.e. >85% weekly used, P_STAT_T4).
      - `("", cur_rem_wk, None)` when `cur_rem_wk < 15` but no healthy target is found (fallback to relay_menu).
    """
    norm_tool = "agy" if (cur_tool or "").lower() == "gemini" else (cur_tool or "").lower()
    by_key: dict[str, dict] = {}
    if isinstance(quota_items, dict):
        for k, v in quota_items.items():
            mapped_k = QUOTA_KEY_MAP.get(str(k).lower(), str(k).lower())
            if isinstance(v, dict):
                by_key[mapped_k] = v
            elif isinstance(v, (int, float)):
                by_key[mapped_k] = {"rem_wk": int(v)}
    else:
        for it in (quota_items or []):
            if isinstance(it, dict) and it.get("key"):
                k = str(it.get("key"))
                mapped_k = QUOTA_KEY_MAP.get(k.lower(), k.lower())
                by_key[mapped_k] = it

    cur_q = by_key.get(QUOTA_KEY_MAP.get(norm_tool, "")) or {}
    cur_wk = cur_q.get("rem_wk")
    if cur_wk is None or int(cur_wk) >= 15:
        return None, None, None

    if installed_tools is None:
        tools_set = {"claude", "codex", "agy"}
    else:
        tools_set = {"agy" if str(t).lower() == "gemini" else str(t).lower() for t in installed_tools}

    best_rank = None
    best_tool = None
    best_twk = None
    for idx, t in enumerate(RELAY_CANDIDATE_ORDER):
        if t == norm_tool or t not in tools_set or t not in QUOTA_KEY_MAP:
            continue
        q = by_key.get(QUOTA_KEY_MAP[t]) or {}
        twk, t5h = q.get("rem_wk"), q.get("rem_5h")
        if twk is None or int(twk) < 40:
            continue
        if t5h is not None and int(t5h) < 15:
            continue
        rank = (int(twk), 100 if t5h is None else int(t5h), -idx)
        if best_rank is None or rank > best_rank:
            best_rank = rank
            best_tool = t
            best_twk = int(twk)

    if best_tool:
        return best_tool, int(cur_wk), best_twk
    return "", int(cur_wk), None


