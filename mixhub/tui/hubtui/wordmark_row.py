#!/usr/bin/env python3
"""`mixhub.tui.hubtui.wordmark_row`: Sidebar row 0: the `mixhub` wordmark, the `▾` filter trigger and the active-filter badge."""
from __future__ import annotations

try:
    import curses
except ImportError:  # Windows without windows-curses installed
    curses = None

from mixhub.tui.widgets.wordmark import (
    ST_ACCENT as WM_ST_ACCENT,
    ST_ACTIVE as WM_ST_ACTIVE,
    ST_DIM as WM_ST_DIM,
    ST_FLASH as WM_ST_FLASH,
    ST_LOCKED as WM_ST_LOCKED,
)

from mixhub.core.textwidth import disp_width
from mixhub.tui.core.style import P_DIM, P_META, P_UNSEL, P_USER_BOX, P_WAIT, P_WAIT_HOV, P_WHITE


class WordmarkRowMixin:
    """Draws and handles clicks on the sidebar's top row."""

    WORDMARK_X = 5             # `m` of `mixhub` aligns with `New session` / section labels / titles
    WORDMARK_HIT = (5, 10)     # row-0 `mixhub` hot zone (inclusive) for hover pill + click
    PROVIDER_DROP_X = 2        # row-0 `▾` filter trigger, LEFT of `mixhub`, in the icon column (`✎` / `▾ Pinned` / `◇`)
    PROVIDER_DROP_HIT = (1, 3)  # ` ▾ ` only; col 4 separates it from the `mixhub` button
    PROVIDER_BADGE_X = 12      # `· Claude` active-filter badge right of `mixhub` (also toggles the card)
    PROVIDER_BADGE_HIT = (11, 20)

    def _wordmark_wait_tool(self) -> bool:
        return any(s.get("waitKind") == "tool" for s in self.sessions)

    def _draw_wordmark_row(self, now_mono: float, hx: int, hy: int, hdr_w: int, toggle_x: int, work_n: int) -> None:
        """
        Row 0 of the sidebar: `mixhub` at col 5 with a ` mixhub ` hover pill, plus the `▾` Filter trigger at col 2
        (left of `mixhub`, in the icon column). The two are separate buttons that never light each other up. `▾` is
        always visible: light gray (`P_UNSEL`, like `mixhub` / `New session`) at rest, boxed ` ▾ ` (cols 1..3, like
        `[✎]`) while the card is open or the trigger is hovered, and brand-colored with a `· Claude` badge at col 12
        whenever a filter is active.
        """
        if self.selected_idx != -1 and self.wordmark.mode != "off":
            self.wordmark.set_mode("off", now_mono)
        wm = self.wordmark
        x0 = self.WORDMARK_X
        cells = wm.render_cells(now_mono, working=work_n > 0, wait_tool=self._wordmark_wait_tool())
        is_wm_hov = hy == 0 and self.WORDMARK_HIT[0] <= hx <= self.WORDMARK_HIT[1]
        if is_wm_hov:
            pill_attr = curses.color_pair(P_USER_BOX) | curses.A_BOLD
            pill_w = self.WORDMARK_HIT[1] - self.WORDMARK_HIT[0] + 3  # ` mixhub `: 1 col of padding each side
            self.stdscr.addstr(0, self.WORDMARK_HIT[0] - 1, " " * pill_w, pill_attr)
            for i, (ch, st) in enumerate(cells):
                attr = pill_attr
                if st == WM_ST_ACCENT:
                    attr = curses.color_pair(P_WAIT_HOV) | curses.A_BOLD
                elif st == WM_ST_FLASH:
                    attr |= curses.A_REVERSE
                self.stdscr.addstr(0, x0 + i, ch, attr)
        else:
            style_attr = {
                WM_ST_ACCENT: curses.color_pair(P_WAIT) | curses.A_BOLD,
                WM_ST_DIM: curses.color_pair(P_DIM),
                WM_ST_LOCKED: curses.color_pair(P_WHITE) | curses.A_BOLD,
                WM_ST_ACTIVE: curses.color_pair(P_UNSEL) | curses.A_BOLD,
                WM_ST_FLASH: curses.color_pair(P_UNSEL) | curses.A_REVERSE,
            }
            if all(st not in style_attr for _, st in cells):
                self.stdscr.addstr(0, x0, "".join(ch for ch, _ in cells), curses.color_pair(P_UNSEL))
            else:
                for i, (ch, st) in enumerate(cells):
                    self.stdscr.addstr(0, x0 + i, ch, style_attr.get(st, curses.color_pair(P_UNSEL)))
        drop_open = getattr(self, "provider_dropdown_open", False)
        pitem = self._provider_filter_item()
        badge = self._provider_badge_text()
        drop_hov = hy == 0 and self._is_provider_toggle_x(hx)
        if drop_open or drop_hov:
            psel_pair = pitem[3] if pitem is not None else P_USER_BOX
            self.stdscr.addstr(0, self.PROVIDER_DROP_X - 1, " ⌄ ", curses.color_pair(psel_pair) | curses.A_BOLD)
        elif pitem is not None:
            self.stdscr.addstr(0, self.PROVIDER_DROP_X, "⌄", curses.color_pair(pitem[2]) | curses.A_BOLD)
        else:
            self.stdscr.addstr(0, self.PROVIDER_DROP_X, "⌄", curses.color_pair(P_UNSEL))
        badge_shown = False
        if pitem is not None and self.PROVIDER_BADGE_X + disp_width(badge) < toggle_x - 1:
            self.stdscr.addstr(0, self.PROVIDER_BADGE_X, badge, curses.color_pair(pitem[2]))
            badge_shown = True
        can_b = self.can_nav_back()
        can_f = self.can_nav_forward()
        show_nav = toggle_x > self.NAV_FWD_HIT[1] + 1
        if self.selected_idx == -1 and not badge_shown:
            suffix, is_record = wm.render_suffix(now_mono)
            max_suf_x = min(toggle_x - 1, self.NAV_BACK_HIT[0]) if show_nav else (toggle_x - 1)
            if suffix and x0 + 8 + len(suffix) < max_suf_x:
                s_attr = (curses.color_pair(P_WHITE) | curses.A_BOLD) if is_record else curses.color_pair(P_DIM)
                self.stdscr.addstr(0, x0 + 8, suffix, s_attr)
        if show_nav:
            b_hov = can_b and hy == 0 and self.NAV_BACK_HIT[0] <= hx <= self.NAV_BACK_HIT[1]
            f_hov = can_f and hy == 0 and self.NAV_FWD_HIT[0] <= hx <= self.NAV_FWD_HIT[1]
            if b_hov:
                self.stdscr.addstr(0, self.NAV_BACK_HIT[0], " ‹ ", curses.color_pair(P_USER_BOX) | curses.A_BOLD)
            else:
                self.stdscr.addstr(0, self.NAV_BACK_X, "‹", curses.color_pair(P_META if can_b else P_DIM))
            if f_hov:
                self.stdscr.addstr(0, self.NAV_FWD_HIT[0], " › ", curses.color_pair(P_USER_BOX) | curses.A_BOLD)
            else:
                self.stdscr.addstr(0, self.NAV_FWD_X, "›", curses.color_pair(P_META if can_f else P_DIM))

    def _on_wordmark_clicked(self, now_mono: float) -> None:
        """Click on top-left `mixhub`.

        - From a session: go Home (deselect + `pool:reader`) with a static wordmark (no animation).
        - Already on Home: toggle the resting Easter egg (300ms debounce right after returning Home).
        """
        if self.selected_idx >= 0:
            self._wordmark_home_nav_ts = now_mono
            self._nav_record("__home__")
            self._apply_nav_target("__home__")
            self.wordmark.set_mode("off", now_mono)
            self._dirty = True
            return
        if now_mono - getattr(self, "_wordmark_home_nav_ts", -10.0) < 0.30:
            return
        self.wordmark.on_wordmark_click(now_mono)
        self._dirty = True
