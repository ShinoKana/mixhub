#!/usr/bin/env python3
"""
`mixhub.tui.core.style`: Unified 256-color pair constants, `Style`, and `(role, state) -> Style` `Theme` table.

Encodes hard visual invariant §2.2:
  All five `age.*` roles (`age.working`, `age.wait`, `age.unread`, `age.failed`, `age.seen`)
  are strictly `bold=False, bg=-1`.
"""
from __future__ import annotations

from dataclasses import dataclass

from mixhub.core.textwidth import char_width, clean_single_line, disp_pad, disp_trunc, disp_width

try:
    import curses
except ImportError:  # pragma: no cover
    curses = None  # type: ignore[assignment]

# Color pair IDs (256-color terminal palette)
P_WHITE = 1              # fg 255, bg -1  (Assistant body, bold headings)
P_UNSEL = 2              # fg 252, bg -1  (Unselected sidebar title, soft light gray)
P_META = 3               # fg 244, bg -1  (Meta, thinking/tool summary, top header)
P_DIM = 4                # fg 240, bg -1  (Dormant ·, hints, right-aligned compact age)
P_BORDER = 5             # fg 237, bg -1  (Subtle pane divider & handle)
P_USER_BOX = 6           # fg 255, bg 237 (Selected sidebar row & hover pill)
P_CODE = 7               # fg 147, bg -1  (Soft lavender inline code, no background block)
P_LIVE = 8               # fg 114, bg -1  (Soft sage green #87d787 for working state)
P_WAIT = 9               # fg 214, bg -1  (Warm amber #ffaf00 bold for wait_input state)
P_DONE = 10              # fg 67,  bg -1  (Calm steel blue #5f87af for done_idle state)
P_UNREAD = 11            # fg 196, bg -1  (Legacy unread pair)
P_AGENT_CLAUDE = 12      # fg 174, bg -1  (Claude Code pink 174, #D78787)
P_AGENT_AGY = 13         # fg 75,  bg -1  (Google Gemini cornflower-blue 75)
P_AGENT_CODEX = 14       # fg 151, bg -1  (OpenAI emerald-green 151)
P_AGENT_CLAUDE_SEL = 15  # fg 174, bg 237 (Selected row Claude brand dot & '┃' bar)
P_AGENT_AGY_SEL = 16     # fg 75,  bg 237 (Selected row Gemini brand dot & '┃' bar)
P_AGENT_CODEX_SEL = 17   # fg 151, bg 237 (Selected row Codex brand dot & '┃' bar)
P_UNREAD_SEL = 18        # fg 196, bg 237 (Legacy selected unread pair)
P_BTN = 19               # fg 252, bg 238 (Legacy pill pair)
P_HOVER = 20             # fg 255, bg 235 (Hovered sidebar row deep slate background + bright title)
P_HOVER_DIM = 21         # fg 243, bg 235 (Hovered sidebar row '┆' indicator & compact age)
P_AGENT_CLAUDE_HOV = 22  # fg 174, bg 235 (Hovered row Claude brand dot)
P_AGENT_AGY_HOV = 23     # fg 75,  bg 235 (Hovered row Gemini brand dot)
P_AGENT_CODEX_HOV = 24   # fg 151, bg 235 (Hovered row Codex brand dot)
P_UNREAD_HOV = 25        # fg 196, bg 235 (Legacy hovered unread pair)
P_LIVE_SEL = 26          # fg 114, bg 237 (Selected row working)
P_WAIT_SEL = 27          # fg 214, bg 237 (Selected row wait_input)
P_DONE_SEL = 28          # fg 67,  bg 237 (Selected row done_idle)
P_DIM_SEL = 29           # fg 244, bg 237 (Selected row dormant & age)
P_LIVE_HOV = 30          # fg 114, bg 235 (Hovered row working)
P_WAIT_HOV = 31          # fg 214, bg 235 (Hovered row wait_input)
P_DONE_HOV = 32          # fg 67,  bg 235 (Hovered row done_idle)
P_DIM_HOV = 33           # fg 243, bg 235 (Hovered row dormant)
P_STAT_LBL = 34          # fg 242, bg -1  (Morandi dim gray #6c6c6c for 'cpu'/'mem' labels)
P_STAT_T1 = 35           # fg 108, bg -1  (Morandi sage moss green #87af87: cpu <40% / mem <2.5G)
P_STAT_T2 = 36           # fg 144, bg -1  (Morandi oat khaki #afaf87: cpu 40-65% / mem 2.5-4.0G)
P_STAT_T3 = 37           # fg 180, bg -1  (Morandi warm sand/camel #d7af87: cpu 65-85% / mem 4.0-5.5G)
P_STAT_T4 = 38           # fg 174, bg -1  (Morandi dusty coral #d78787: cpu >=85% / mem >=5.5G, zero bold)
P_SEL_WAIT = 39          # fg 221, bg 58  (Morandi amber solid band + warm gold text for selected/header wait_input)
P_SEL_WAIT_DIM = 40      # fg 180, bg 58  (Muted camel gold on Morandi amber band for secondary meta)
P_BTN_HOT = 41           # fg 16,  bg 117 (Hovered primary action chip: bright sky-blue #87d7ff + dark text, + A_BOLD)
P_BTN_OFF = 42           # fg 240, bg 235 (Disabled '↙ Move Here' when external tab is busy)
P_REC = 43               # fg 231, bg 124 (Urgent weekly-quota '<15%' Relay pill: deep crimson #af0000 + white text, + A_BOLD)
P_BAND_T1 = 44           # fg 108, bg 235 (Morandi sage green on bg=235 bar)
P_BAND_T2 = 45           # fg 144, bg 235 (Morandi oat khaki on bg=235 bar)
P_BAND_T3 = 46           # fg 180, bg 235 (Morandi warm camel on bg=235 bar: 'stops it in that tab, resumes here')
P_BAND_T4 = 47           # fg 174, bg 235 (Morandi dusty coral on bg=235 bar: 'Claude weekly 6% left')
P_BTN_SEC = 48           # fg 253, bg 240 (Secondary candidate pill at rest: medium-gray #585858 + bright white text)
P_ACT_PRI = 49           # fg 16,  bg 39  (Default lit primary button at rest: bright cyan-blue #00afff + dark text, + A_BOLD)
P_ACT_SEC_HOV = 50       # fg 16,  bg 250 (Hovered secondary candidate / agent pill: bright light-gray #bcbcbc + dark text, + A_BOLD)
P_ACT_WARN_HOV = 51      # fg 231, bg 160 (Hovered urgent Relay pill: bright red #d70000 + white text, + A_BOLD)
P_STAT_T3_SEL = 52       # fg 180, bg 237 (Selected row unread age: Morandi warm sand gold, zero bold)
P_STAT_T4_SEL = 53       # fg 174, bg 237 (Selected row wait_input age: Morandi terracotta coral, zero bold)
P_STAT_FAIL = 54         # fg 139, bg -1  (Failed/interrupted unread age: Morandi dusty lavender-gray, zero bold)
P_STAT_FAIL_SEL = 55     # fg 139, bg 237 (Selected row failed/interrupted unread age)
P_AGENT_OPENCODE = 68     # fg 253, bg -1  (OpenCode logo off-white, a touch grayer)
P_AGENT_OPENCODE_SEL = 69 # fg 253, bg 237
P_AGENT_OPENCODE_HOV = 70 # fg 253, bg 235

