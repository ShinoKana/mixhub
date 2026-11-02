#!/usr/bin/env python3
"""`mixhub.tui.sidebar_rows`: Sidebar row cell layout (32-column grid), relative-age labels, and Agent filter tabs."""
from __future__ import annotations

import time

from mixhub.core.textwidth import disp_pad, disp_width
from mixhub.tui.badges import _agent_badge, WORKING_DIAMOND_FRAMES, WORKING_DIAMOND_PERIOD
from mixhub.tui.core.style import disp_fade_cells

SIDEBAR_FILTER_TABS = (
    ("all", "All"),
    ("claude", "Claude"),
    ("codex", "Codex"),
    ("agy", "Gemini"),
)

AGENT_TAB_SPECS = (
    ("claude", "Claude", "Cl"),
    ("codex", "Codex", "Cx"),
    ("agy", "Gemini", "Ge"),
    ("opencode", "OpenCode", "Oc"),
    ("deepseek", "DeepSeek", "Ds"),
)


def sidebar_filter_tab_ranges(
    start_x: int = 1,
    available_tools: set[str] | list[str] | tuple[str, ...] | None = None,
    max_x: int = 29,
) -> list[tuple[str, str, int, int]]:
    """
    Return `(tool_key, label, x_start, x_end)` for the top borderless Agent filter bar.
    - Dynamically filters to agents actually present on the user's machine when `available_tools` is given.
    - If only 1 agent is installed, shows only that agent (no redundant `All` tab).
    - If 4+ agents are present and full names exceed `max_x`, automatically compacts labels (`Cl`, `Ge`, `Cx`, `Ds`).
    """
    if available_tools is None:
        active_specs = [(k, full, short) for k, full, short in AGENT_TAB_SPECS if k in ("claude", "agy", "codex")]
    else:
        avail_set = {str(t).lower() for t in available_tools if t}
        active_specs = [(k, full, short) for k, full, short in AGENT_TAB_SPECS if k in avail_set]
        for extra in sorted(avail_set - {k for k, _, _ in AGENT_TAB_SPECS}):
            active_specs.append((extra, extra.capitalize()[:6], extra[:2].capitalize()))
        if not active_specs:
            active_specs = [(k, full, short) for k, full, short in AGENT_TAB_SPECS if k in ("claude", "agy", "codex")]

    if len(active_specs) == 1:
        k, full, _ = active_specs[0]
        return [(k, full, start_x, start_x + disp_width(full))]

    full_tabs = [("all", "All")] + [(k, full) for k, full, _ in active_specs]
    total_full_w = sum(disp_width(lbl) + 2 for _, lbl in full_tabs) - 2
    use_short = (start_x + total_full_w) > max_x
    tabs = [("all", "All")] + [(k, short if use_short else full) for k, full, short in active_specs]

    out = []
    cx = start_x
    for tkey, label in tabs:
        w = disp_width(label)
        if cx + w > max_x and out:
            break
        out.append((tkey, label, cx, cx + w))
        cx += w + 2
    return out


def rel_time(ts: float) -> str:
    if not ts:
        return ""
    diff = max(0, int(time.time() - ts))
    if diff < 60:
        return "1m"  # minute granularity: never a ticking `4s` / `16s` / `40s` stopwatch
    if diff < 3600:
        return f"{diff // 60}m"
    if diff < 86400:
        return f"{diff // 3600}h"
    if diff < 86400 * 99:
        return f"{diff // 86400}d"
    return f"{diff // (86400 * 7)}w"


_TITLE_CELL_CACHE: dict[tuple[str, int], tuple[str, list[tuple[str, int]], tuple[tuple[str, int, int], ...]]] = {}


