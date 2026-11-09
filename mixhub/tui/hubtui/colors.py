#!/usr/bin/env python3
"""`mixhub.tui.hubtui.colors`: curses color pair initialization for the 256-color Morandi palette."""
from __future__ import annotations

try:
    import curses
except ImportError:  # Windows without windows-curses installed
    curses = None

from mixhub.tui.core.style import (
    P_ACT_PRI,
    P_ACT_SEC_HOV,
    P_ACT_WARN_HOV,
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
    P_BAND_T1,
    P_BAND_T2,
    P_BAND_T3,
    P_BAND_T4,
    P_BORDER,
    P_BTN,
    P_BTN_HOT,
    P_BTN_OFF,
    P_BTN_SEC,
    P_CODE,
    P_DIM,
    P_DIM_HOV,
    P_DIM_SEL,
    P_DONE,
    P_DONE_HOV,
    P_DONE_SEL,
    P_FADE_BRIGHT_1,
    P_FADE_BRIGHT_2,
    P_FADE_BRIGHT_3,
    P_FADE_HOV_1,
    P_FADE_HOV_2,
    P_FADE_HOV_3,
    P_FADE_SEL_1,
    P_FADE_SEL_2,
    P_FADE_SEL_3,
    P_FADE_UNSEL_1,
    P_FADE_UNSEL_2,
    P_FADE_UNSEL_3,
    P_HOVER,
    P_HOVER_DIM,
    P_LIVE,
    P_LIVE_HOV,
    P_LIVE_SEL,
    P_META,
    P_REC,
    P_SEL_WAIT,
    P_SEL_WAIT_DIM,
    P_STAT_FAIL,
    P_STAT_FAIL_SEL,
    P_STAT_LBL,
    P_STAT_T1,
    P_STAT_T2,
    P_STAT_T3,
    P_STAT_T3_SEL,
    P_STAT_T4,
    P_STAT_T4_SEL,
    P_UNREAD,
    P_UNREAD_HOV,
    P_UNREAD_SEL,
    P_UNSEL,
    P_USER_BOX,
    P_WAIT,
    P_WAIT_HOV,
    P_WAIT_SEL,
    P_WHITE,
)