# Right-edge 3-cell title gradient fade-out pairs (interpolated toward BG_GRAY = 233 / row band bg)
BG_GRAY = 233            # Default dark terminal background reference (~#121212)
P_FADE_UNSEL_1 = 56      # fg 247 (#9e9e9e), bg -1  (Unselected 252 title fade cell -3)
P_FADE_UNSEL_2 = 57      # fg 243 (#767676), bg -1  (Unselected 252 title fade cell -2)
P_FADE_UNSEL_3 = 58      # fg 238 (#444444), bg -1  (Unselected 252 title fade cell -1)
P_FADE_BRIGHT_1 = 59     # fg 250 (#bcbcbc), bg -1  (Bright 255 title fade cell -3 on transparent bg)
P_FADE_BRIGHT_2 = 60     # fg 244 (#808080), bg -1  (Bright 255 title fade cell -2 on transparent bg)
P_FADE_BRIGHT_3 = 61     # fg 239 (#4e4e4e), bg -1  (Bright 255 title fade cell -1 on transparent bg)
P_FADE_HOV_1 = 62        # fg 248 (#a8a8a8), bg 235 (Full-band hover 255 title fade cell -3)
P_FADE_HOV_2 = 63        # fg 243 (#767676), bg 235 (Full-band hover 255 title fade cell -2)
P_FADE_HOV_3 = 64        # fg 239 (#4e4e4e), bg 235 (Full-band hover 255 title fade cell -1)
P_FADE_SEL_1 = 65        # fg 250 (#bcbcbc), bg 237 (Full-band selected 255 title fade cell -3)
P_FADE_SEL_2 = 66        # fg 245 (#8a8a8a), bg 237 (Full-band selected 255 title fade cell -2)
P_FADE_SEL_3 = 67        # fg 241 (#626262), bg 237 (Full-band selected 255 title fade cell -1)

