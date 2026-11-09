#!/usr/bin/env python3
"""`mixhub.tui.hubtui.draw`: Frame drawing for every mode (sidebar, reader, header, legacy single pane) and presenting it."""
from __future__ import annotations

try:
    import curses
except ImportError:  # Windows without windows-curses installed
    curses = None
import os
import time

from mixhub.core.cli_version import old_version_hint
from mixhub.core.textwidth import disp_trunc, disp_width
from mixhub.tui import diag as _diag
from mixhub.tui.badges import (
    SPINNER_FRAMES,
    WORKING_DIAMOND_PERIOD,
    agent_badge_and_pair,
    resolve_sidebar_age_pair,
)
from mixhub.tui.core.style import (
    P_AGENT_AGY,
    P_AGENT_AGY_HOV,
    P_AGENT_CLAUDE,
    P_AGENT_CLAUDE_HOV,
    P_AGENT_CODEX,
    P_AGENT_CODEX_HOV,
    P_AGENT_OPENCODE,
    P_AGENT_OPENCODE_HOV,
    P_BORDER,
    P_CODE,
    P_DIM,
    P_DONE,
    P_HOVER,
    P_HOVER_DIM,
    P_META,
    P_STAT_LBL,
    P_STAT_T3,
    P_STAT_T4,
    P_UNSEL,
    P_USER_BOX,
    P_WAIT,
    P_WAIT_HOV,
    P_WHITE,
    resolve_title_fade_pair,
)
from mixhub.tui.quota import get_cli_quota_summary
from mixhub.tui.sidebar_rows import layout_sidebar_row_32, render_sidebar_row_cells, sidebar_filter_tab_ranges
from mixhub.tui.web import WEB_LINK_LABEL
from mixhub.tui.widgets.preview_bar import MSG_MOVE_RACE


