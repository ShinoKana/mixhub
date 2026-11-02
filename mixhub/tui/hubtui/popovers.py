#!/usr/bin/env python3
"""`mixhub.tui.hubtui.popovers`: Floating sidebar cards: provider filter, New Session, row `⋯` actions, and Relay To."""
from __future__ import annotations

from collections.abc import Sequence
import os
import time

from mixhub.core.textwidth import disp_width
from mixhub.tui.badges import AGENT_DISPLAY_NAMES
from mixhub.tui.core.style import (
    P_AGENT_AGY,
    P_AGENT_AGY_SEL,
    P_AGENT_CLAUDE,
    P_AGENT_CLAUDE_SEL,
    P_AGENT_CODEX,
    P_AGENT_CODEX_SEL,
    P_AGENT_OPENCODE,
    P_AGENT_OPENCODE_SEL,
    P_DONE,
    P_DONE_SEL,
    P_META,
    P_UNSEL,
    P_USER_BOX,
)
from mixhub.tui.widgets import (
    draw_popover_card_to_stdscr,
    popover_card_x,
    relay_to_card_y,
)


class PopoversMixin:
    """Open / dismiss state, geometry and items of the sidebar popover cards."""

    # ------------------------------------------------------------------ `mixhub ▾` provider filter
    @property
    def provider_filter(self) -> str | None:
        """Active `mixhub ▾` provider filter (`None` = All), kept in sync with `self.tool_filter`."""
        tf = getattr(self, "tool_filter", "all")
        if tf in ("all", "live", "", None):
            return None
        return "gemini" if getattr(self, "_provider_filter", None) == "gemini" else tf

    @provider_filter.setter
    def provider_filter(self, val) -> None:
        if val in (None, "", "all"):
            self._provider_filter = None
            self.tool_filter = "all"
            return
        val = str(val).lower()
        self._provider_filter = val
        self.tool_filter = "agy" if val == "gemini" else val

    @property
    def new_session_menu_open(self) -> bool:
        return bool(getattr(self, "_new_session_menu", False))

    @new_session_menu_open.setter
    def new_session_menu_open(self, val: bool) -> None:
        self._new_session_menu = bool(val)

    @property
    def relay_menu_open(self) -> bool:
        return bool(getattr(self, "_relay_menu", False))

    @relay_menu_open.setter
    def relay_menu_open(self, val: bool) -> None:
        self._relay_menu = bool(val)

    # ------------------------------------------------------------------ Sidebar popover lifecycle
    def _any_sidebar_popover_open(self) -> bool:
        return bool(
            getattr(self, "_new_session_menu", False)
            or getattr(self, "provider_dropdown_open", False)
            or (self.sidebar_only and getattr(self, "_relay_menu", False))
        )

    def _dismiss_sidebar_popovers(self) -> bool:
        """Close every floating sidebar popover (`✎ New Session`, `mixhub ▾`, `⇄ Relay To`). True if any was open."""
        was_open = self._any_sidebar_popover_open()
        self._new_session_menu = False
        self.provider_dropdown_open = False
        if self.sidebar_only and getattr(self, "_relay_menu", False):
            self._set_relay_state("CLOSED")
        if was_open:
            self._dirty = True
        return was_open

    def _note_sidebar_popover_opened(self, kind: str = "") -> None:
        """
        Called right after a sidebar popover opens (`kind` in `new_session` / `provider` / `relay`):
        closes the other popovers, stamps the open time for the focus-loss fallback, and keeps outer tmux focus
        on `--sidebar` so a click into the right pane reliably produces a FocusOut.
        """
        if kind != "new_session":
            self._new_session_menu = False
        if kind != "provider":
            self.provider_dropdown_open = False
        if kind != "relay" and getattr(self, "_relay_menu", False):
            self._set_relay_state("CLOSED")
        self._popover_opened_mono = time.monotonic()
        self._dirty = True
        if self.sidebar_only:
            self._focus_sidebar_pane_async()

    def _check_sidebar_popover_focus_loss(self, now_mono: float) -> bool:
        """
        Fallback when no FocusOut (`\\x1b[O`) arrives: once a popover has been open >= 0.30s and outer tmux
        reports this pane inactive (user clicked into the right pane), dismiss all sidebar popovers.
        """
        if not (self.sidebar_only and os.environ.get("TMUX") and self._any_sidebar_popover_open()):
            return False
        if now_mono - getattr(self, "_popover_opened_mono", 0.0) < 0.30:
            return False
        # Throttle the tmux probe (draw() + the 0.25s kqueue wakeups both call this)
        if now_mono - getattr(self, "_popover_focus_probe_mono", 0.0) < 0.20:
            return False
        self._popover_focus_probe_mono = now_mono
        if self._is_sidebar_pane_active() is not False:
            return False
        self._dismiss_sidebar_popovers()
        self._clear_hover_state()
        return True
    # Floating sidebar popovers (`Filter` / `New Session` / `⋯` actions / `⇄ Relay To`) share one geometry
    POPOVER_W = 22
    PROVIDER_CARD_Y, PROVIDER_CARD_X, PROVIDER_CARD_W = 1, 4, POPOVER_W
    NEW_SESSION_CARD_Y = 2
    # `(key, label, rest pair, hover pair)`; the `1`-`5` / `1`-`4` keys still pick by position
    PROVIDER_FILTER_ITEMS = (
        (None, "All", P_UNSEL, P_USER_BOX),
        ("claude", "Claude Code", P_AGENT_CLAUDE, P_AGENT_CLAUDE_SEL),
        ("codex", "Codex", P_AGENT_CODEX, P_AGENT_CODEX_SEL),
        ("opencode", "OpenCode", P_AGENT_OPENCODE, P_AGENT_OPENCODE_SEL),
        ("gemini", "Gemini", P_AGENT_AGY, P_AGENT_AGY_SEL),
    )
    # DeepSeek is a model, not a harness: CC·DS stays on the `d` hotkey, OpenCode takes the row
    NEW_SESSION_ITEMS = (
        ("claude", "Claude Code", P_AGENT_CLAUDE, P_AGENT_CLAUDE_SEL),
        ("codex", "Codex", P_AGENT_CODEX, P_AGENT_CODEX_SEL),
        ("opencode", "OpenCode", P_AGENT_OPENCODE, P_AGENT_OPENCODE_SEL),
        ("agy", "Gemini", P_AGENT_AGY, P_AGENT_AGY_SEL),
    )
    RELAY_TO_ITEMS = (
        ("claude", "Claude Code", P_AGENT_CLAUDE, P_AGENT_CLAUDE_SEL),
        ("agy", "Gemini", P_AGENT_AGY, P_AGENT_AGY_SEL),
        ("codex", "Codex", P_AGENT_CODEX, P_AGENT_CODEX_SEL),
        ("deepseek", "DeepSeek", P_DONE, P_DONE_SEL),
    )

    def _provider_filter_item(self) -> tuple | None:
        """`(key, label, pair, sel_pair)` of the active `mixhub ▾` filter, or `None` when showing All."""
        pf = self.provider_filter
        if pf is None:
            return None
        key = "gemini" if pf in ("gemini", "agy") else pf
        for item in self.PROVIDER_FILTER_ITEMS:
            if item[0] == key:
                return item
        if key == "deepseek":  # set by the `d` hotkey / `@deepseek`; no Filter row (a model, not an agent)
            return (key, "DeepSeek", P_DONE, P_DONE_SEL)
        return (key, AGENT_DISPLAY_NAMES.get(key, str(key).capitalize()), P_META, P_USER_BOX)

    def provider_filter_items(self) -> list[tuple]:
        """`Filter` rows: `All` plus agents (harnesses, not models) that have sessions here."""
        tools = self._available_agent_tools(default=set())
        if "agy" in tools:
            tools.add("gemini")
        active_key = (self._provider_filter_item() or (None,))[0]
        return [it for it in self.PROVIDER_FILTER_ITEMS if it[0] is None or it[0] in tools or it[0] == active_key]

    def _provider_badge_text(self) -> str:
        """Row 0 active-filter badge (`· Claude`, short brand name), or `""` when showing All."""
        item = self._provider_filter_item()
        if item is None:
            return ""
        return f"· {AGENT_DISPLAY_NAMES.get(item[0], item[1])}"

    def _provider_badge_hit_x1(self) -> int:
        """Right edge (inclusive) of the clickable `· Claude` badge, or -1 when no filter is active."""
        badge = self._provider_badge_text()
        if not badge:
            return -1
        return max(self.PROVIDER_BADGE_HIT[1], self.PROVIDER_BADGE_X + disp_width(badge) - 1)

    def _is_provider_toggle_x(self, x: int) -> bool:
        """Row 0 column that toggles the Filter card: the `▾` zone, or the `· Claude` badge while filtered."""
        if self.PROVIDER_DROP_HIT[0] <= x <= self.PROVIDER_DROP_HIT[1]:
            return True
        return self.PROVIDER_BADGE_HIT[0] <= x <= self._provider_badge_hit_x1()

    def _toggle_provider_dropdown(self) -> None:
        self.provider_dropdown_open = not getattr(self, "provider_dropdown_open", False)
        if self.provider_dropdown_open:
            self._note_sidebar_popover_opened("provider")
        self._dirty = True

    def _select_provider_filter(self, fkey: str | None) -> None:
        """Apply a `mixhub ▾` choice. Filtering only changes the list: Home stays Home, the open session stays open."""
        on_home = self.current_session() is None
        self.provider_filter = fkey
        self.provider_dropdown_open = False
        self.list_scroll = 0
        self.refresh_data(fast_only=True, reorder=True)
        if on_home:
            self.selected_idx = -1
        self._dirty = True

    def _popover_card_x(self, left_w: int) -> int:
        return popover_card_x(left_w, self.POPOVER_W)

    def _relay_to_card_y(self, list_top: int, list_bottom: int) -> int:
        return relay_to_card_y(list_top, list_bottom, len(self.RELAY_TO_ITEMS))

    def _draw_sidebar_popover_card(
        self,
        card_y: int,
        card_x: int,
        card_w: int,
        title: str,
        items: Sequence[tuple[str | None, str, int, int, str]],
        hx: int,
        hy: int,
        left_w: int,
    ) -> None:
        """Delegate floating sidebar popover rendering to `mixhub.tui.widgets.popover.draw_popover_card_to_stdscr`."""
        draw_popover_card_to_stdscr(self.stdscr, card_y, card_x, card_w, title, items, hx, hy, left_w)