P_ACT_PRI_HOV = P_BTN_HOT
P_ACT_SEC = P_BTN_SEC
P_ACT_WARN = P_REC
P_RELAY_HOT = P_ACT_WARN_HOV
P_RELAY_ALERT = P_REC
P_BAND_T1_ON_BAR = P_BAND_T1
P_BAND_T2_ON_BAR = P_BAND_T2
P_BAND_T3_ON_BAR = P_BAND_T3
P_BAND_T4_ON_BAR = P_BAND_T4
P_DIM_ON_BAR = P_HOVER_DIM
P_META_ON_BAR = P_HOVER_DIM
P_STAT_LOW = P_STAT_T1
P_STAT_WARN = P_STAT_T3
P_STAT_HIGH = P_STAT_T4
P_ERR = P_STAT_T4
P_WARN = P_STAT_T3

AGE_ROLES: tuple[str, ...] = (
    "age.working",
    "age.wait",
    "age.unread",
    "age.failed",
    "age.seen",
)


@dataclass(frozen=True)
class Style:
    """
    Immutable cell style token.
    `attr()` is the canonical place translating `(pair, bold, reverse, underline)` into a curses attribute bitmask,
    falling back deterministically when curses is not initialized (headless unit tests).
    """

    pair: int = P_UNSEL
    bold: bool = False
    reverse: bool = False
    underline: bool = False
    bg: int = -1

    def attr(self) -> int:
        base = self.pair << 8
        if curses is not None:
            try:
                base = int(curses.color_pair(self.pair))
            except Exception:
                base = self.pair << 8
            if self.bold:
                base |= int(getattr(curses, "A_BOLD", 1 << 21))
            if self.reverse:
                base |= int(getattr(curses, "A_REVERSE", 1 << 18))
            if self.underline:
                base |= int(getattr(curses, "A_UNDERLINE", 1 << 17))
            return base
        if self.bold:
            base |= 1 << 21
        if self.reverse:
            base |= 1 << 18
        if self.underline:
            base |= 1 << 17
        return base