class DrawMixin:
    """Renders one frame and flushes it to the terminal."""

    def draw(self):
        _t0_draw = time.monotonic()
        _orig_cp = curses.color_pair
        _cp_memo: dict[int, int] = {}

        def _fast_cp(pair_id: int, _memo: dict[int, int] = _cp_memo, _orig=_orig_cp) -> int:
            val = _memo.get(pair_id)
            if val is None:
                val = _orig(pair_id)
                _memo[pair_id] = val
            return val

        curses.color_pair = _fast_cp
        try:
            self._draw_inner()
        finally:
            curses.color_pair = _orig_cp
            _draw_elapsed = time.monotonic() - _t0_draw
            if _draw_elapsed > 0.200:
                _cur_s = self.current_session()
                _diag.log_tui_diag(
                    "slow_draw",
                    elapsed_ms=round(_draw_elapsed * 1000.0, 1),
                    mode=self._diag_mode_label(),
                    session_id=(str(_cur_s.get("id", "")) if _cur_s else ""),
                )

    def _present(self) -> None:
        """
        Present the virtual screen (`newscr`) via ncurses diffing against `curscr`.
        Only forces a full-screen repaint (`clearok` / `redrawwin`) when `self._force_full_repaint`
        was explicitly set by a terminal resize (`_sync_terminal_size` / `KEY_RESIZE`) or `Ctrl+L`.
        """
        loaded = getattr(self, "loaded_key", None) if self.reader_only else None
        shown_key = tuple(loaded[:2]) if loaded else None  # (tool, id): a new message alone is not a switch
        if shown_key is not None and shown_key != getattr(self, "_presented_key", None):
            # New preview content: the cell diff can leave ambiguous-width glyphs behind, so rewrite every
            # cell once (`redrawwin`, no blank `clear` frame)
            if getattr(self, "_presented_key", None) is not None and hasattr(self.stdscr, "redrawwin"):
                try:
                    self.stdscr.redrawwin()
                except Exception:
                    pass
            self._presented_key = shown_key
        if getattr(self, "_force_full_repaint", False):
            self._force_full_repaint = False
            if hasattr(self.stdscr, "clearok"):
                try:
                    self.stdscr.clearok(True)
                except Exception:
                    pass
            elif hasattr(self.stdscr, "redrawwin"):
                try:
                    self.stdscr.redrawwin()
                except Exception:
                    pass
        self.stdscr.refresh()
        self._dirty = False

    def _draw_inner(self):
        self._sync_terminal_size()
        self.stdscr.erase()
        h, w = self.stdscr.getmaxyx()
        if self.sidebar_only:
            self._schedule_debounced_sidebar_rebalance(w)
            h, w = self.stdscr.getmaxyx()
        now_mono = time.monotonic()
        if (
            (self.hover_idx != -1 or self.hover_pos != (-1, -1))
            and getattr(self, "_last_hover_ts", 0.0) > 0.0
            and (now_mono - self._last_hover_ts) >= 1.8
        ):
            self.hover_idx = -1
            self.hover_pos = (-1, -1)
            self._last_hover_ts = 0.0
        self._check_sidebar_popover_focus_loss(now_mono)
        hx, hy = self.hover_pos
        self._spinner_frame = int(now_mono / WORKING_DIAMOND_PERIOD) % len(SPINNER_FRAMES)
        c = self.counts
        work_n = c.get("working", 0)

        if not getattr(self, "header_only", False) and (h < 6 or w < 14):
            try:
                self.stdscr.addstr(max(0, h // 2), max(0, w // 2), "◇", curses.color_pair(P_DIM))
            except curses.error:
                pass
            self._present()
            return

        sidebar_only = self.sidebar_only or (w < 64 and not self.reader_only and not getattr(self, "header_only", False))
        left_w = self._effective_left_w(w, sidebar_only)
        is_legacy_single_mock = (len(self.sessions) == 1 and self.sessions[0].get("id") == "row")
        pstate = self._compute_preview_bar_state(h, w, left_w, sidebar_only)
        hover_key = pstate.get("hover_key")

        # 1. Right Pane Top Bar (`--reader` / `--header`): `◧   ●N` ONLY when sidebar is collapsed/zoomed, Right `↩` + `↩ Back`/`▶ Resume`/`↙ Move Here` + `⇄ Relay`
        if self.reader_only or getattr(self, "header_only", False):
            s = self.current_session()
            hdr_band_pair = P_HOVER_DIM
            hdr_dim_pair = P_HOVER_DIM
            hdr_title_pair = P_HOVER
            in_tmux = bool(os.environ.get("TMUX"))
            show_collapse_icon = (not in_tmux) or self._is_reader_zoomed(w)
            attn_n = self._count_awaiting_or_unread_sessions() if show_collapse_icon else 0
            badge_str = f"●{attn_n}" if attn_n > 0 else ""
            collapse_hit_w = (6 + len(badge_str)) if badge_str else 3
            self._header_collapse_max_x = collapse_hit_w
            icon_hov = hy == 0 and 0 <= hx <= collapse_hit_w
            icon_attr = (curses.color_pair(P_USER_BOX) | curses.A_BOLD) if icon_hov else curses.color_pair(hdr_dim_pair)
            try:
                self.stdscr.addstr(0, 0, " " * max(1, w - 1), curses.color_pair(hdr_band_pair))
                if h >= 2:
                    self.stdscr.addstr(1, 0, " " * max(1, w - 1), curses.color_pair(hdr_band_pair))
            except curses.error:
                pass
            if show_collapse_icon:
                try:
                    self.stdscr.addstr(0, 1, "◧", icon_attr)
                    if badge_str:
                        b_attr = (curses.color_pair(P_USER_BOX) | curses.A_BOLD) if icon_hov else (curses.color_pair(P_WAIT_HOV) | curses.A_BOLD)
                        self.stdscr.addstr(0, 5, badge_str, b_attr)
                except curses.error:
                    pass
            if s:
                meta_x = (8 + len(badge_str)) if (show_collapse_icon and badge_str) else (5 if show_collapse_icon else 1)
                short_cwd = s.get("shortCwd") or "~"
                meta_part = disp_trunc(f"{short_cwd} · {s['id'][:8]}", max(8, w - meta_x - 2))
                b_ch, b_pair, _ = agent_badge_and_pair(s)
                b_hov_pair = {
                    P_AGENT_CLAUDE: P_AGENT_CLAUDE_HOV,
                    P_AGENT_AGY: P_AGENT_AGY_HOV,
                    P_AGENT_CODEX: P_AGENT_CODEX_HOV,
                    P_AGENT_OPENCODE: P_AGENT_OPENCODE_HOV,
                    P_META: P_HOVER,
                }.get(b_pair, P_HOVER)
                badge_x = 2 if show_collapse_icon else 1
                title_x = 5 if show_collapse_icon else 3
                try:
                    if meta_x + disp_width(meta_part) < w - 1:
                        self.stdscr.addstr(0, meta_x, meta_part, curses.color_pair(hdr_dim_pair))
                    # Right end of Row 0: a session still on an older Claude Code build (a hint; MixHub never restarts it)
                    ver_hint = old_version_hint(s)
                    ver_x = w - 2 - disp_width(ver_hint)
                    if ver_hint and ver_x >= meta_x + disp_width(meta_part) + 2:
                        self.stdscr.addstr(0, ver_x, ver_hint, curses.color_pair(hdr_dim_pair))
                    if h >= 2:
                        self.stdscr.addstr(1, badge_x, b_ch, curses.color_pair(b_hov_pair) | curses.A_BOLD)
                        hdr_info = pstate.get("hdr") or {}
                        eff_relay = pstate.get("eff_relay", "CLOSED")
                        # `--header` Row 1: badge + title + left-expanding `⇄ Relay`; `--reader` Row 1: badge + title only
                        r_items = hdr_info.get("relay_items") or []
                        title_end = r_items[0][0] if r_items else (w - 2)
                        title_part = disp_trunc(s.get("title") or "", max(8, title_end - title_x - 2))
                        self.stdscr.addstr(1, title_x, title_part, curses.color_pair(hdr_title_pair) | curses.A_BOLD)
                        for rx0, r_lbl, r_act in r_items:
                            r_w = disp_width(r_lbl)
                            if rx0 + r_w > w - 1:
                                continue
                            r_hov = hy == 1 and rx0 <= hx < rx0 + r_w
                            r_attr = self._relay_item_attr(
                                r_act, r_hov, eff_relay, bool(pstate.get("has_quota_rec")), on_header_band=True
                            )
                            self.stdscr.addstr(1, rx0, r_lbl, r_attr)
                    # Row 2: Horizontal divider aligned 100% with the left sidebar's Row 2
                    if h >= 3 and not getattr(self, "header_only", False):
                        self.stdscr.addstr(2, 1, "─" * max(1, w - 2), curses.color_pair(P_BORDER))
                except curses.error:
                    pass
            if getattr(self, "header_only", False):
                self._present()
                return
        else:
            if left_w == 0:
                # Collapsed sidebar in single-process dual-pane mode: show `◧   ●N` at top-left
                attn_n = self._count_awaiting_or_unread_sessions()
                badge_str = f"●{attn_n}" if attn_n > 0 else ""
                collapse_hit_w = (6 + len(badge_str)) if badge_str else 3
                self._header_collapse_max_x = collapse_hit_w
                icon_hov = hy == 0 and 0 <= hx <= collapse_hit_w
                icon_attr = (curses.color_pair(P_USER_BOX) | curses.A_BOLD) if icon_hov else curses.color_pair(P_DIM)
                try:
                    self.stdscr.addstr(0, 1, "◧", icon_attr)
                    if badge_str:
                        b_attr = (curses.color_pair(P_USER_BOX) | curses.A_BOLD) if icon_hov else (curses.color_pair(P_WAIT) | curses.A_BOLD)
                        self.stdscr.addstr(0, 5, badge_str, b_attr)
                except curses.error:
                    pass
            else:
                hdr_w = left_w
                cpu_pct = self._get_cached_cpu_percent(now_mono)
                has_manual_live_mem = (
                    "liveMemoryMB" in c
                    and c.get("liveMemoryMB") != getattr(self, "_last_refreshed_live_mem", None)
                )
                if is_legacy_single_mock or has_manual_live_mem:
                    mem_mb = float(c.get("liveMemoryMB", 0.0))
                    tot_mb = 0.0
                    c_lbl_attr, c_val_attr, m_lbl_attr, m_val_attr = self._morandi_stat_attrs(cpu_pct, mem_mb)
                else:
                    sys_m = self._get_cached_system_metrics(now_mono)
                    mem_mb = float(c.get("sysMemoryUsedMB") or sys_m.get("sysMemoryUsedMB") or c.get("liveMemoryMB", 0.0))
                    tot_mb = float(c.get("sysMemoryTotalMB") or sys_m.get("sysMemoryTotalMB") or 0.0)
                    pres_lvl = int(c.get("sysMemoryPressureLevel") or sys_m.get("sysMemoryPressureLevel") or 1)
                    c_lbl_attr, c_val_attr, m_lbl_attr, m_val_attr = self._morandi_stat_attrs(
                        cpu_pct, mem_mb, total_mem_mb=tot_mb, pressure_level=pres_lvl
                    )
                if not (is_legacy_single_mock or has_manual_live_mem) and tot_mb > 0:
                    mem_val = f"{min(99, max(1, int(round(mem_mb * 100.0 / tot_mb))))}%"
                else:
                    mem_val = f"{mem_mb / 1024:.1f}G" if mem_mb >= 1000 else f"{mem_mb:.0f}M"
                w_attr = (curses.color_pair(P_WHITE) | curses.A_BOLD) if work_n > 0 else curses.color_pair(P_DIM)
                w_part = f"◈ {work_n}"

                if not sidebar_only or is_legacy_single_mock:
                    tab_pair_map = {
                        "all": P_META,
                        "claude": P_AGENT_CLAUDE,
                        "agy": P_AGENT_AGY,
                        "codex": P_AGENT_CODEX,
                        "deepseek": P_DONE,
                    }
                    toggle_x = max(1, left_w - 2)
                    avail_tools = self._available_agent_tools()
                    for tkey, tlabel, x0, x1 in sidebar_filter_tab_ranges(start_x=1, available_tools=avail_tools, max_x=toggle_x - 1):
                        if x1 >= toggle_x:
                            break
                        is_act = self.tool_filter == tkey
                        is_tab_hov = hy == 0 and x0 <= hx < x1
                        base_pair = P_WHITE if (tkey == "all" and (is_act or is_tab_hov)) else tab_pair_map.get(tkey, P_META)
                        attr = curses.color_pair(base_pair)
                        if is_act:
                            attr |= curses.A_BOLD | curses.A_UNDERLINE
                        elif is_tab_hov:
                            attr |= curses.A_BOLD
                        try:
                            self.stdscr.addstr(0, x0, tlabel, attr)
                        except curses.error:
                            pass
                    icon_hov = hy == 0 and abs(hx - toggle_x) <= 1
                    icon_attr = (curses.color_pair(P_USER_BOX) | curses.A_BOLD) if icon_hov else curses.color_pair(P_DIM)
                    try:
                        self.stdscr.addstr(0, toggle_x, "◧", icon_attr)
                        self.stdscr.addstr(1, 2, w_part, w_attr)
                        cpu_val_s = f"{cpu_pct}%"
                        mem_x = max(16, hdr_w - disp_width(mem_val) - 2)
                        mem_lbl_x = max(12, mem_x - 4)
                        cpu_val_x = max(9, mem_lbl_x - disp_width(cpu_val_s) - 1)
                        cpu_lbl_x = max(5, cpu_val_x - 4)
                        self.stdscr.addstr(1, cpu_lbl_x, "cpu ", c_lbl_attr)
                        self.stdscr.addstr(1, cpu_val_x, cpu_val_s, c_val_attr)
                        self.stdscr.addstr(1, mem_lbl_x, "mem ", m_lbl_attr)
                        self.stdscr.addstr(1, mem_x, mem_val, m_val_attr)
                        self.stdscr.addstr(2, 1, "─" * max(1, hdr_w - 2), curses.color_pair(P_BORDER))
                    except curses.error:
                        pass
                else:
                    # Clean Top Header on Row 0 with default transparent background (zero bg=235 box, zero Row 1 line, zero ✕ button):
                    # Row 0: `  mixhub` on left, `◧` on right (col hdr_w - 3)
                    # Row 1: Clean blank breathing space
                    # Row 2: Clickable `  ✎  New session` row right above `▾ Pinned` / `▾ Active`
                    #        (open card: ` ✎ ` boxed at cols 1..3 like the Row 0 ` ▾ `; hover: whole label boxed)
                    toggle_x = max(1, hdr_w - 3)
                    icon_hov = hy == 0 and (toggle_x - 1 <= hx <= hdr_w - 1)
                    icon_attr = (curses.color_pair(P_USER_BOX) | curses.A_BOLD) if icon_hov else curses.color_pair(P_DIM)
                    try:
                        self._draw_wordmark_row(now_mono, hx, hy, hdr_w, toggle_x, work_n)
                        self.stdscr.addstr(0, toggle_x, "◧", icon_attr)
                        is_new_hov = (hy in (1, 2)) and 0 <= hx < hdr_w - 1
                        if getattr(self, "_new_session_menu", False):
                            self.stdscr.addstr(2, 1, " ✎ ", curses.color_pair(P_USER_BOX) | curses.A_BOLD)
                            self.stdscr.addstr(2, 5, "New session", curses.color_pair(P_UNSEL))
                        elif is_new_hov:
                            self.stdscr.addstr(2, 1, " ✎  New session ", curses.color_pair(P_USER_BOX) | curses.A_BOLD)
                        else:
                            self.stdscr.addstr(2, 2, "✎  New session", curses.color_pair(P_UNSEL))
                    except curses.error:
                        pass

        list_top, list_bottom, list_h = self._list_bounds(h)

        # 2. Left Sidebar: Collapsible Sections (`Pinned` / `Active` / `Recent` or `Workspace`) + 3-col pill + Hover `⋯ ☆`/`★`
        if left_w > 0:
            drows = self._sidebar_display_rows()
            max_list_scroll = max(0, len(drows) - list_h)
            self.list_scroll = max(0, min(self.list_scroll, max_list_scroll))

            # Blank every list row first: no leftover cell / half CJK wide char from the previous frame bleeds through
            row_blank = " " * max(1, left_w - 1)
            for ry in range(list_top, list_bottom):
                try:
                    self.stdscr.addstr(ry, 0, row_blank, curses.color_pair(P_UNSEL))
                except curses.error:
                    pass

            for i in range(list_h):
                r_idx = self.list_scroll + i
                if r_idx >= len(drows):
                    break
                ry = list_top + i
                row_item = drows[r_idx]
                if row_item.get("kind") == "header":
                    arrow = "▸" if row_item.get("collapsed") else "▾"
                    cnt_str = str(row_item.get("count", 0))
                    is_hdr_hov = hy == ry and 0 <= hx < left_w - 1
                    hdr_attr = (curses.color_pair(P_WHITE) | curses.A_BOLD) if is_hdr_hov else (curses.color_pair(P_META) | curses.A_BOLD)
                    raw_lbl = disp_trunc(str(row_item.get("label", "")), max(4, left_w - len(cnt_str) - 9))
                    try:
                        self.stdscr.addstr(ry, 2, arrow, hdr_attr)
                        self.stdscr.addstr(ry, 5, raw_lbl, hdr_attr)
                        self.stdscr.addstr(ry, 5 + disp_width(raw_lbl) + 1, cnt_str, curses.color_pair(P_DIM))
                    except curses.error:
                        pass
                    continue

                idx = row_item["idx"]
                s = row_item["session"]
                is_sel = idx == self.selected_idx
                is_hov = (idx == self.hover_idx) and not is_sel
                show_row_actions = is_hov or (is_sel and hy == ry and 0 <= hx < left_w - 1)
                s_state = s.get("agentState") or ("working" if s.get("isLive") else "dormant")
                is_working_now = (s_state == "working")
                is_wait = (not is_working_now) and s_state in ("wait_input", "wait")
                is_unread = False if (is_sel or is_working_now) else self.is_session_unread(s)
                row_layout_fn = render_sidebar_row_cells if is_legacy_single_mock else layout_sidebar_row_32
                cells = row_layout_fn(s, left_w=left_w, is_unread=is_unread, now_mono=now_mono)
                # Right-hand timestamp color priority (legacy single mock keeps amber `P_WAIT | A_BOLD`).
                # Otherwise 5-state zero-bold Morandi palette on `age_col` via `STATE_AGE_COLOR` (NEVER `A_BOLD`):
                # 1) wait_input: warm sand yellow 180 (P_STAT_T3, stays 180 when selected);
                # 2) working: steel blue 67 (P_DONE, stays 67 when selected);
                # 3) failed/interrupted unread: warning red 167 (P_STAT_T4);
                # 4) unread: sage moss green 108 (P_STAT_T1);
                # 5) seen: calm dim gray 240 (P_DIM) / 244 (P_META) selected.
                is_failed_unread = bool(
                    is_unread and (s.get("failed") or s.get("interrupted") or s_state in ("failed", "interrupted"))
                )
                if is_wait:
                    age_state_key = "wait_input"
                elif is_working_now:
                    age_state_key = "working"
                elif is_failed_unread:
                    age_state_key = "failed"
                elif is_unread:
                    age_state_key = "unread"
                else:
                    age_state_key = "seen"
                if is_legacy_single_mock:
                    highlight_age = False if is_working_now else cells["show_dot"]
                else:
                    highlight_age = False if is_working_now else (is_wait or is_unread)

                try:
                    sel_band_pair = P_USER_BOX
                    bg_pair = sel_band_pair if is_sel else (P_HOVER if is_hov else P_UNSEL)
                    brand_pair = cells["agent_sel_pair"] if is_sel else (cells["agent_hov_pair"] if is_hov else cells["agent_pair"])
                    if is_sel or is_hov:
                        self.stdscr.addstr(ry, 1, "   ", curses.color_pair(bg_pair))
                    is_recent_bucket = (
                        not is_legacy_single_mock
                        and not s.get("pinned")
                        and not s.get("isLive")
                        and not s.get("inWorkingSet")
                        and s_state not in ("working", "wait_input", "interrupted")
                    )
                    self.stdscr.addstr(
                        ry, cells["dot_x"], cells["dot_ch"],
                        curses.color_pair(brand_pair) | (curses.A_BOLD if (is_sel and not is_recent_bucket) or cells["is_bold"] else 0),
                    )
                    is_bright_title = bool(is_sel or is_hov or highlight_age)
                    bold_bit = curses.A_BOLD if ((is_sel and not is_recent_bucket) or highlight_age) else 0
                    fade_pos = cells.get("t_fade_pos")
                    if (
                        fade_pos
                        and not is_legacy_single_mock
                        and getattr(curses, "COLORS", 8) >= 256
                    ):
                        for seg_txt, fade_step, tx in fade_pos:
                            pair_id = resolve_title_fade_pair(fade_step, is_bright=is_bright_title)
                            self.stdscr.addstr(ry, tx, seg_txt, curses.color_pair(pair_id) | bold_bit)
                    else:
                        title_attr = curses.color_pair(P_WHITE if is_bright_title else P_UNSEL) | bold_bit
                        self.stdscr.addstr(ry, cells["title_x"], cells["t_cell"], title_attr)
                    age_x = cells["age_x"]
                    is_pinned = bool(s.get("pinned"))
                    if show_row_actions:
                        pin_sym = "★" if is_pinned else "☆"
                        pin_hov = hy == ry and (hx >= age_x + 1)
                        if is_legacy_single_mock and not pin_hov:
                            act_attr = (
                                (curses.color_pair(P_WAIT) | curses.A_BOLD)
                                if (highlight_age or is_pinned)
                                else curses.color_pair(P_META)
                            )
                            self.stdscr.addstr(ry, age_x, f"  {pin_sym}", act_attr)
                        else:
                            pin_attr = (curses.color_pair(P_WAIT) | curses.A_BOLD) if (pin_hov or is_pinned) else curses.color_pair(P_META)
                            self.stdscr.addstr(ry, age_x, "  ", curses.color_pair(P_DIM))
                            self.stdscr.addstr(ry, age_x + 2, pin_sym, pin_attr)
                    else:
                        if is_legacy_single_mock:
                            age_attr = (
                                (curses.color_pair(P_WAIT) | curses.A_BOLD)
                                if cells["show_dot"]
                                else curses.color_pair(P_META if is_sel else P_DIM)
                            )
                        else:
                            age_attr = curses.color_pair(resolve_sidebar_age_pair(age_state_key, is_selected=is_sel))
                        self.stdscr.addstr(ry, age_x, cells["age_col"], age_attr)
                except curses.error:
                    pass

            # Floating sidebar popovers, all drawn by `_draw_sidebar_popover_card` with one geometry
            # (`card_x = 4`, `card_w = 22`, no `[1]`..`[5]` prefixes; the number keys still work in `run()`)
            pop_x, pop_w = self._popover_card_x(left_w), self.POPOVER_W

            # `⇄ Relay To` target agent card in sidebar mode
            if getattr(self, "_relay_menu", False) and left_w >= 20:
                relay_items = [(k, lbl, rp, sp, "") for k, lbl, rp, sp in self.RELAY_TO_ITEMS]
                rcard_y = self._relay_to_card_y(list_top, list_bottom)
                self._draw_sidebar_popover_card(rcard_y, pop_x, pop_w, "⇄ Relay To", relay_items, hx, hy, left_w)

            # `New Session` primary agent card (`✎  New session` or `n`); ` ✎ ` stays boxed beside the card
            if getattr(self, "_new_session_menu", False) and left_w >= 20:
                new_items = [(k, lbl, rp, sp, "") for k, lbl, rp, sp in self.new_session_items()]
                ncard_y = self.NEW_SESSION_CARD_Y
                self._draw_sidebar_popover_card(ncard_y, pop_x, pop_w, "New Session", new_items, hx, hy, left_w)
                try:
                    self.stdscr.addstr(ncard_y, 1, " ✎ ", curses.color_pair(P_USER_BOX) | curses.A_BOLD)
                except curses.error:
                    pass

            # `Filter` card under the Row 0 `▾` trigger, `✓` marks the active filter
            if getattr(self, "provider_dropdown_open", False) and left_w >= 20:
                active_key = (self._provider_filter_item() or (None,))[0]
                filter_items = [
                    (k, lbl, rp, sp, "✓" if k == active_key else "") for k, lbl, rp, sp in self.provider_filter_items()
                ]
                self._draw_sidebar_popover_card(
                    self.PROVIDER_CARD_Y, pop_x, pop_w, "Filter", filter_items, hx, hy, left_w
                )

            # Draggable Split Divider Handle (`⋮` in middle rows + full vertical highlight on hover/drag)
            div_x = (w - 1) if sidebar_only else left_w
            is_div_hover = self._dragging_divider or (hy >= 1 and abs(hx - div_x) <= 1)
            mid_y = h // 2
            handle_rows = (mid_y - 1, mid_y, mid_y + 1)
            if not sidebar_only and not self.sidebar_collapsed:
                line_attr = curses.color_pair(P_UNSEL) if is_div_hover else curses.color_pair(P_BORDER)
                handle_attr = (curses.color_pair(P_WHITE) | curses.A_BOLD) if is_div_hover else curses.color_pair(P_META)
                for ry in range(1, h - 1):
                    try:
                        if ry in handle_rows:
                            self.stdscr.addstr(ry, div_x, "⋮", handle_attr)
                        else:
                            self.stdscr.addstr(ry, div_x, "│", line_attr)
                    except curses.error:
                        pass

        # 3. Right Archive Reader Pane (Welcome Screen when unselected, or borderless conversation reader)
        if not sidebar_only:
            right_x = 1 if (self.sidebar_collapsed or self.reader_only) else (left_w + 2)
            right_w = max(10, w - right_x)
            box_w = max(10, right_w - 4)
            cur_s = self.current_session()
            if not cur_s:
                self._draw_welcome_pane(right_x, right_w, list_top, list_bottom)
            else:
                _, brand_pair, _ = agent_badge_and_pair(cur_s)
                tlines = self.build_transcript_lines(right_w)

                if len(tlines) <= list_h:
                    self.msg_scroll = 0
                elif self.msg_scroll > len(tlines):
                    self.msg_scroll = max(0, len(tlines) - max(1, list_h - 2))

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

                max_scroll = max(0, len(tlines) - msg_h)
                self.msg_scroll = max(0, min(self.msg_scroll, max_scroll))

                active_turn = self._active_turn_index()

                if has_sticky and active_turn >= 0:
                    _, active_prompt = self.user_turns[active_turn]
                    turn_badge = f"#{active_turn + 1}/{len(self.user_turns)}"
                    tb_w = disp_width(turn_badge)
                    prompt_Part = disp_trunc(active_prompt, max(10, box_w - tb_w - 4))
                    try:
                        self.stdscr.addstr(list_top, right_x, "❯ ", curses.color_pair(P_META) | curses.A_BOLD)
                        self.stdscr.addstr(list_top, right_x + 2, prompt_Part, curses.color_pair(P_WHITE) | curses.A_BOLD)
                        self.stdscr.addstr(list_top, right_x + box_w - tb_w, turn_badge, curses.color_pair(P_DIM))
                        self.stdscr.addstr(list_top + 1, right_x, "─" * box_w, curses.color_pair(P_BORDER))
                    except curses.error:
                        pass

                for i in range(msg_h):
                    li = self.msg_scroll + i
                    if li >= len(tlines):
                        break
                    ry = msg_top + i
                    kind, text = tlines[li]
                    if kind == "blank" or not text:
                        continue
                    if kind == "user_box":
                        try:
                            self.stdscr.addstr(ry, right_x, "❯ ", curses.color_pair(P_META) | curses.A_BOLD)
                            self.stdscr.addstr(ry, right_x + 2, disp_trunc(text, box_w - 2), curses.color_pair(P_WHITE) | curses.A_BOLD)
                        except curses.error:
                            pass
                    elif kind == "user_cont":
                        try:
                            self.stdscr.addstr(ry, right_x + 2, disp_trunc(text, box_w - 2), curses.color_pair(P_UNSEL))
                        except curses.error:
                            pass
                    elif kind == "heading":
                        try:
                            self.stdscr.addstr(ry, right_x, disp_trunc(text, box_w), curses.color_pair(P_WHITE) | curses.A_BOLD)
                        except curses.error:
                            pass
                    elif kind == "code_line":
                        try:
                            self.stdscr.addstr(ry, right_x, disp_trunc(text, box_w), curses.color_pair(P_CODE))
                        except curses.error:
                            pass
                    elif kind == "meta":
                        try:
                            self.stdscr.addstr(ry, right_x, disp_trunc(text, box_w), curses.color_pair(P_META))
                        except curses.error:
                            pass
                    elif kind == "dim":
                        try:
                            self.stdscr.addstr(ry, right_x, disp_trunc(text, box_w), curses.color_pair(P_DIM))
                        except curses.error:
                            pass
                    else:
                        self._draw_asst_line(ry, right_x, text, box_w)

                # Right-Edge Prompt Turn Minimap Rail (`x = w - 2`, inset 1 col from macOS Terminal's native scrollbar on `w - 1`)
                if self.user_turns and msg_h >= 3 and w - right_x >= 12:
                    rail_x = max(right_x, w - 2)
                    tick_map = self._prompt_rail_ticks(list_top, preview_bottom, len(tlines), active_turn)
                    for ry, t_idx in tick_map.items():
                        is_cur = t_idx == active_turn
                        ch_sym = "━" if is_cur else "─"
                        attr = (curses.color_pair(brand_pair) | curses.A_BOLD) if is_cur else curses.color_pair(P_META)
                        try:
                            self.stdscr.addstr(ry, rail_x, ch_sym, attr)
                        except curses.error:
                            pass

        # 4. Ephemeral Toast (Row h-2, auto-expires in 5s or on any keystroke) & Preview Bottom Action Bar (Mode B / Mode C Buttons-Only + Weekly-Quota Relay)
        toast_txt = self.active_ephemeral_toast()
        if toast_txt and h >= 4:
            try:
                self.stdscr.addstr(h - 2, 1, disp_trunc(toast_txt, max(10, w - 2)), curses.color_pair(P_META))
            except curses.error:
                pass
        elif not sidebar_only and not getattr(self, "header_only", False) and self.current_session() and h >= 6:
            right_x = 1 if (self.sidebar_collapsed or self.reader_only) else (left_w + 2)
            bar_info = pstate.get("bar") or {}
            bar_x = bar_info.get("bar_x", 0 if (self.sidebar_collapsed or self.reader_only) else (left_w + 1))
            has_quota_rec = pstate.get("has_quota_rec", False)
            eff_relay = pstate.get("eff_relay", "CLOSED")
            try:
                # Minimalist 1-line transparent action bar on `Row h - 1` (thin `─` rule on `Row h - 2`)
                self.stdscr.addstr(h - 2, right_x, "─" * max(1, w - right_x - 1), curses.color_pair(P_BORDER))
                for pill_lbl, px0, px1, p_act, p_en, p_foc in (bar_info.get("pills") or ()):
                    if px1 > w - 1:
                        continue
                    if p_act == "relay_toggle" and eff_relay != "CLOSED":
                        p_attr = curses.color_pair(P_WHITE) | curses.A_BOLD
                    elif not p_en:
                        p_attr = curses.color_pair(P_DIM)
                    elif p_foc:
                        p_attr = curses.color_pair(P_USER_BOX) | curses.A_BOLD
                    elif p_act == "relay_toggle" and has_quota_rec:
                        p_attr = curses.color_pair(P_STAT_T4) | curses.A_BOLD
                    else:
                        p_attr = curses.color_pair(P_META)
                    self.stdscr.addstr(h - 1, px0, pill_lbl, p_attr)

                for rx0, r_lbl, r_act in (bar_info.get("relay_items") or ()):
                    if r_act == "relay_toggle":
                        continue
                    r_w = disp_width(r_lbl)
                    if rx0 + r_w > w - 1:
                        continue
                    r_hov = hy == (h - 1) and rx0 <= hx < rx0 + r_w
                    r_attr = self._relay_item_attr(r_act, r_hov, eff_relay, has_quota_rec)
                    self.stdscr.addstr(h - 1, rx0, r_lbl, r_attr)

                if self.status_msg == MSG_MOVE_RACE and eff_relay == "CLOSED":
                    race_x = bar_info.get("left_chips_end", bar_x + 2) + 2
                    if race_x + disp_width(MSG_MOVE_RACE) < w - 1:
                        self.stdscr.addstr(h - 1, race_x, MSG_MOVE_RACE, curses.color_pair(P_STAT_T3))
            except curses.error:
                pass
        self._web_link_hit = None
        # Never render status_msg at the bottom-left of `--sidebar` unless a search query filter is active
        if (
            self.status_msg
            and not self.status_msg.startswith("#")
            and self.status_msg != MSG_MOVE_RACE
            and not self.reader_only
            and (not sidebar_only or bool(self.query))
        ):
            try:
                self.stdscr.addstr(h - 1, 0, disp_trunc(f" {self.status_msg}", w - 1), curses.color_pair(P_DIM))
            except curses.error:
                pass
        elif left_w >= 14 and not is_legacy_single_mock and not self.query and h >= 12:
            if sidebar_only:
                # Bottom Row h-1: Collapsible quota toggle (`▴` expanded upward / `▸` collapsed) + Morandi 4-tier zero-bold `cpu xx%  mem xx%` (zero `━──` bars)
                q_collapsed = bool(getattr(self, "footer_quota_collapsed", False))
                tog_sym = "▸" if q_collapsed else "▴"
                tog_hov = hy == h - 1 and 0 <= hx <= 4
                tog_attr = (curses.color_pair(P_WHITE) | curses.A_BOLD) if tog_hov else curses.color_pair(P_DIM)
                cpu_val_s = f"{cpu_pct}%"
                try:
                    self.stdscr.addstr(h - 1, 2, tog_sym, tog_attr)
                    self.stdscr.addstr(h - 1, 4, "cpu ", c_lbl_attr)
                    self.stdscr.addstr(h - 1, 8, cpu_val_s, c_val_attr)
                    mem_lbl_x = 8 + disp_width(cpu_val_s) + 2
                    mem_val_x = mem_lbl_x + 4
                    stat_end = 8 + disp_width(cpu_val_s)
                    if mem_val_x + disp_width(mem_val) < left_w:
                        self.stdscr.addstr(h - 1, mem_lbl_x, "mem ", m_lbl_attr)
                        self.stdscr.addstr(h - 1, mem_val_x, mem_val, m_val_attr)
                        stat_end = mem_val_x + disp_width(mem_val)
                    # `↗ web` right-aligned to col `left_w - 3` (col 29: same right edge as `◧` and the quota `10/28`),
                    # dim like the `cpu`/`mem` labels; opens the Web UI on the selected session (root on Home)
                    web_x = left_w - 2 - disp_width(WEB_LINK_LABEL)
                    if web_x >= stat_end + 2:
                        web_hov = hy == (h - 1) and (web_x - 1) <= hx <= (left_w - 2)
                        if web_hov:
                            self.stdscr.addstr(
                                h - 1, web_x - 1, f" {WEB_LINK_LABEL} ", curses.color_pair(P_USER_BOX) | curses.A_BOLD
                            )
                        else:
                            self.stdscr.addstr(h - 1, web_x, WEB_LINK_LABEL, curses.color_pair(P_STAT_LBL))
                        self._web_link_hit = (h - 1, web_x - 1, left_w - 2)
                except curses.error:
                    pass
                # Row h-3 is fixed blank breathing space; Row h-2 renders zero-text Occam's Razor quota (clickable to cycle mode 0/1/2)
                if not q_collapsed and h >= 14:
                    self._draw_footer_quota(h, left_w)
            else:
                q_bar = get_cli_quota_summary(max_w=max(10, left_w - 3), home_dir=getattr(self.hub, "home", None))
                if q_bar:
                    try:
                        self.stdscr.addstr(h - 1, 2, disp_trunc(q_bar, max(8, left_w - 3)), curses.color_pair(P_DIM))
                    except curses.error:
                        pass
        self._present()
