#!/usr/bin/env python3
"""`mixhub.tui.hubtui.home`: The Home (welcome) pane shown in the reader when no session is selected."""
from __future__ import annotations

try:
    import curses
except ImportError:  # Windows without windows-curses installed
    curses = None

from mixhub.core.textwidth import clean_single_line, disp_trunc, disp_width
from mixhub.tui.badges import agent_badge_and_pair
from mixhub.tui.core.style import (
    P_AGENT_AGY,
    P_AGENT_AGY_SEL,
    P_AGENT_CLAUDE,
    P_AGENT_CLAUDE_SEL,
    P_AGENT_CODEX,
    P_AGENT_CODEX_SEL,
    P_BORDER,
    P_DIM,
    P_ERR,
    P_META,
    P_USER_BOX,
    P_WARN,
    P_WHITE,
)
from mixhub.tui.sidebar_rows import rel_time


class HomeMixin:
    """Draws the Home dashboard and clears the unread marks it lists."""

    def mark_welcome_unread_seen(self) -> int:
        """Mark all `unread` sessions (excluding `wait_input` awaiting approval) as seen from the Welcome screen."""
        cleared = 0
        for s in self.sessions:
            if s.get("agentState") in ("wait_input", "wait"):
                continue
            if self.is_session_unread(s):
                self._mark_session_seen(s)
                cleared += 1
        self.welcome_scroll = 0
        if cleared > 0:
            self.status_msg = f"Cleared {cleared} unread"
        self._dirty = True
        return cleared

    def _welcome_triage_items(self) -> list[tuple[int, dict, str]]:
        """`(index, session, "awaiting" | "unread")` for `NEEDS YOU`, awaiting first. The reader pane takes the
        sidebar's published verdict: its own rescan can run before its screen probe sees an approval prompt."""
        published = None
        if self.reader_only and not self.sidebar_only:
            published = self._published_needs_you()
        items: list[tuple[int, dict, str]] = []
        for idx, s in enumerate(self.sessions):
            if str(s.get("id", "")).startswith("new-"):
                continue
            if published is not None:
                key = self._session_key(s)
                kind = "awaiting" if key in published[1] else ("unread" if key in published[0] else "")
            elif s.get("agentState") in ("wait_input", "wait"):
                kind = "awaiting"
            else:
                kind = "unread" if self.is_session_unread(s) else ""
            if kind:
                items.append((idx, s, kind))
        items.sort(key=lambda item: (0 if item[2] == "awaiting" else 1, item[0]))
        return items

    @staticmethod
    def _launch_label(key: str, name: str) -> str:
        return f"[ + {'Claude' if key == 'claude' else name} ]"

    def _draw_welcome_pane(self, right_x: int, right_w: int, top_y: int, bottom_y: int):
        """
        Render the Swiss-Grid 60-col Character-Level Refined Home:
          - ~25% top golden whitespace + centered 60-col Swiss grid (`mixhub` left, `X waiting   Y unread` right).
          - `NEEDS YOU` section with Dual-Tier Adaptive Density + Overflow Footer + Wheel Scroll:
              * `<= 4` items: spacious 2-line mode (`◇ Title ... age` + indented `› ...` on line 2).
              * `>= 5` items: first <=3 `awaiting` items stay 2-line, remaining `awaiting` and all `unread` items
                compress to 1-line compact mode (`◇ Title  › snippet  2h`), with overflow footer
                `▾ +N more (X waiting, Y unread) · scroll ↕` when items exceed viewport.
          - Bottom `LAUNCH` bar: one `[ + Agent ]` per agent in `✎ New session`, most used first.
        """
        self._welcome_triage_rows: list[tuple[int, int, int]] = []
        self._welcome_launchpad_rects: list[tuple[str, int, int, int]] = []
        self._welcome_overflow_rect: tuple[int, int, int] | None = None
        hx, hy = self.hover_pos

        triage_items = self._welcome_triage_items()
        wait_n = sum(1 for _, _, kind in triage_items if kind == "awaiting")
        unread_n = len(triage_items) - wait_n

        total_triage = len(triage_items)
        compact_mode = total_triage >= 5
        avail_h = max(8, bottom_y - top_y)
        # Wide enough for the LAUNCH row (label + every `[ + Agent ]`), so it never runs past the divider lines
        launch_w = 10 + sum(disp_width(self._launch_label(row[0], row[1])) + 3 for row in self.new_session_items()) - 3
        box_w = min(max(60, launch_w), max(24, right_w - 8))
        pad_x = right_x + max(2, (right_w - box_w) // 2)
        if not compact_mode:
            start_y = top_y + max(1, min(avail_h // 4, max(1, avail_h - (10 + total_triage * 2))))
        else:
            est_rows = min(max(4, avail_h - 7), total_triage + min(3, wait_n) + 1)
            start_y = top_y + max(1, min(avail_h // 5, max(1, avail_h - (7 + est_rows))))

        try:
            # Swiss-Grid Header Row: left `mixhub`, right `X waiting   Y unread`
            self.stdscr.addstr(start_y, pad_x, "mixhub", curses.color_pair(P_WHITE) | curses.A_BOLD)
            summary_str = f"{wait_n} waiting   {unread_n} unread" if wait_n or unread_n else "all clear"
            sum_x = max(pad_x + 10, pad_x + box_w - disp_width(summary_str))
            if sum_x + disp_width(summary_str) < right_x + right_w:
                if not wait_n and not unread_n:
                    self.stdscr.addstr(start_y, sum_x, "all clear", curses.color_pair(P_DIM))
                else:
                    waiting = f"{wait_n} waiting"
                    self.stdscr.addstr(start_y, sum_x, waiting, curses.color_pair(P_ERR if wait_n else P_DIM))
                    self.stdscr.addstr(start_y, sum_x + disp_width(waiting), "   ", curses.color_pair(P_DIM))
                    self.stdscr.addstr(start_y, sum_x + disp_width(waiting) + 3,
                                       f"{unread_n} unread", curses.color_pair(P_WARN if unread_n else P_DIM))
            if start_y + 1 < bottom_y - 2:
                self.stdscr.addstr(start_y + 1, pad_x, "─" * box_w, curses.color_pair(P_BORDER))

            cur_y = start_y + 2
            self._welcome_hdr_mark_rect: tuple[int, int, int] | None = None
            if cur_y < bottom_y - 3:
                self.stdscr.addstr(cur_y, pad_x, "NEEDS YOU", curses.color_pair(P_DIM))
                if unread_n >= 2:
                    mark_lbl = f"✓ mark {unread_n} read"
                    mw = disp_width(mark_lbl)
                    mx0 = max(pad_x + 12, pad_x + box_w - mw)
                    if mx0 + mw < right_x + right_w:
                        is_mhov = (hy == cur_y) and (mx0 <= hx < mx0 + mw)
                        mattr = (curses.color_pair(P_USER_BOX) | curses.A_BOLD) if is_mhov else curses.color_pair(P_DIM)
                        self.stdscr.addstr(cur_y, mx0, mark_lbl, mattr)
                        self._welcome_hdr_mark_rect = (cur_y, mx0, mx0 + mw)
                cur_y += 1

            max_triage_y = bottom_y - 2
            rows_avail = min(9, max(2, max_triage_y - cur_y))

            if triage_items:
                # Assign line height per item:
                # - total <= 4: 2 lines each (if rows_avail allows)
                # - total >= 5: first <=3 awaiting items get 2 lines (if rows_avail >= 7), rest get 1 line
                item_heights: list[int] = []
                await_two_line_budget = 3 if (compact_mode and rows_avail >= 7) else (4 if not compact_mode else 0)
                await_used = 0
                for _, _, kind in triage_items:
                    if not compact_mode and total_triage * 2 <= rows_avail:
                        item_heights.append(2)
                    elif kind == "awaiting" and await_used < await_two_line_budget:
                        item_heights.append(2)
                        await_used += 1
                    else:
                        item_heights.append(1)

                raw_scroll = max(0, int(getattr(self, "welcome_scroll", 0)))
                max_scroll = max(0, total_triage - 1)
                scroll_idx = min(raw_scroll, max_scroll)

                # Determine whether all items fit without scrolling
                total_needed_rows = sum(item_heights)
                needs_overflow = (total_needed_rows > rows_avail) or (scroll_idx > 0)
                row_budget = max(1, rows_avail - (1 if needs_overflow else 0))

                # Clamp scroll_idx back if near the end so the viewport stays full
                if scroll_idx > 0:
                    while scroll_idx > 0 and sum(item_heights[scroll_idx - 1 :]) <= row_budget:
                        scroll_idx -= 1
                self.welcome_scroll = scroll_idx

                vis_indices: list[int] = []
                used_r = 0
                for ti in range(scroll_idx, total_triage):
                    ih = item_heights[ti]
                    if used_r + ih > row_budget:
                        if ih == 2 and used_r + 1 <= row_budget:
                            item_heights[ti] = 1
                            ih = 1
                        else:
                            break
                    vis_indices.append(ti)
                    used_r += ih

                for ti in vis_indices:
                    idx, s, kind = triage_items[ti]
                    ih = item_heights[ti]
                    if cur_y + ih - 1 >= max_triage_y:
                        break
                    is_hov = (cur_y <= hy <= cur_y + ih - 1) and (pad_x <= hx < pad_x + box_w)
                    b_ch, b_pair, _ = agent_badge_and_pair(s)
                    age_s = rel_time(s.get("updatedAt", 0)) or "now"
                    right_tag = f"{age_s:>3s}"
                    tag_attr = curses.color_pair(P_USER_BOX if is_hov else (P_ERR if kind == "awaiting" else P_WARN))
                    rtag_w = disp_width(right_tag)
                    row_attr = (curses.color_pair(P_USER_BOX) | curses.A_BOLD) if is_hov else (curses.color_pair(P_WHITE) | curses.A_BOLD)
                    ask_line = clean_single_line(
                        s.get("lastPrompt") or s.get("preview") or s.get("firstPrompt") or "Awaiting confirmation or next instruction"
                    )

                    if ih == 2:
                        t_title = disp_trunc(s.get("title") or s.get("id", "")[:8], max(10, box_w - rtag_w - 6))
                        self.stdscr.addstr(cur_y, pad_x, b_ch, curses.color_pair(b_pair) | curses.A_BOLD)
                        self.stdscr.addstr(cur_y, pad_x + 2, t_title, row_attr)
                        self.stdscr.addstr(cur_y, pad_x + box_w - rtag_w, right_tag, tag_attr)
                        ask_trunc = disp_trunc(f"› {ask_line}", max(10, box_w - 4))
                        self.stdscr.addstr(cur_y + 1, pad_x + 2, ask_trunc, curses.color_pair(P_META))
                        self._welcome_triage_rows.append((cur_y, cur_y + 1, idx))
                        cur_y += 2
                    else:
                        # Compact 1-line mode: `◇ Title   › snippet   2h`
                        avail_mid = max(10, box_w - rtag_w - 4)
                        raw_title = s.get("title") or s.get("id", "")[:8]
                        title_budget = min(max(14, avail_mid // 2), avail_mid)
                        t_title = disp_trunc(raw_title, title_budget)
                        tw = disp_width(t_title)
                        self.stdscr.addstr(cur_y, pad_x, b_ch, curses.color_pair(b_pair) | curses.A_BOLD)
                        self.stdscr.addstr(cur_y, pad_x + 2, t_title, row_attr)
                        rem_snip_w = avail_mid - tw - 2
                        if rem_snip_w >= 12 and ask_line:
                            snip_s = disp_trunc(f"› {ask_line}", rem_snip_w)
                            self.stdscr.addstr(cur_y, pad_x + 2 + tw + 2, snip_s, curses.color_pair(P_META))
                        self.stdscr.addstr(cur_y, pad_x + box_w - rtag_w, right_tag, tag_attr)
                        self._welcome_triage_rows.append((cur_y, cur_y, idx))
                        cur_y += 1

                hidden_indices = [i for i in range(total_triage) if i not in set(vis_indices)]
                if hidden_indices and cur_y < max_triage_y:
                    h_wait = sum(1 for i in hidden_indices if triage_items[i][2] == "awaiting")
                    h_unread = len(hidden_indices) - h_wait
                    arrow_ch = "▾" if (vis_indices and vis_indices[-1] < total_triage - 1) else "▴"
                    ov_txt = f"{arrow_ch} +{len(hidden_indices)} more ({h_wait} waiting, {h_unread} unread) · scroll ↕"
                    ov_trunc = disp_trunc(ov_txt, max(12, box_w))
                    is_ov_hov = (hy == cur_y) and (pad_x <= hx < pad_x + box_w)
                    ov_attr = (curses.color_pair(P_USER_BOX) | curses.A_BOLD) if is_ov_hov else curses.color_pair(P_DIM)
                    self.stdscr.addstr(cur_y, pad_x, ov_trunc, ov_attr)
                    self._welcome_overflow_rect = (cur_y, pad_x, pad_x + box_w)
                    cur_y += 1
            elif cur_y < bottom_y - 2:
                empty_msg = "◇  All clear — no sessions awaiting approval or unread"
                self.stdscr.addstr(cur_y, pad_x + 2, disp_trunc(empty_msg, max(12, box_w - 4)), curses.color_pair(P_META))
                cur_y += 2

            launch_y = min(bottom_y - 1, max(cur_y + 1, start_y + 6))
            if launch_y < bottom_y:
                if launch_y - 1 >= cur_y and launch_y - 1 > start_y + 2:
                    self.stdscr.addstr(launch_y - 1, pad_x, "─" * box_w, curses.color_pair(P_BORDER))
                self.stdscr.addstr(launch_y, pad_x, "LAUNCH", curses.color_pair(P_DIM))
                # Hover keeps the brand foreground (`P_AGENT_*_SEL`: brand fg on the bg 237 hover box)
                # Same agents and order as `✎ New session`
                specs = [(key, self._launch_label(key, name), pair, sel_pair)
                         for key, name, pair, sel_pair in self.new_session_items()]
                if getattr(self, "_no_agent_cli", False):
                    specs = []
                    hint = "No agent CLI found: install Claude Code, Codex, Gemini CLI or OpenCode"
                    self.stdscr.addstr(launch_y, pad_x + 10, disp_trunc(hint, max(12, box_w - 10)), curses.color_pair(P_META))
                gap = 3
                bx = pad_x + 10
                for tkey, lbl, bpair, bsel_pair in specs:
                    bw = disp_width(lbl)
                    if bx + bw >= right_x + right_w:
                        break
                    is_bhov = (hy == launch_y) and (bx <= hx < bx + bw)
                    battr = curses.color_pair(bsel_pair if is_bhov else bpair) | curses.A_BOLD
                    self.stdscr.addstr(launch_y, bx, lbl, battr)
                    self._welcome_launchpad_rects.append((tkey, launch_y, bx, bx + bw))
                    bx += bw + gap
        except curses.error:
            pass