_THEME_TABLE: dict[tuple[str, str], Style] = {
    # 5-state right-aligned compact age roles: NEVER bold, NEVER background fill (bg=-1)
    ("age.working", "rest"): Style(P_DONE, bold=False, bg=-1),
    ("age.working", "selected"): Style(P_DONE, bold=False, bg=-1),
    ("age.working", "hover"): Style(P_DONE, bold=False, bg=-1),
    ("age.wait", "rest"): Style(P_STAT_T3, bold=False, bg=-1),
    ("age.wait", "selected"): Style(P_STAT_T3, bold=False, bg=-1),
    ("age.wait", "hover"): Style(P_STAT_T3, bold=False, bg=-1),
    ("age.unread", "rest"): Style(P_STAT_T1, bold=False, bg=-1),
    ("age.unread", "selected"): Style(P_META, bold=False, bg=-1),
    ("age.unread", "hover"): Style(P_STAT_T1, bold=False, bg=-1),
    ("age.failed", "rest"): Style(P_STAT_T4, bold=False, bg=-1),
    ("age.failed", "selected"): Style(P_META, bold=False, bg=-1),
    ("age.failed", "hover"): Style(P_STAT_T4, bold=False, bg=-1),
    ("age.seen", "rest"): Style(P_DIM, bold=False, bg=-1),
    ("age.seen", "selected"): Style(P_META, bold=False, bg=-1),
    ("age.seen", "hover"): Style(P_DIM, bold=False, bg=-1),
    # Popover / menu roles
    ("menu.item", "rest"): Style(P_UNSEL, bold=False, bg=-1),
    ("menu.item", "hover"): Style(P_USER_BOX, bold=True, bg=237),
    ("menu.item", "selected"): Style(P_USER_BOX, bold=True, bg=237),
    ("menu.item", "active"): Style(P_USER_BOX, bold=True, bg=237),
    ("menu.item", "disabled"): Style(P_DIM, bold=False, bg=-1),
    ("menu.border", "rest"): Style(P_META, bold=False, bg=-1),
    ("menu.border", "hover"): Style(P_META, bold=False, bg=-1),
    # Action chips / pills
    ("chip", "rest"): Style(P_UNSEL, bold=False, bg=-1),
    ("chip", "hover"): Style(P_USER_BOX, bold=True, bg=237),
    ("chip", "selected"): Style(P_USER_BOX, bold=True, bg=237),
    ("chip", "active"): Style(P_USER_BOX, bold=True, bg=237),
    ("chip", "disabled"): Style(P_BTN_OFF, bold=False, bg=235),
    ("chip.primary", "rest"): Style(P_ACT_PRI, bold=True, bg=39),
    ("chip.primary", "hover"): Style(P_BTN_HOT, bold=True, bg=117),
    ("chip.primary", "disabled"): Style(P_BTN_OFF, bold=False, bg=235),
    ("chip.secondary", "rest"): Style(P_BTN_SEC, bold=False, bg=240),
    ("chip.secondary", "hover"): Style(P_ACT_SEC_HOV, bold=True, bg=250),
    ("chip.warn", "rest"): Style(P_REC, bold=True, bg=124),
    ("chip.warn", "hover"): Style(P_ACT_WARN_HOV, bold=True, bg=160),
    # Sidebar section headers & row titles
    ("section", "rest"): Style(P_META, bold=True, bg=-1),
    ("section", "hover"): Style(P_WHITE, bold=True, bg=-1),
    ("title", "rest"): Style(P_UNSEL, bold=False, bg=-1),
    ("title", "hover"): Style(P_WHITE, bold=False, bg=235),
    ("title", "selected"): Style(P_WHITE, bold=True, bg=237),
    ("title", "active"): Style(P_WHITE, bold=True, bg=-1),
    ("title", "disabled"): Style(P_DIM, bold=False, bg=-1),
    # Right-edge 3-cell title gradient fade-out roles (col -3, col -2, col -1)
    ("title.fade1", "rest"): Style(P_FADE_UNSEL_1, bold=False, bg=-1),
    ("title.fade2", "rest"): Style(P_FADE_UNSEL_2, bold=False, bg=-1),
    ("title.fade3", "rest"): Style(P_FADE_UNSEL_3, bold=False, bg=-1),
    ("title.fade1", "active"): Style(P_FADE_BRIGHT_1, bold=True, bg=-1),
    ("title.fade2", "active"): Style(P_FADE_BRIGHT_2, bold=True, bg=-1),
    ("title.fade3", "active"): Style(P_FADE_BRIGHT_3, bold=True, bg=-1),
    ("title.fade1", "hover"): Style(P_FADE_HOV_1, bold=False, bg=235),
    ("title.fade2", "hover"): Style(P_FADE_HOV_2, bold=False, bg=235),
    ("title.fade3", "hover"): Style(P_FADE_HOV_3, bold=False, bg=235),
    ("title.fade1", "selected"): Style(P_FADE_SEL_1, bold=True, bg=237),
    ("title.fade2", "selected"): Style(P_FADE_SEL_2, bold=True, bg=237),
    ("title.fade3", "selected"): Style(P_FADE_SEL_3, bold=True, bg=237),
    # Wordmark states
    ("wordmark", "rest"): Style(P_UNSEL, bold=False, bg=-1),
    ("wordmark", "hover"): Style(P_USER_BOX, bold=True, bg=237),
    ("wordmark", "accent"): Style(P_WAIT, bold=True, bg=-1),
    ("wordmark", "dim"): Style(P_DIM, bold=False, bg=-1),
    ("wordmark", "locked"): Style(P_WHITE, bold=True, bg=-1),
    ("wordmark", "active"): Style(P_UNSEL, bold=False, bg=-1),
    ("wordmark", "flash"): Style(P_UNSEL, bold=False, reverse=True, bg=-1),
    # Brand roles
    ("agent.claude", "rest"): Style(P_AGENT_CLAUDE, bold=False, bg=-1),
    ("agent.claude", "hover"): Style(P_AGENT_CLAUDE_SEL, bold=True, bg=237),
    ("agent.claude", "selected"): Style(P_AGENT_CLAUDE_SEL, bold=True, bg=237),
    ("agent.agy", "rest"): Style(P_AGENT_AGY, bold=False, bg=-1),
    ("agent.agy", "hover"): Style(P_AGENT_AGY_SEL, bold=True, bg=237),
    ("agent.agy", "selected"): Style(P_AGENT_AGY_SEL, bold=True, bg=237),
    ("agent.gemini", "rest"): Style(P_AGENT_AGY, bold=False, bg=-1),
    ("agent.gemini", "hover"): Style(P_AGENT_AGY_SEL, bold=True, bg=237),
    ("agent.gemini", "selected"): Style(P_AGENT_AGY_SEL, bold=True, bg=237),
    ("agent.codex", "rest"): Style(P_AGENT_CODEX, bold=False, bg=-1),
    ("agent.codex", "hover"): Style(P_AGENT_CODEX_SEL, bold=True, bg=237),
    ("agent.codex", "selected"): Style(P_AGENT_CODEX_SEL, bold=True, bg=237),
    ("agent.opencode", "rest"): Style(P_AGENT_OPENCODE, bold=False, bg=-1),
    ("agent.opencode", "hover"): Style(P_AGENT_OPENCODE_SEL, bold=True, bg=237),
    ("agent.opencode", "selected"): Style(P_AGENT_OPENCODE_SEL, bold=True, bg=237),
    ("agent.deepseek", "rest"): Style(P_DONE, bold=False, bg=-1),
    ("agent.deepseek", "hover"): Style(P_DONE_SEL, bold=True, bg=237),
    ("agent.deepseek", "selected"): Style(P_DONE_SEL, bold=True, bg=237),
    # Morandi system & quota telemetry tiers
    ("stat.label", "rest"): Style(P_STAT_LBL, bold=False, bg=-1),
    ("stat.t1", "rest"): Style(P_STAT_T1, bold=False, bg=-1),
    ("stat.t2", "rest"): Style(P_STAT_T2, bold=False, bg=-1),
    ("stat.t3", "rest"): Style(P_STAT_T3, bold=False, bg=-1),
    ("stat.t4", "rest"): Style(P_STAT_T4, bold=False, bg=-1),
    ("divider", "rest"): Style(P_BORDER, bold=False, bg=-1),
    ("divider", "hover"): Style(P_UNSEL, bold=False, bg=-1),
    ("divider.handle", "rest"): Style(P_META, bold=False, bg=-1),
    ("divider.handle", "hover"): Style(P_WHITE, bold=True, bg=-1),
}


