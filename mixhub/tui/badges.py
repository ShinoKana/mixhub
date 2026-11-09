#!/usr/bin/env python3
"""`mixhub.tui.badges`: Agent brand glyphs and color pairs, status icons, working-diamond frames, and the sidebar
timestamp color table.
"""
from __future__ import annotations

from mixhub.tui.core.style import (
    P_AGENT_AGY,
    P_AGENT_AGY_HOV,
    P_AGENT_AGY_SEL,
    P_AGENT_CLAUDE,
    P_AGENT_CLAUDE_HOV,
    P_AGENT_CLAUDE_SEL,
    P_AGENT_CODEX,
    P_AGENT_CODEX_HOV,
    P_AGENT_CODEX_SEL,
    P_AGENT_OPENCODE,
    P_AGENT_OPENCODE_HOV,
    P_AGENT_OPENCODE_SEL,
    P_DIM,
    P_DONE,
    P_HOVER,
    P_LIVE,
    P_META,
    P_STAT_T1,
    P_STAT_T3,
    P_STAT_T4,
    P_USER_BOX,
    P_WAIT,
)

# 5-state zero-bold Morandi timestamp color table (`(unselected_pair, selected_pair)`, always `bg = -1`, zero bold):
#   1) wait_input: 180 warm sand yellow (P_STAT_T3), stays 180 when selected
#   2) working:    67 steel blue (P_DONE), stays 67 when selected
#   3) failed:     174 terracotta coral (P_STAT_T4), settles to 244 (P_META) once selected/seen
#   4) unread:     108 sage moss green (P_STAT_T1), settles to 244 (P_META) once selected/seen
#   5) seen:       240 dim gray (P_DIM) unselected, 244 (P_META) when selected
STATE_AGE_COLOR: dict[str, tuple[int, int]] = {
    "wait_input": (P_STAT_T3, P_STAT_T3),
    "working": (P_DONE, P_DONE),
    "failed": (P_STAT_T4, P_META),
    "unread": (P_STAT_T1, P_META),
    "seen": (P_DIM, P_META),
}


def resolve_sidebar_age_pair(row_state: str, is_selected: bool = False) -> int:
    """Return the Morandi color pair (`bg = -1`, zero bold) for a sidebar row timestamp from `STATE_AGE_COLOR`."""
    unsel_p, sel_p = STATE_AGE_COLOR.get(row_state, STATE_AGE_COLOR["seen"])
    return sel_p if is_selected else unsel_p


AGENT_DISPLAY_NAMES = {
    "claude": "Claude",
    "codex": "Codex",
    "agy": "Gemini",
    "gemini": "Gemini",
    "deepseek": "DeepSeek",
    "opencode": "OpenCode",
}
WORKING_DIAMOND_FRAMES = ("◇", "◈")
WORKING_DIAMOND_PERIOD = 0.45
SPINNER_FRAMES = WORKING_DIAMOND_FRAMES


def status_icon_and_pair(session: dict) -> tuple[str, int, bool]:
    """
    Orthogonal Shape x Color encoding:
      - Shape: Pinned (★ live / ☆ dormant) vs Unpinned (◆ live / ◇ dormant)
      - Color: working = P_LIVE (114 green), wait_input = P_WAIT (214 amber bold),
               done_idle = P_DONE (67 steel blue), dormant = P_DIM (242 gray)
    Returns `(symbol, color_pair_id, is_bold)`.
    """
    pinned = bool(session.get("pinned"))
    is_live = bool(session.get("isLive"))
    state = session.get("agentState") or ("working" if is_live else "dormant")
    if not is_live or state == "dormant":
        return ("☆" if pinned else "◇"), P_DIM, False
    sym = "★" if pinned else "◆"
    if state == "working":
        return sym, P_LIVE, True
    if state in ("wait_input", "wait"):
        return sym, P_WAIT, True
    return sym, P_DONE, False


def _agent_badge(tool: str, status: str = "dormant", pinned: bool = False) -> tuple[str, int, int, int, bool]:
    """
    Single-column 'State Shape x Official Brand Color' encoding (zero fake company logos):
      - Brand color:
        Claude (`claude`)       = pink 174 (P_AGENT_CLAUDE)
        Gemini (`agy`/`gemini`) = cornflower-blue 75 (P_AGENT_AGY)
        Codex (`codex`)         = pale lavender-white 189 (P_AGENT_CODEX)
      - Shape (kept identical whether pinned or unpinned; pinned rows live in the `Pinned` section):
        Live (working/wait/done)= '◇' (hollow diamond)
        Dormant                 = '·' (subtle middle dot)
    Returns `(glyph, normal_pair, selected_pair, hover_pair, is_bold)`.
    """
    t = (tool or "").lower()
    if t == "claude":
        norm_p, sel_p, hov_p = P_AGENT_CLAUDE, P_AGENT_CLAUDE_SEL, P_AGENT_CLAUDE_HOV
    elif t in ("agy", "gemini"):
        norm_p, sel_p, hov_p = P_AGENT_AGY, P_AGENT_AGY_SEL, P_AGENT_AGY_HOV
    elif t == "codex":
        norm_p, sel_p, hov_p = P_AGENT_CODEX, P_AGENT_CODEX_SEL, P_AGENT_CODEX_HOV
    elif t == "opencode":
        norm_p, sel_p, hov_p = P_AGENT_OPENCODE, P_AGENT_OPENCODE_SEL, P_AGENT_OPENCODE_HOV
    else:
        norm_p, sel_p, hov_p = P_META, P_USER_BOX, P_HOVER

    is_live = status in ("working", "wait_input", "wait", "done_idle")
    is_bold = bool(pinned or status in ("working", "wait_input", "wait"))
    if is_live:
        glyph = "◇"
    else:
        glyph = "·"
    return glyph, norm_p, sel_p, hov_p, is_bold


def agent_badge_and_pair(session: dict) -> tuple[str, int, int]:
    """
    Return `(glyph, normal_pair_id, selected_pair_id)` for `session` using `_agent_badge()`.
    """
    tool = (session.get("tool") or "").lower()
    pinned = bool(session.get("pinned"))
    is_live = bool(session.get("isLive"))
    state = (session.get("agentState") or ("working" if is_live else "dormant")) if is_live else "dormant"
    glyph, norm_p, sel_p, _, _ = _agent_badge(tool, state, pinned=pinned)
    return glyph, norm_p, sel_p