def render_sidebar_row_cells(
    session: dict, left_w: int = 32, is_unread: bool = False, now_mono: float | None = None,
) -> dict:
    """Lay out a single-column badge, CJK title, and right-aligned 3-char age.

    Col 0..1 are blank, and the brand diamond badge is at col 2 (`WORKING_DIAMOND_FRAMES`
    when actively thinking, `◈` for cross-agent relay, `◇` for live idle, `·` for dormant;
    pinned sessions keep their true status symbol and are grouped under the `Pinned` section).
    Unread completed output highlights the title in bold white and the timestamp
    in warm yellow (`P_WAIT`), with no extra symbol after the timestamp.
    """
    tool = (session.get("tool") or "").lower()
    pinned = bool(session.get("pinned"))
    is_live = bool(session.get("isLive"))
    state = (session.get("agentState") or ("working" if is_live else "dormant")) if is_live else "dormant"
    dot_ch, agent_pair, agent_sel_pair, agent_hov_pair, is_bold = _agent_badge(tool, state, pinned=pinned)
    origin_pair = agent_pair
    is_working = state == "working"
    if is_working and now_mono is not None:
        dot_ch = WORKING_DIAMOND_FRAMES[int(now_mono / WORKING_DIAMOND_PERIOD) % len(WORKING_DIAMOND_FRAMES)]
    dot2_ch = " "
    title_cols = max(4, left_w - 11)
    raw_title = session.get("title") or ""
    t_key = (raw_title, title_cols)
    t_cached = _TITLE_CELL_CACHE.get(t_key)
    if t_cached is None:
        if len(_TITLE_CELL_CACHE) > 2048:
            _TITLE_CELL_CACHE.clear()
        f_cells = disp_fade_cells(raw_title, title_cols)
        if any(step > 0 for _, step in f_cells):
            pos_list = []
            cx = 5
            for seg_txt, fade_step in f_cells:
                if seg_txt:
                    pos_list.append((seg_txt, fade_step, cx))
                    cx += disp_width(seg_txt)
            f_pos = tuple(pos_list)
        else:
            f_pos = ()
        t_cached = (disp_pad(raw_title, title_cols), f_cells, f_pos)
        _TITLE_CELL_CACHE[t_key] = t_cached
    t_cell, t_fade_cells, t_fade_pos = t_cached
    age = rel_time(session.get("updatedAt", 0))[-3:]
    age_col = age.rjust(3)
    show_dot = bool(is_unread and not is_working)
    unread_ch = " "
    full_str = f"  {dot_ch}{dot2_ch} {t_cell} {age_col}{unread_ch} "
    return {
        "dot_ch": dot_ch,
        "dot_x": 2,
        "dot_pair": agent_pair,
        "dot_sel_pair": agent_sel_pair,
        "dot_hov_pair": agent_hov_pair,
        "origin_pair": origin_pair,
        "dot2_ch": dot2_ch,
        "dot2_x": 3,
        "dot2_pair": agent_pair,
        "dot2_sel_pair": agent_sel_pair,
        "dot2_hov_pair": agent_hov_pair,
        "is_bold": is_bold,
        "is_working": is_working,
        "agent_ch": dot_ch,
        "agent_pair": agent_pair,
        "agent_sel_pair": agent_sel_pair,
        "agent_hov_pair": agent_hov_pair,
        "agent_x": 2,
        "title_x": 5,
        "title_cols": title_cols,
        "t_cell": t_cell,
        "t_fade_cells": t_fade_cells,
        "t_fade_pos": t_fade_pos,
        "age_col": age_col,
        "age_x": 5 + title_cols + 1,
        "unread_ch": unread_ch,
        "unread_x": 5 + title_cols + 4,
        "show_dot": show_dot,
        "full_str": full_str,
        "disp_w": title_cols + 11,
    }


def layout_sidebar_row_32(
    session: dict, left_w: int = 32, is_unread: bool = False, now_mono: float | None = None,
) -> dict:
    """Symmetric 2-col margin layout for the 32-col sidebar (inner width 31, cols 0..30).

    `cols 0..1` blank, icon at `col 2`, title from `col 5` with `title_cols = max(4, left_w - 11)`
    (21 cols -> `cols 5..25` at `left_w=32`), 1 blank (`col 26`), 3-col age at
    `age_x = 5 + title_cols + 1` (`cols 27..29`, right edge at `left_w - 3`, aligned with the
    top-right `◧` toggle and the quota table's right edge), and a 1-col right margin (`col 30`).
    `render_sidebar_row_cells` keeps its legacy 21-col geometry (`age_x = 27`) unchanged.
    """
    cells = render_sidebar_row_cells(session, left_w=left_w, is_unread=is_unread, now_mono=now_mono)
    cells["full_str"] = f"  {cells['dot_ch']}{cells['dot2_ch']} {cells['t_cell']} {cells['age_col']} "
    cells["disp_w"] = cells["title_cols"] + 10
    return cells