class Theme:
    """
    Role × State style lookup table.
    Guarantees invariant §2.2: `age.working`, `age.wait`, `age.unread`, `age.failed`, `age.seen`
    always resolve to `bold=False` and `bg=-1`.
    """

    def __init__(self, overrides: dict[tuple[str, str], Style] | None = None):
        self._table: dict[tuple[str, str], Style] = dict(_THEME_TABLE)
        if overrides:
            self._table.update(overrides)

    def style(self, role: str, state: str = "rest") -> Style:
        st = self._table.get((role, state))
        if st is None:
            st = self._table.get((role, "rest"))
        if st is None:
            st = Style(P_UNSEL, bold=False, bg=-1)
        if role.startswith("age."):
            return Style(pair=st.pair, bold=False, reverse=False, underline=False, bg=-1)
        return st


DEFAULT_THEME = Theme()

FADE_PAIR_RAMPS: dict[str, tuple[int, int, int, int]] = {
    "rest": (P_UNSEL, P_FADE_UNSEL_1, P_FADE_UNSEL_2, P_FADE_UNSEL_3),
    "bright": (P_WHITE, P_FADE_BRIGHT_1, P_FADE_BRIGHT_2, P_FADE_BRIGHT_3),
    "hover": (P_HOVER, P_FADE_HOV_1, P_FADE_HOV_2, P_FADE_HOV_3),
    "selected": (P_USER_BOX, P_FADE_SEL_1, P_FADE_SEL_2, P_FADE_SEL_3),
}