class ColorsMixin:
    """Registers every `P_*` color pair with curses."""

    def _init_colors(self):
        curses.start_color()
        curses.use_default_colors()
        if getattr(curses, "COLORS", 8) >= 256:
            curses.init_pair(P_WHITE, 255, -1)
            curses.init_pair(P_UNSEL, 252, -1)
            curses.init_pair(P_META, 244, -1)
            curses.init_pair(P_DIM, 240, -1)
            curses.init_pair(P_BORDER, 237, -1)
            curses.init_pair(P_USER_BOX, 255, 237)
            curses.init_pair(P_CODE, 147, -1)
            curses.init_pair(P_LIVE, 114, -1)
            curses.init_pair(P_WAIT, 214, -1)
            curses.init_pair(P_DONE, 67, -1)
            curses.init_pair(P_UNREAD, 196, -1)
            curses.init_pair(P_AGENT_CLAUDE, 174, -1)
            curses.init_pair(P_AGENT_AGY, 75, -1)
            curses.init_pair(P_AGENT_CODEX, 189, -1)
            curses.init_pair(P_AGENT_CLAUDE_SEL, 174, 237)
            curses.init_pair(P_AGENT_AGY_SEL, 75, 237)
            curses.init_pair(P_AGENT_CODEX_SEL, 189, 237)
            curses.init_pair(P_UNREAD_SEL, 196, 237)
            curses.init_pair(P_BTN, 252, 238)
            curses.init_pair(P_HOVER, 255, 235)
            curses.init_pair(P_HOVER_DIM, 243, 235)
            curses.init_pair(P_AGENT_CLAUDE_HOV, 174, 235)
            curses.init_pair(P_AGENT_AGY_HOV, 75, 235)
            curses.init_pair(P_AGENT_CODEX_HOV, 189, 235)
            curses.init_pair(P_AGENT_OPENCODE, 253, -1)
            curses.init_pair(P_AGENT_OPENCODE_SEL, 253, 237)
            curses.init_pair(P_AGENT_OPENCODE_HOV, 253, 235)
            curses.init_pair(P_UNREAD_HOV, 196, 235)
            curses.init_pair(P_LIVE_SEL, 114, 237)
            curses.init_pair(P_WAIT_SEL, 214, 237)
            curses.init_pair(P_DONE_SEL, 67, 237)
            curses.init_pair(P_DIM_SEL, 244, 237)
            curses.init_pair(P_LIVE_HOV, 114, 235)
            curses.init_pair(P_WAIT_HOV, 214, 235)
            curses.init_pair(P_DONE_HOV, 67, 235)
            curses.init_pair(P_DIM_HOV, 243, 235)
            curses.init_pair(P_STAT_LBL, 242, -1)
            curses.init_pair(P_STAT_T1, 108, -1)
            curses.init_pair(P_STAT_T2, 144, -1)
            curses.init_pair(P_STAT_T3, 180, -1)
            curses.init_pair(P_STAT_T4, 174, -1)
            curses.init_pair(P_SEL_WAIT, 221, 58)
            curses.init_pair(P_SEL_WAIT_DIM, 180, 58)
            curses.init_pair(P_BTN_HOT, 16, 117)
            curses.init_pair(P_BTN_OFF, 240, 235)
            curses.init_pair(P_REC, 231, 124)
            curses.init_pair(P_BAND_T1, 108, 235)
            curses.init_pair(P_BAND_T2, 144, 235)
            curses.init_pair(P_BAND_T3, 180, 235)
            curses.init_pair(P_BAND_T4, 174, 235)
            curses.init_pair(P_BTN_SEC, 253, 240)
            curses.init_pair(P_ACT_PRI, 16, 39)
            curses.init_pair(P_ACT_SEC_HOV, 16, 250)
            curses.init_pair(P_ACT_WARN_HOV, 231, 160)
            curses.init_pair(P_STAT_T3_SEL, 180, 237)
            curses.init_pair(P_STAT_T4_SEL, 174, 237)
            curses.init_pair(P_STAT_FAIL, 139, -1)
            curses.init_pair(P_STAT_FAIL_SEL, 139, 237)
            curses.init_pair(P_FADE_UNSEL_1, 247, -1)
            curses.init_pair(P_FADE_UNSEL_2, 243, -1)
            curses.init_pair(P_FADE_UNSEL_3, 238, -1)
            curses.init_pair(P_FADE_BRIGHT_1, 250, -1)
            curses.init_pair(P_FADE_BRIGHT_2, 244, -1)
            curses.init_pair(P_FADE_BRIGHT_3, 239, -1)
            curses.init_pair(P_FADE_HOV_1, 248, 235)
            curses.init_pair(P_FADE_HOV_2, 243, 235)
            curses.init_pair(P_FADE_HOV_3, 239, 235)
            curses.init_pair(P_FADE_SEL_1, 250, 237)
            curses.init_pair(P_FADE_SEL_2, 245, 237)
            curses.init_pair(P_FADE_SEL_3, 241, 237)
        else:
            curses.init_pair(P_WHITE, curses.COLOR_WHITE, -1)
            curses.init_pair(P_UNSEL, curses.COLOR_WHITE, -1)
            curses.init_pair(P_META, curses.COLOR_WHITE, -1)
            curses.init_pair(P_DIM, curses.COLOR_WHITE, -1)
            curses.init_pair(P_BORDER, curses.COLOR_WHITE, -1)
            curses.init_pair(P_USER_BOX, curses.COLOR_WHITE, curses.COLOR_BLACK)
            curses.init_pair(P_CODE, curses.COLOR_MAGENTA, -1)
            curses.init_pair(P_LIVE, curses.COLOR_GREEN, -1)
            curses.init_pair(P_WAIT, curses.COLOR_YELLOW, -1)
            curses.init_pair(P_DONE, curses.COLOR_CYAN, -1)
            curses.init_pair(P_UNREAD, curses.COLOR_RED, -1)
            curses.init_pair(P_AGENT_CLAUDE, curses.COLOR_YELLOW, -1)
            curses.init_pair(P_AGENT_AGY, curses.COLOR_CYAN, -1)
            curses.init_pair(P_AGENT_CODEX, curses.COLOR_WHITE, -1)
            curses.init_pair(P_AGENT_CLAUDE_SEL, curses.COLOR_YELLOW, curses.COLOR_BLACK)
            curses.init_pair(P_AGENT_AGY_SEL, curses.COLOR_CYAN, curses.COLOR_BLACK)
            curses.init_pair(P_AGENT_CODEX_SEL, curses.COLOR_WHITE, curses.COLOR_BLACK)
            curses.init_pair(P_UNREAD_SEL, curses.COLOR_RED, curses.COLOR_BLACK)
            curses.init_pair(P_BTN, curses.COLOR_WHITE, curses.COLOR_BLACK)
            curses.init_pair(P_HOVER, curses.COLOR_WHITE, curses.COLOR_BLACK)
            curses.init_pair(P_HOVER_DIM, curses.COLOR_WHITE, curses.COLOR_BLACK)
            curses.init_pair(P_AGENT_CLAUDE_HOV, curses.COLOR_YELLOW, curses.COLOR_BLACK)
            curses.init_pair(P_AGENT_AGY_HOV, curses.COLOR_CYAN, curses.COLOR_BLACK)
            curses.init_pair(P_AGENT_CODEX_HOV, curses.COLOR_WHITE, curses.COLOR_BLACK)
            curses.init_pair(P_AGENT_OPENCODE, curses.COLOR_WHITE, -1)
            curses.init_pair(P_AGENT_OPENCODE_SEL, curses.COLOR_WHITE, curses.COLOR_BLACK)
            curses.init_pair(P_AGENT_OPENCODE_HOV, curses.COLOR_WHITE, curses.COLOR_BLACK)
            curses.init_pair(P_UNREAD_HOV, curses.COLOR_RED, curses.COLOR_BLACK)
            curses.init_pair(P_LIVE_SEL, curses.COLOR_GREEN, curses.COLOR_BLACK)
            curses.init_pair(P_WAIT_SEL, curses.COLOR_YELLOW, curses.COLOR_BLACK)
            curses.init_pair(P_DONE_SEL, curses.COLOR_CYAN, curses.COLOR_BLACK)
            curses.init_pair(P_DIM_SEL, curses.COLOR_WHITE, curses.COLOR_BLACK)
            curses.init_pair(P_LIVE_HOV, curses.COLOR_GREEN, curses.COLOR_BLACK)
            curses.init_pair(P_WAIT_HOV, curses.COLOR_YELLOW, curses.COLOR_BLACK)
            curses.init_pair(P_DONE_HOV, curses.COLOR_CYAN, curses.COLOR_BLACK)
            curses.init_pair(P_DIM_HOV, curses.COLOR_WHITE, curses.COLOR_BLACK)
            curses.init_pair(P_STAT_LBL, curses.COLOR_WHITE, -1)
            curses.init_pair(P_STAT_T1, curses.COLOR_GREEN, -1)
            curses.init_pair(P_STAT_T2, curses.COLOR_WHITE, -1)
            curses.init_pair(P_STAT_T3, curses.COLOR_YELLOW, -1)
            curses.init_pair(P_STAT_T4, curses.COLOR_YELLOW, -1)
            curses.init_pair(P_SEL_WAIT, curses.COLOR_YELLOW, curses.COLOR_BLACK)
            curses.init_pair(P_SEL_WAIT_DIM, curses.COLOR_YELLOW, curses.COLOR_BLACK)
            curses.init_pair(P_BTN_HOT, curses.COLOR_BLACK, curses.COLOR_CYAN)
            curses.init_pair(P_BTN_OFF, curses.COLOR_WHITE, curses.COLOR_BLACK)
            curses.init_pair(P_REC, curses.COLOR_WHITE, curses.COLOR_RED)
            curses.init_pair(P_BAND_T1, curses.COLOR_GREEN, curses.COLOR_BLACK)
            curses.init_pair(P_BAND_T2, curses.COLOR_WHITE, curses.COLOR_BLACK)
            curses.init_pair(P_BAND_T3, curses.COLOR_YELLOW, curses.COLOR_BLACK)
            curses.init_pair(P_BAND_T4, curses.COLOR_YELLOW, curses.COLOR_BLACK)
            curses.init_pair(P_BTN_SEC, curses.COLOR_WHITE, curses.COLOR_BLACK)
            curses.init_pair(P_ACT_PRI, curses.COLOR_BLACK, curses.COLOR_CYAN)
            curses.init_pair(P_ACT_SEC_HOV, curses.COLOR_BLACK, curses.COLOR_WHITE)
            curses.init_pair(P_ACT_WARN_HOV, curses.COLOR_WHITE, curses.COLOR_RED)
            curses.init_pair(P_STAT_T3_SEL, curses.COLOR_YELLOW, curses.COLOR_BLACK)
            curses.init_pair(P_STAT_T4_SEL, curses.COLOR_YELLOW, curses.COLOR_BLACK)
            curses.init_pair(P_STAT_FAIL, curses.COLOR_MAGENTA, -1)
            curses.init_pair(P_STAT_FAIL_SEL, curses.COLOR_MAGENTA, curses.COLOR_BLACK)
