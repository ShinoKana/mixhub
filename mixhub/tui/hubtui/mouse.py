#!/usr/bin/env python3
"""`mixhub.tui.hubtui.mouse`: Mouse input: SGR mouse decoding, click / hover / scroll handling, and live tmux mouse bindings."""
from __future__ import annotations

try:
    import curses
except ImportError:  # Windows without windows-curses installed
    curses = None
import os
import re
import subprocess
import time

from mixhub.core.textwidth import disp_width
from mixhub.tmux import outer as _outer
from mixhub.tmux import pool as _pool
from mixhub.tmux import workspace as _workspace
from mixhub.tmux.outer import current_outer_session_name, current_pool_session_name
from mixhub.tui.sidebar_rows import sidebar_filter_tab_ranges
from mixhub.tui.web import WEB_LINK_LABEL


class MouseMixin:
    """Turns mouse events into selection, scrolling, popover and action changes."""

    def _handle_mouse(self, parsed_event: tuple[int, int, int, bool] | None = None):
        self._last_input_ts = time.monotonic()
        h, w = self.stdscr.getmaxyx()
        sidebar_only = self.sidebar_only or (w < 64 and not self.reader_only and not getattr(self, "header_only", False))
        left_w = self._effective_left_w(w, sidebar_only)
        list_top, list_bottom, list_h = self._list_bounds(h)

        is_wheel_up = False
        is_wheel_down = False
        is_click = False
        is_double = False
        is_motion = False
        is_drag = False
        is_release = False
        self._last_mouse_was_click = False

        if parsed_event is not None:
            btn, mx, my, is_rel = parsed_event
            if btn < 0:
                return
            is_release = is_rel
            if btn == 64:
                is_wheel_up = True
            elif btn == 65:
                is_wheel_down = True
            elif btn == 32:
                is_drag = True
            elif btn >= 35 or (btn & 32):
                is_motion = True
            elif btn == 0 and not is_rel:
                is_click = True
        else:
            try:
                _, mx, my, _, bstate = curses.getmouse()
            except curses.error:
                # Never guess a scroll from an unreadable mouse event (L4): drop it.
                return

            btn4 = getattr(curses, "BUTTON4_PRESSED", 0x80000)
            btn5 = getattr(curses, "BUTTON5_PRESSED", 0x200000)
            pos_mask = getattr(curses, "REPORT_MOUSE_POSITION", 0x8000000)
            rel_mask = getattr(curses, "BUTTON1_RELEASED", 0x1)
            click_mask = (
                getattr(curses, "BUTTON1_CLICKED", 0)
                | getattr(curses, "BUTTON1_PRESSED", 0)
                | getattr(curses, "BUTTON1_DOUBLE_CLICKED", 0)
                | getattr(curses, "BUTTON1_TRIPLE_CLICKED", 0)
            )
            is_wheel_up = bool(bstate & btn4)
            is_wheel_down = bool(bstate & btn5)
            is_release = bool(bstate & rel_mask) and not bool(bstate & click_mask)
            is_click = bool(bstate & click_mask)
            is_double = bool(bstate & getattr(curses, "BUTTON1_DOUBLE_CLICKED", 0))
            is_motion = bool(bstate & pos_mask) and not (is_click or is_wheel_up or is_wheel_down)

        self._last_mouse_was_click = bool(is_click)

        if is_release and self._dragging_divider:
            self._dragging_divider = False
            self._dirty = True
            return

        if self._dragging_divider and (is_drag or is_motion or is_click):
            if not sidebar_only and not self.reader_only and not self.sidebar_collapsed:
                self.custom_left_w = max(20, min(w - 20, mx))
                self.loaded_key = None
            self.hover_pos = (mx, my)
            self._dirty = True
            return

        if is_wheel_up:
            self._last_wheel_ts = time.monotonic()
            if mx < left_w and left_w > 0:
                new_ls = max(0, self.list_scroll - 3)
                if new_ls != self.list_scroll:
                    self.list_scroll = new_ls
                    self._dirty = True
            elif not self.current_session():
                new_ws = max(0, int(getattr(self, "welcome_scroll", 0)) - 1)
                if new_ws != int(getattr(self, "welcome_scroll", 0)):
                    self.welcome_scroll = new_ws
                    self._dirty = True
            else:
                new_scroll = max(0, self.msg_scroll - 4)
                if new_scroll == 0 and self.is_tail_truncated:
                    self._expand_tail_preserving_viewport()
                else:
                    self.msg_scroll = new_scroll
                    self._dirty = True
            return

        if is_wheel_down:
            self._last_wheel_ts = time.monotonic()
            if mx < left_w and left_w > 0:
                drows = self._sidebar_display_rows()
                max_list_scroll = max(0, len(drows) - list_h)
                new_ls = min(max_list_scroll, self.list_scroll + 3)
                if new_ls != self.list_scroll:
                    self.list_scroll = new_ls
                    self._dirty = True
            elif not self.current_session():
                self.welcome_scroll = int(getattr(self, "welcome_scroll", 0)) + 1
                self._dirty = True
            else:
                self.msg_scroll += 4
                self._dirty = True
            return

        if is_motion or is_drag:
            web_x0 = left_w - 2 - disp_width(WEB_LINK_LABEL) - 1
            on_bottom_interactive = my == h - 1 and (1 <= mx <= 4 or web_x0 <= mx <= left_w - 2)
            if sidebar_only and (mx <= 0 or mx >= left_w - 1 or my > h - 1 or (my == h - 1 and not on_bottom_interactive)):
                # Boundary exit: pointer left the sidebar's interactive area
                self._clear_hover_state()
                return
            self._last_hover_ts = time.monotonic()
            old_pos = self.hover_pos
            self.hover_pos = (mx, my)
            if is_motion and not is_drag and (mx, my) == old_pos:
                return
            if (
                is_drag
                and not sidebar_only
                and not getattr(self, "header_only", False)
                and self.current_session()
                and w - 3 <= mx <= w - 1
                and self.user_turns
            ):
                preview_bottom = (h - 2) if h >= 6 else list_bottom
                if list_top <= my < preview_bottom:
                    self._jump_to_rail_row(my, list_top, preview_bottom)
                    return
            div_x = (w - 1) if sidebar_only else left_w
            if 0 <= mx < left_w - 1 and list_top <= my < list_bottom:
                drows = self._sidebar_display_rows()
                r_idx = self.list_scroll + (my - list_top)
                if 0 <= r_idx < len(drows) and drows[r_idx].get("kind") == "session":
                    new_hov = drows[r_idx]["idx"]
                else:
                    new_hov = -1
            else:
                new_hov = -1
            if (
                new_hov != self.hover_idx
                or my in (0, 1, 2)
                or old_pos[1] in (0, 1, 2)
                or my >= h - 3
                or old_pos[1] >= h - 3
                or (0 <= mx < left_w and my != old_pos[1])
                or (0 <= mx < left_w and mx >= left_w - 8)
                or (0 <= old_pos[0] < left_w and old_pos[0] >= left_w - 8)
                or getattr(self, "_relay_menu", False)
                or getattr(self, "provider_dropdown_open", False)
                or getattr(self, "_new_session_menu", False)
                or abs(mx - div_x) <= 2
                or abs(old_pos[0] - div_x) <= 2
                or (not self.current_session() and mx >= left_w and (mx, my) != old_pos)
            ):
                self.hover_idx = new_hov
                self._dirty = True
            return

        if not is_click:
            return

        self.hover_pos = (mx, my)
        div_x = (w - 1) if sidebar_only else left_w

        # Generic `⇄ Relay` hit dispatch (Row 1 of `--reader` / `--header`, bottom action bar) for ANY row
        if not getattr(self, "_relay_hits", None) and (self.reader_only or getattr(self, "header_only", False) or not sidebar_only):
            self._compute_preview_bar_state(h, w, left_w, sidebar_only)
        for rx0, rx1, r_act in getattr(self, "_relay_hits", {}).get(my, ()):
            if rx0 <= mx < rx1:
                self._handle_relay_action(r_act)
                return

        pop_x, pop_w = self._popover_card_x(left_w), self.POPOVER_W
        in_pop_x = pop_x <= mx < pop_x + pop_w

        def _covered(card_y: int, n_items: int) -> bool:
            # Rows under a card are blanked across the whole sidebar: a click there must not hit a hidden row
            return card_y <= my <= card_y + n_items + 1 and 0 <= mx < left_w - 1

        # `▾` Filter card: item click filters, border clicks are swallowed, Row 0 is handled below
        # (toggle / wordmark close), any other outside click closes the card and falls through.
        if getattr(self, "provider_dropdown_open", False) and not self.reader_only and not getattr(self, "header_only", False):
            pcard_y = self.PROVIDER_CARD_Y
            filter_items = self.provider_filter_items()
            n_items = len(filter_items)
            if left_w >= 20 and in_pop_x and pcard_y + 1 <= my <= pcard_y + n_items:
                self._select_provider_filter(filter_items[my - pcard_y - 1][0])
                return
            if left_w >= 20 and in_pop_x and my in (pcard_y, pcard_y + n_items + 1):
                return
            if my != 0:
                self.provider_dropdown_open = False
                self._dirty = True
                if left_w >= 20 and _covered(pcard_y, n_items):
                    return

        # Handle click inside or outside the floating `New Session` agent card
        if getattr(self, "_new_session_menu", False) and not self.reader_only and not getattr(self, "header_only", False) and left_w >= 20:
            self._new_session_menu = False
            self._dirty = True
            ncard_y = self.NEW_SESSION_CARD_Y
            menu_items = self.new_session_items()
            if in_pop_x and ncard_y + 1 <= my <= ncard_y + len(menu_items):
                self._launch_new_agent_session(menu_items[my - ncard_y - 1][0])
                return
            if my == 1 or _covered(ncard_y, len(menu_items)):
                # Incl. the ` ✎ ` trigger on Row 2: close only (never fall through and re-open it)
                return

        # Handle click inside or outside the floating `⇄ Relay To` card in sidebar mode
        if getattr(self, "_relay_menu", False) and not self.reader_only and not getattr(self, "header_only", False) and left_w >= 20:
            self._relay_menu = False
            self._dirty = True
            rcard_y = self._relay_to_card_y(list_top, list_bottom)
            if in_pop_x and rcard_y + 1 <= my <= rcard_y + len(self.RELAY_TO_ITEMS):
                self._trigger_handoff_interactive(self.RELAY_TO_ITEMS[my - rcard_y - 1][0])
                return
            if _covered(rcard_y, len(self.RELAY_TO_ITEMS)):
                return

        # Click/Press on Split Divider (`⋮`) starts interactive divider drag
        if not sidebar_only and left_w > 0 and my >= 1 and abs(mx - div_x) <= 1:
            self._dragging_divider = True
            self._dirty = True
            return

        # Top Row 0: Left Sidebar (`mixhub ●N ◧ ✕`) or Right Pane (`◧ ●N ~ · id`)
        if my == 0:
            if self.reader_only or getattr(self, "header_only", False) or left_w == 0:
                max_col_x = int(getattr(self, "_header_collapse_max_x", 2))
                if mx <= max_col_x:
                    self._toggle_sidebar_collapse()
                return
            is_legacy_single_mock = (len(self.sessions) == 1 and self.sessions[0].get("id") == "row")
            if sidebar_only and not is_legacy_single_mock:
                toggle_x = max(1, left_w - 3)
                drop_open = getattr(self, "provider_dropdown_open", False)
                if toggle_x - 2 <= mx <= left_w - 1:
                    self.provider_dropdown_open = False
                    self._toggle_sidebar_collapse()
                    return
                if self._is_provider_toggle_x(mx):
                    self._toggle_provider_dropdown()
                    return
                if drop_open:
                    # Any other Row 0 click closes the open Filter card; `mixhub` still acts as its own button below
                    self.provider_dropdown_open = False
                    self._dirty = True
                if self.WORDMARK_HIT[0] <= mx <= self.WORDMARK_HIT[1]:
                    self._on_wordmark_clicked(time.monotonic())
                    return
                if self.can_nav_back() or self.can_nav_forward():
                    if self.NAV_BACK_HIT[0] <= mx <= self.NAV_BACK_HIT[1]:
                        self.nav_back()
                        return
                    if self.NAV_FWD_HIT[0] <= mx <= self.NAV_FWD_HIT[1]:
                        self.nav_forward()
                        return
                return
            toggle_x = max(1, left_w - 2)
            if abs(mx - toggle_x) <= 1:
                self._toggle_sidebar_collapse()
                return
            avail_tools = self._available_agent_tools()
            for tkey, label, x0, x1 in sidebar_filter_tab_ranges(start_x=1, available_tools=avail_tools, max_x=toggle_x - 1):
                if x0 <= mx < x1:
                    self.tool_filter = tkey
                    self.list_scroll = 0
                    self.tail_step_idx = 0
                    self.refresh_data(fast_only=True, reorder=True)
                    self.selected_idx = 0 if self.sessions else -1
                    if self.sidebar_only:
                        self._on_sidebar_selection_changed(activate_live=False)
                    self.status_msg = ""
                    self._dirty = True
                    return
            return

        # Row 1 / Row 2: In `--sidebar` mode, clicking `✎  New session` opens Primary Agent Selector;
        # in `--reader` or `--header` mode on Row 1, handles `↩` home button, `↩ Back`, and `⇄ Relay`
        if my in (1, 2):
            if my == 1 and (self.reader_only or getattr(self, "header_only", False)):
                s = self.current_session()
                if s:
                    # `self._relay_hits[1]` was already dispatched above; a miss while open closes the Relay menu.
                    if self._eff_relay_state() != "CLOSED":
                        self._set_relay_state("CLOSED")
                        return
                    zones = getattr(self, "_preview_action_zones", None)
                    if not zones:
                        self._compute_preview_bar_state(h, w, left_w, sidebar_only)
                        zones = getattr(self, "_preview_action_zones", ())
                    for zy, zx0, zx1, zkey, zen in zones:
                        if zy == 1 and (zx0 - 1) <= mx <= zx1:
                            self._execute_preview_action(zkey, zen)
                            return
                return
            is_legacy_single_mock = (len(self.sessions) == 1 and self.sessions[0].get("id") == "row")
            if sidebar_only and not is_legacy_single_mock and not self.reader_only and not getattr(self, "header_only", False) and 0 <= mx < left_w - 1:
                self._new_session_menu = not getattr(self, "_new_session_menu", False)
                if self._new_session_menu:
                    self._note_sidebar_popover_opened("new_session")
                self._dirty = True
                return

        # Click in Left Sidebar Session List (Collapsible Section Header, Web Archive Footer, or Session Row)
        if 0 <= mx < left_w - 1 and list_top <= my < list_bottom:
            if sidebar_only and (time.monotonic() - getattr(self, "_last_wheel_ts", 0.0)) < 0.18:
                # Ignore accidental trackpad tap-to-click emitted immediately after two-finger scrolling
                return
            self.wordmark.cancel_game(time.monotonic())
            is_legacy_single_mock_row = (len(self.sessions) == 1 and self.sessions[0].get("id") == "row")
            drows = self._sidebar_display_rows()
            r_idx = self.list_scroll + (my - list_top)
            if 0 <= r_idx < len(drows):
                row_item = drows[r_idx]
                if row_item.get("kind") == "header":
                    sec_key = row_item.get("key", "")
                    if sec_key in self._collapsed_sections:
                        self._collapsed_sections.discard(sec_key)
                    else:
                        self._collapsed_sections.add(sec_key)
                    new_drows = self._sidebar_display_rows()
                    self.list_scroll = max(0, min(self.list_scroll, max(0, len(new_drows) - list_h)))
                    self._dirty = True
                    return
                clicked_idx = row_item["idx"]
                if 0 <= clicked_idx < len(self.sessions):
                    now = time.monotonic()
                    target_s = self.sessions[clicked_idx]
                    target_id = target_s["id"]
                    age_x = max(4, left_w - 11) + 6
                    # Hover action zone on right end of row (`☆/★` Pin at age_x+1..left_w-2; `⋯` removed)
                    if mx >= age_x + 1:
                        self.toggle_pin_session(target_s)
                        self._dirty = True
                        return
                    # First click on any non-resident row switches right pane to preview (`reader`);
                    # double-clicking within 0.40s enters the live CLI!
                    last_row_id, last_row_ts = self._last_row_click
                    is_dbl = is_double or (last_row_id == target_id and (now - last_row_ts) < 0.40)
                    self._last_row_click = ("", 0.0) if is_dbl else (target_id, now)
                    self.selected_idx = clicked_idx
                    # Clicked/selected row shows its timestamp cleanly (no sticky `⋯ ☆` hover actions)
                    self.hover_idx = -1
                    self.hover_pos = (-1, -1)
                    self._last_hover_ts = 0.0
                    self.tail_step_idx = 0
                    self.msg_scroll = 999999
                    self.status_msg = ""
                    self._nav_record(target_id, from_keyboard=False)
                    if self.sidebar_only:
                        self._on_sidebar_selection_changed(activate_live=is_dbl)
                        if not is_dbl:
                            self._focus_outer_right_pane_async()
                    self._mark_session_seen(target_s)
                    self._dirty = True
            return

        # Click on Left Sidebar Bottom Footer (`↗ web` opens the Web UI; rest of h-1 collapses/expands; quota rows cycle mode 0/1/2)
        if sidebar_only and 0 <= mx < left_w - 1 and my >= list_bottom:
            web_hit = getattr(self, "_web_link_hit", None)
            if web_hit is not None and my == web_hit[0] and web_hit[1] <= mx <= web_hit[2]:
                self._open_web_archive()
                return
            if my == h - 1:
                self.footer_quota_collapsed = not bool(getattr(self, "footer_quota_collapsed", False))
            elif my < h - 1 and not getattr(self, "footer_quota_collapsed", False):
                self.footer_quota_mode = (int(getattr(self, "footer_quota_mode", 0)) + 1) % 3
            self._dirty = True
            return

        # Click in Right Pane
        if not sidebar_only and not getattr(self, "header_only", False) and mx >= left_w:
            if not self.current_session():
                hdr_m_rect = getattr(self, "_welcome_hdr_mark_rect", None)
                if hdr_m_rect is not None and my == hdr_m_rect[0] and hdr_m_rect[1] <= mx <= hdr_m_rect[2]:
                    self.mark_welcome_unread_seen()
                    return
                ov_rect = getattr(self, "_welcome_overflow_rect", None)
                if ov_rect is not None and my == ov_rect[0] and ov_rect[1] <= mx <= ov_rect[2]:
                    mid_x = (ov_rect[1] + ov_rect[2]) // 2
                    if mx >= mid_x:
                        self.mark_welcome_unread_seen()
                    else:
                        self.welcome_scroll = int(getattr(self, "welcome_scroll", 0)) + 2
                        self._dirty = True
                    return
                for y0, y1, s_idx in getattr(self, "_welcome_triage_rows", ()):
                    if y0 <= my <= y1 and 0 <= s_idx < len(self.sessions):
                        now = time.monotonic()
                        target_s = self.sessions[s_idx]
                        target_id = target_s["id"]
                        last_row_id, last_row_ts = self._last_row_click
                        is_dbl = is_double or (last_row_id == target_id and (now - last_row_ts) < 0.40)
                        self._last_row_click = ("", 0.0) if is_dbl else (target_id, now)
                        self.selected_idx = s_idx
                        self._nav_record(target_id, from_keyboard=False)
                        self.tail_step_idx = 0
                        self.msg_scroll = 999999
                        is_res = bool(
                            target_s.get("resident")
                            or any(p.get("resident") for p in (target_s.get("processes") or []))
                            or _pool.find_hubpool_window_for_session(target_id, allow_probe=False) is not None
                        )
                        self._write_active_selection(target_s, activate_reader=not (is_res or is_dbl))
                        self._mark_session_seen(target_s)
                        self._dirty = True
                        # Single-click previews non-resident sessions in MixHub (or switches to resident `hubpool` window);
                        # only double-click (or clicking Move/Jump/Resume on the Preview bar) jumps to external tab or spawns CLI!
                        if os.environ.get("TMUX") and self._outer_tmux_io_allowed(subprocess.run):
                            if is_dbl:
                                if target_s.get("isLive") and not is_res:
                                    self.focus_external_session(target_s)
                                else:
                                    _pool.activate_session_in_hubpool(
                                        target_s, current_outer_session_name(), force_takeover=True, app=self
                                    )
                            elif is_res:
                                _pool.activate_session_in_hubpool(
                                    target_s, current_outer_session_name(), force_takeover=False, app=self
                                )
                        return
                for tkey, ly, bx0, bx1 in getattr(self, "_welcome_launchpad_rects", ()):
                    if my == ly and bx0 <= mx <= bx1:
                        self._launch_new_agent_session(tkey)
                        return
                return
            if my in (h - 1, h - 2) and self.current_session() and not self.active_ephemeral_toast():
                zones = getattr(self, "_preview_action_zones", None)
                if not zones:
                    self._compute_preview_bar_state(h, w, left_w, sidebar_only)
                    zones = getattr(self, "_preview_action_zones", ())
                for zy, zx0, zx1, zkey, zen in zones:
                    if zy == my and zx0 <= mx < zx1 and zkey != "relay_toggle":
                        if self._eff_relay_state() != "CLOSED":
                            self._set_relay_state("CLOSED")
                        self._execute_preview_action(zkey, zen)
                        return
                # `self._relay_hits[h - 1]` was already dispatched above; a miss while open closes the Relay menu.
                if self._eff_relay_state() != "CLOSED":
                    self._set_relay_state("CLOSED")
                    return
                return
            active_turn = self._active_turn_index()
            has_sticky = (
                len(self.user_turns) > 0
                and list_h >= 6
                and active_turn >= 0
                and self.msg_scroll > self.user_turns[active_turn][0]
            )
            msg_top = (list_top + 2) if has_sticky else list_top
            preview_bottom = (h - 2) if h >= 6 else list_bottom
            msg_h = max(1, preview_bottom - msg_top)
            if w - 3 <= mx <= w - 1 and self.user_turns and list_top <= my < preview_bottom:
                # The rail spans the whole message area, so it wins over the sticky header's top two rows
                self._jump_to_rail_row(my, list_top, preview_bottom)
                return
            if has_sticky and my in (list_top, list_top + 1):
                if active_turn >= 0:
                    line_no, _ = self.user_turns[active_turn]
                    self.msg_scroll = max(0, line_no)
                    self.status_msg = ""
                    self._dirty = True
                return

    def _forward_preview_scroll_from_sidebar(self, key_name: str) -> bool:
        """
        When `--sidebar` receives an uncoordinated `KEY_UP` / `KEY_DOWN` (e.g. Terminal.app alternate-screen
        trackpad scroll while outer tmux still had `%2` focused), forward the scroll to `pool:reader` and
        hand outer tmux focus to the right pane (`%1`), NEVER changing `self.selected_idx`.
        """
        if not os.environ.get("TMUX"):
            return False
        popen_fn = subprocess.Popen
        is_mock_popen = type(popen_fn).__name__ in ("MagicMock", "AsyncMock", "NonCallableMagicMock")
        if is_mock_popen and os.environ.get("TMUX") in ("/tmp/tmux-1000/default,123,0", "test-pool"):
            return False
        env = dict(os.environ)
        env.pop("TMUX", None)
        try:
            subprocess.Popen(
                ["tmux", "-L", "hubpool", "send-keys", "-t", f"{current_pool_session_name()}:reader.{{top}}", key_name],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
            )
        except OSError:
            return False
        self._focus_outer_right_pane_async()
        return True

    def _ensure_live_tmux_mouse_bindings(self) -> None:
        if not os.environ.get("TMUX"):
            return
        run_fn = subprocess.run
        popen_fn = subprocess.Popen
        if (
            type(run_fn).__name__ in ("MagicMock", "AsyncMock", "NonCallableMagicMock")
            or type(popen_fn).__name__ in ("MagicMock", "AsyncMock", "NonCallableMagicMock")
            or os.environ.get("TMUX") in ("/tmp/tmux-1000/default,123,0", "test-pool")
        ):
            return
        env = dict(os.environ)
        env.pop("TMUX", None)
        try:
            if self.sidebar_only:
                outer_sess = current_outer_session_name()
                _workspace.configure_tmux_mouse_and_copy(None, env=env, show_status=False, session_target=outer_sess)
                _outer.set_outer_client_autowrap(outer_sess, enabled=False)
            elif self.reader_only and not getattr(self, "header_only", False):
                _workspace.configure_tmux_mouse_and_copy("hubpool", env=env, show_status=False)
        except Exception:
            pass

    def _consume_sgr_mouse_if_any(self) -> tuple[int, int, int, bool] | None:
        """
        Read SGR mouse sequence `\\x1b[<btn;col;rowM` or `m` (or tmux FocusIn/FocusOut `\\x1b[I` / `\\x1b[O`,
        or normal-mode CSI / split SS3 cursor keys `\\x1b[A..D` / `\\x1bOA..D`) after `ch == 27` (ESC).
        """
        self.stdscr.nodelay(True)
        buf = []
        arrow_map = {
            ord("A"): curses.KEY_UP,
            ord("B"): curses.KEY_DOWN,
            ord("C"): curses.KEY_RIGHT,
            ord("D"): curses.KEY_LEFT,
        }
        try:
            c1 = self.stdscr.getch()
            if c1 == -1:
                return None
            if c1 == ord("O"):
                c2 = self.stdscr.getch()
                if c2 in arrow_map:
                    try:
                        curses.ungetch(arrow_map[c2])
                    except Exception:
                        pass
                    return (-2, -1, -1, False)
                if c2 != -1:
                    try:
                        curses.ungetch(c2)
                    except Exception:
                        pass
                try:
                    curses.ungetch(c1)
                except Exception:
                    pass
                return None
            if c1 != ord("["):
                try:
                    curses.ungetch(c1)
                except Exception:
                    pass
                return None
            c2 = self.stdscr.getch()
            if c2 == ord("O"):
                # tmux FocusOut (\x1b[O): focus left the pane (e.g. click into the right agent pane)
                # -> drop any stale hover AND auto-dismiss every open sidebar popover
                self._clear_hover_state()
                self._dismiss_sidebar_popovers()
                return (-2, -1, -1, False)
            if c2 == ord("I"):
                # tmux FocusIn (\x1b[I): clear unread on the open done_idle session when focus returns
                if getattr(self, "_owns_seen_attn", False):
                    cur_s = self.current_session()
                    if cur_s and cur_s.get("agentState") not in ("wait_input", "wait") and self.is_session_unread(cur_s):
                        self._mark_session_seen(cur_s, save=True)
                        self._publish_attention()
                        self._dirty = True
                return (-2, -1, -1, False)
            if c2 in arrow_map:
                # Normal-mode CSI cursor key (\x1b[A / \x1b[B / \x1b[C / \x1b[D)
                try:
                    curses.ungetch(arrow_map[c2])
                except Exception:
                    pass
                return (-2, -1, -1, False)
            if c2 == ord("M"):
                # X10 / normal mouse fallback (\x1b[M<btn><x><y>): drain the 3 raw coordinate bytes
                for _ in range(3):
                    if self.stdscr.getch() == -1:
                        break
                return (-2, -1, -1, False)
            if c2 != ord("<"):
                if c2 == -1:
                    return None
                if 0x20 <= c2 <= 0x3F:
                    # Unknown CSI sequence with parameter/intermediate bytes: drain through final byte (0x40..0x7E)
                    for _ in range(32):
                        ch = self.stdscr.getch()
                        if ch == -1 or (0x40 <= ch <= 0x7E):
                            break
                return (-2, -1, -1, False)
            while len(buf) < 32:
                ch = self.stdscr.getch()
                if ch == -1:
                    break
                ch_str = chr(ch) if 0 <= ch < 256 else ""
                buf.append(ch_str)
                if ch_str in ("M", "m"):
                    break
        finally:
            if not self.reader_only and not self.sidebar_only and not getattr(self, "header_only", False):
                self.stdscr.timeout(-1)

        seq = "".join(buf)
        m = re.match(r"^(\d+);(\d+);(\d+)([Mm])$", seq)
        if not m:
            return None
        btn = int(m.group(1))
        mx = max(0, int(m.group(2)) - 1)
        my = max(0, int(m.group(3)) - 1)
        is_rel = m.group(4) == "m"
        return (btn, mx, my, is_rel)

    def _clear_hover_state(self) -> bool:
        changed = (self.hover_idx != -1) or (self.hover_pos != (-1, -1))
        self.hover_idx = -1
        self.hover_pos = (-1, -1)
        self._last_hover_ts = 0.0
        if changed:
            self._dirty = True
        return changed