def resolve_title_fade_pair(step: int, is_bright: bool = False, band_mode: str = "transparent") -> int:
    """
    Return the color pair ID for a sidebar title segment at fade `step` (`0` = full, `1..3` = fade ramp).
    In `transparent` mode (`bg = -1`, used by MixHub's 3-col icon pill sidebar), selects between
    the normal (`252 -> 247 -> 243 -> 238`) and bright (`255 -> 250 -> 244 -> 239`) ramps.
    """
    idx = max(0, min(3, int(step)))
    if band_mode == "selected":
        ramp = FADE_PAIR_RAMPS["selected"]
    elif band_mode == "hover":
        ramp = FADE_PAIR_RAMPS["hover"]
    else:
        ramp = FADE_PAIR_RAMPS["bright" if is_bright else "rest"]
    return ramp[idx]


def disp_fade_cells(text: str, target_cols: int = 21, fade_cols: int = 3) -> list[tuple[str, int]]:
    """
    Lay out `text` into exact `target_cols` display width using a 3-cell right-edge fade-out
    when `disp_width(text) > target_cols` (instead of a trailing `…` ellipsis character).

    Returns a list of coalesced `(substring, fade_step)` tuples where `fade_step` is in `{0, 1, 2, 3}`:
      - `0`: full brightness (all non-overflowing titles, prefix before the fade window, and trailing pad spaces)
      - `1, 2, 3`: 3-step grayscale fade across `[end_col - fade_cols, end_col)` anchored to the last
        visible non-space glyph (`end_col`), with 2-col CJK wide characters averaged across their 2 cells
        (`(step(c) + step(c+1) + 1) // 2`) so brightness decreases monotonically without abrupt jumps.
    """
    cleaned = clean_single_line(text)
    if target_cols <= 0:
        return []
    total_w = disp_width(cleaned)
    if total_w <= target_cols:
        pad = max(0, target_cols - total_w)
        return [(cleaned + (" " * pad), 0)]
    if fade_cols <= 0 or target_cols <= fade_cols:
        return [(disp_pad(cleaned, target_cols), 0)]

    raw_trunc = disp_trunc(cleaned, target_cols, "")
    vis_str = raw_trunc.rstrip(" ")
    end_col = disp_width(vis_str)
    pad_len = max(0, target_cols - end_col)
    if end_col <= 0:
        return [(" " * target_cols, 0)]

    fade_start = max(0, end_col - fade_cols)

    def _col_step(c: int) -> int:
        if c < fade_start:
            return 0
        return min(3, c - fade_start + 1)

    runs: list[tuple[str, int]] = []
    col = 0
    for ch in vis_str:
        cw = char_width(ch)
        if cw <= 0:
            if runs:
                runs[-1] = (runs[-1][0] + ch, runs[-1][1])
            else:
                runs.append((ch, 0))
            continue
        if cw == 1:
            step = _col_step(col)
        else:
            s0 = _col_step(col)
            s1 = _col_step(col + 1)
            step = (s0 + s1 + 1) // 2
        if runs and runs[-1][1] == step:
            runs[-1] = (runs[-1][0] + ch, step)
        else:
            runs.append((ch, step))
        col += cw

    if pad_len > 0:
        pad_str = " " * pad_len
        if runs and runs[-1][1] == 0:
            runs[-1] = (runs[-1][0] + pad_str, 0)
        else:
            runs.append((pad_str, 0))
    return runs


__all__ = [
    "AGE_ROLES",
    "BG_GRAY",
    "DEFAULT_THEME",
    "FADE_PAIR_RAMPS",
    "P_ACT_PRI",
    "P_ACT_PRI_HOV",
    "P_ACT_SEC",
    "P_ACT_SEC_HOV",
    "P_ACT_WARN",
    "P_ACT_WARN_HOV",
    "P_AGENT_AGY",
    "P_AGENT_AGY_HOV",
    "P_AGENT_AGY_SEL",
    "P_AGENT_CLAUDE",
    "P_AGENT_CLAUDE_HOV",
    "P_AGENT_CLAUDE_SEL",
    "P_AGENT_CODEX",
    "P_AGENT_CODEX_HOV",
    "P_AGENT_OPENCODE",
    "P_AGENT_OPENCODE_SEL",
    "P_AGENT_OPENCODE_HOV",
    "P_AGENT_CODEX_SEL",
    "P_BAND_T1",
    "P_BAND_T1_ON_BAR",
    "P_BAND_T2",
    "P_BAND_T2_ON_BAR",
    "P_BAND_T3",
    "P_BAND_T3_ON_BAR",
    "P_BAND_T4",
    "P_BAND_T4_ON_BAR",
    "P_BORDER",
    "P_BTN",
    "P_BTN_HOT",
    "P_BTN_OFF",
    "P_BTN_SEC",
    "P_CODE",
    "P_DIM",
    "P_DIM_HOV",
    "P_DIM_ON_BAR",
    "P_DIM_SEL",
    "P_DONE",
    "P_DONE_HOV",
    "P_DONE_SEL",
    "P_ERR",
    "P_FADE_BRIGHT_1",
    "P_FADE_BRIGHT_2",
    "P_FADE_BRIGHT_3",
    "P_FADE_HOV_1",
    "P_FADE_HOV_2",
    "P_FADE_HOV_3",
    "P_FADE_SEL_1",
    "P_FADE_SEL_2",
    "P_FADE_SEL_3",
    "P_FADE_UNSEL_1",
    "P_FADE_UNSEL_2",
    "P_FADE_UNSEL_3",
    "P_HOVER",
    "P_HOVER_DIM",
    "P_LIVE",
    "P_LIVE_HOV",
    "P_LIVE_SEL",
    "P_META",
    "P_META_ON_BAR",
    "P_REC",
    "P_RELAY_ALERT",
    "P_RELAY_HOT",
    "P_SEL_WAIT",
    "P_SEL_WAIT_DIM",
    "P_STAT_FAIL",
    "P_STAT_FAIL_SEL",
    "P_STAT_HIGH",
    "P_STAT_LBL",
    "P_STAT_LOW",
    "P_STAT_T1",
    "P_STAT_T2",
    "P_STAT_T3",
    "P_STAT_T3_SEL",
    "P_STAT_T4",
    "P_STAT_T4_SEL",
    "P_STAT_WARN",
    "P_UNREAD",
    "P_UNREAD_HOV",
    "P_UNREAD_SEL",
    "P_UNSEL",
    "P_USER_BOX",
    "P_WAIT",
    "P_WAIT_HOV",
    "P_WAIT_SEL",
    "P_WARN",
    "P_WHITE",
    "Style",
    "Theme",
    "disp_fade_cells",
    "resolve_title_fade_pair",
]
