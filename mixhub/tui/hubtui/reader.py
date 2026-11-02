#!/usr/bin/env python3
"""`mixhub.tui.hubtui.reader`: Transcript preview: progressive tail loading, transcript lines, turn rail, and the preview action bar."""
from __future__ import annotations

try:
    import curses
except ImportError:  # Windows without windows-curses installed
    curses = None
import os
import re
import subprocess

from mixhub.core.textwidth import clean_single_line, disp_trunc, disp_width, disp_wrap
from mixhub.tmux import pool as _pool
from mixhub.tmux.outer import current_outer_session_name, current_pool_session_name
from mixhub.tmux.pool import _session_preview_mode_and_state
from mixhub.tui import quota as _quota
from mixhub.tui.badges import AGENT_DISPLAY_NAMES, agent_badge_and_pair
from mixhub.tui.core.style import P_CODE, P_WHITE
from mixhub.tui.widgets.preview_bar import RELAY_BOTTOM_OPEN, preview_relay_layout
from mixhub.tui.widgets.quota_table import pick_relay_target

TAIL_STEPS = (131072, 524288, 4194304, None)  # 128KB -> 512KB -> 4MB -> Full


class ReaderMixin:
    """Builds and scrolls the transcript preview and resolves its Resume / Go to Tab / Relay actions."""

    def _current_tail_bytes(self) -> int | None:
        return TAIL_STEPS[min(self.tail_step_idx, len(TAIL_STEPS) - 1)]

    def build_transcript_lines(self, pane_w: int):
        """Build conversation lines with clean vertical-bar user prompts, turn index, and LRU cache."""
        s = self.current_session()
        if not s:
            self.user_turns = []
            return []

        box_w = max(24, pane_w - 2)
        body_w = box_w
        tail_limit = self._current_tail_bytes()
        key = (
            s["tool"],
            s["id"],
            round(s.get("updatedAt", 0), 1),
            s.get("agentState", "dormant"),
            box_w,
            self.expand_tools,
            tail_limit,
        )
        if self.loaded_key == key:
            return self.rendered_lines
        cached = self.render_cache.get(key)
        if cached is not None:
            self.loaded_key = key
            self.rendered_lines, self.user_turns, self.is_tail_truncated = cached
            return self.rendered_lines

        self.loaded_key = key
        lines = []
        user_turns = []
        full = self.hub.sessions.get(s["tool"] + ":" + s["id"]) or s
        entries, is_trunc = self.hub.transcript_window(full, tail_bytes=tail_limit)
        self.is_tail_truncated = is_trunc

        state = s.get("agentState") or ("working" if s.get("isLive") else "dormant")
        sym, _, _ = agent_badge_and_pair(s)
        # Plain words only: the preview is clicked, never typed into, so no key hints and no internal state ids
        if s.get("isLive"):
            sub_cnt = int(s.get("activeSubagents") or 0)
            work_lbl = f"{sym} working · {sub_cnt} subagent{'s' if sub_cnt > 1 else ''}" if sub_cnt > 0 else f"{sym} working"
            state_labels = {
                "working": work_lbl,
                "wait_input": f"{sym} waiting for you",
                "done_idle": f"{sym} idle",
            }
            live_tag = f"  ·  {state_labels.get(state, f'{sym} idle')}"
        else:
            live_tag = f"  ·  {sym} asleep"
        title_str = disp_trunc(s.get("title") or "", box_w)
        meta_str = disp_trunc(f"{s['tool']} · {s.get('shortCwd','')} · {s['id'][:8]}{live_tag}", box_w)
        lines.append(("heading", title_str))
        lines.append(("meta", meta_str))
        if is_trunc:
            kb_label = f"{tail_limit // 1024}KB" if tail_limit else "full"
            lines.append(("dim", disp_trunc(f"↑ Last {kb_label} shown — scroll up for earlier history", box_w)))
        lines.append(("blank", ""))

        if not entries:
            lines.append(("meta", "(No transcript yet — Resume opens the session)"))
            self.rendered_lines = lines
            self.user_turns = []
            return lines

        pending_tools = []

        def flush_tools():
            nonlocal pending_tools
            if not pending_tools:
                return
            names = []
            for name, _ in pending_tools:
                if name not in names:
                    names.append(name)
            summary = f"· Ran {len(pending_tools)} tool step(s) ({', '.join(names[:4])})"
            lines.append(("meta", disp_trunc(summary, body_w)))
            if self.expand_tools:
                for name, preview in pending_tools:
                    lines.append(("dim", disp_trunc(f"  ├─ {name}: {preview}", body_w)))
            lines.append(("blank", ""))
            pending_tools = []

        for e in entries:
            role = e.get("role", "")
            kind = e.get("kind", "message")
            txt = (e.get("text") or "").strip()

            if kind in ("tool_call", "tool_result"):
                if kind == "tool_call":
                    tname = e.get("name") or "tool"
                    pending_tools.append((tname, clean_single_line(txt)[:80]))
                continue

            if kind == "thinking":
                if self.expand_tools and txt:
                    lines.append(("dim", disp_trunc(f"· Thinking: {clean_single_line(txt)}", body_w)))
                continue

            if not txt:
                continue

            flush_tools()

            if role == "user":
                if lines and lines[-1][0] != "blank":
                    lines.append(("blank", ""))
                turn_start_idx = len(lines)
                user_turns.append((turn_start_idx, clean_single_line(txt)))
                raw_lines = txt.splitlines() or [txt]
                wrapped_user_lines = []
                for rline in raw_lines:
                    for wline in disp_wrap(rline, box_w - 4):
                        wrapped_user_lines.append(wline)
                max_user_preview = 6 if not self.expand_tools else len(wrapped_user_lines)
                for u_idx, wline in enumerate(wrapped_user_lines[:max_user_preview]):
                    lines.append(("user_box" if u_idx == 0 else "user_cont", wline))
                if len(wrapped_user_lines) > max_user_preview:
                    hidden_n = len(wrapped_user_lines) - max_user_preview
                    lines.append(("dim", f"  … (+{hidden_n} more lines)"))
                lines.append(("blank", ""))
            else:
                raw_lines = txt.splitlines() or [txt]
                first_asst_line = True
                in_code_fence = False
                for rline in raw_lines:
                    stripped = rline.strip()
                    if stripped.startswith("```"):
                        in_code_fence = not in_code_fence
                        continue
                    if not stripped:
                        lines.append(("blank", ""))
                        continue
                    is_heading = False
                    if not in_code_fence and re.match(r"^#{1,6}\s+", stripped):
                        stripped = re.sub(r"^#{1,6}\s+", "", stripped)
                        is_heading = True
                    wrapped = disp_wrap(stripped if is_heading else rline, body_w - 4)  # + the 2-col `● ` indent
                    for wline in wrapped:
                        bullet = "● " if first_asst_line else "  "
                        first_asst_line = False
                        if in_code_fence:
                            lines.append(("code_line", bullet + wline))
                        elif is_heading:
                            lines.append(("heading", bullet + wline))
                        else:
                            lines.append(("asst", bullet + wline))
                lines.append(("blank", ""))

        flush_tools()
        self.rendered_lines = lines
        self.user_turns = user_turns
        if len(self.render_cache) >= 48:
            for old_k in list(self.render_cache.keys())[:16]:
                self.render_cache.pop(old_k, None)
        self.render_cache[key] = (lines, user_turns, is_trunc)
        return lines

    def _active_turn_index(self) -> int:
        if not self.user_turns:
            return -1
        active = 0
        probe = self.msg_scroll + 2
        for idx, (line_no, _) in enumerate(self.user_turns):
            if line_no <= probe:
                active = idx
            else:
                break
        return active

    def _expand_tail_preserving_viewport(self) -> bool:
        if not self.is_tail_truncated or self.tail_step_idx + 1 >= len(TAIL_STEPS):
            return False
        old_len = len(self.rendered_lines)
        self.tail_step_idx += 1
        self.loaded_key = None
        _, w = self.stdscr.getmaxyx()
        sidebar_only = self.sidebar_only or (w < 64 and not self.reader_only)
        left_w = self._effective_left_w(w, sidebar_only)
        right_x = 1 if (self.sidebar_collapsed or self.reader_only) else (left_w + 2)
        right_w = max(10, w - right_x)
        new_lines = self.build_transcript_lines(right_w)
        added = max(0, len(new_lines) - old_len)
        self.msg_scroll = max(0, self.msg_scroll + added)
        limit = self._current_tail_bytes()
        label = f"{limit // 1024}KB" if limit else "100% full"
        self.status_msg = f"Expanded history window ({label})"
        self._dirty = True
        return True

    def _draw_asst_line(self, y: int, x: int, text: str, max_cols: int):
        col = 0
        tokens = re.split(r"(`[^`]+`|\*\*[^*]+\*\*)", text)
        for tok in tokens:
            if not tok or col >= max_cols:
                continue
            attr = curses.color_pair(P_WHITE)
            content = tok
            if tok.startswith("`") and tok.endswith("`") and len(tok) >= 2:
                content = tok[1:-1]
                attr = curses.color_pair(P_CODE)
            elif tok.startswith("**") and tok.endswith("**") and len(tok) >= 4:
                content = tok[2:-2]
                attr = curses.color_pair(P_WHITE) | curses.A_BOLD
            # `disp_trunc` trims edge whitespace, which is the space between this piece and the code span next to it
            lead = content[:len(content) - len(content.lstrip())]  # a wrapped line's 2-col indent included
            trail = " " if content[-1:].isspace() and content.strip() else ""
            room = max_cols - col
            piece = lead + disp_trunc(content, max(0, room - len(lead) - len(trail)), "") + trail
            piece = disp_trunc(piece, room, "") if disp_width(piece) > room else piece
            pw = disp_width(piece)
            if pw <= 0:
                continue
            try:
                self.stdscr.addstr(y, x + col, piece, attr)
            except curses.error:
                pass
            col += pw

    def _compute_preview_bar_state(self, h: int, w: int, left_w: int, sidebar_only: bool) -> dict:
        self._relay_hits = {}
        s = self.current_session()
        header_only = bool(getattr(self, "header_only", False))
        if not s or (sidebar_only and not self.reader_only and not header_only):
            self._preview_action_zones = []
            return {}
        pmode, ext_state, can_move = _session_preview_mode_and_state(s)
        # The two-row header has no quota display or recommendation pills.
        quota_items = [] if header_only else _quota.get_cli_quota_metrics(home_dir=getattr(self.hub, "home", None))
        avail_tools = self._available_agent_tools() | {"claude", "codex", "agy"}
        rec_target, cur_rem_wk, target_rem_wk = pick_relay_target(s.get("tool", ""), quota_items, avail_tools)
        has_quota_rec = cur_rem_wk is not None and int(cur_rem_wk) < 15
        rec_key = "relay_toggle"
        rec_name = AGENT_DISPLAY_NAMES.get(rec_target, rec_target.capitalize()) if (has_quota_rec and rec_target) else ""
        eff_relay = self._eff_relay_state()
        hx, hy = self.hover_pos

        zones: list[tuple[int, int, int, str, bool]] = []
        hdr_info: dict = {}
        if (self.reader_only or header_only) and h >= 2:
            in_tmux = bool(os.environ.get("TMUX"))
            show_collapse_icon = (not in_tmux) or self._is_reader_zoomed(w)
            title_x = 5 if show_collapse_icon else 3
            self._header_mode_btn_x = (-10, -10)
            self._header_home_btn_x = (-10, -10)
            self._header_relay_x = (-10, -10)
            hdr_info = {"title_x": title_x, "relay_items": [], "relay_hits": []}

        bar_info: dict = {}
        if not sidebar_only and not header_only and h >= 6 and not self.active_ephemeral_toast():
            # Minimalist 1-line transparent action bar on `Row h - 1` (thin `─` divider on `Row h - 2`)
            bar_y = h - 1
            bar_x = 0 if (self.sidebar_collapsed or self.reader_only) else (left_w + 1)
            specs = self._sync_preview_focus(s, pmode, can_move)
            cx = bar_x + 2
            spans: list[tuple[int, str, str, bool, int, int]] = []
            for idx, (base_lbl, act, en) in enumerate(specs):
                pw = disp_width(base_lbl) + 4  # fixed width: `" <lbl> ↵ "` == `" <lbl>   "` == `" <lbl> ▸ "`
                x0, x1 = cx, cx + pw
                spans.append((idx, base_lbl, act, en, x0, x1))
                cx = x1 + 2

            # Hovering an enabled pill on Row h - 1 moves focus (`self._preview_focus_idx`) to that pill
            if hy == bar_y and eff_relay == "CLOSED":
                for idx, _, _, en, x0, x1 in spans:
                    if x0 <= hx < x1 and en:
                        self._preview_focus_idx = idx
                        break

            focus_idx = self._preview_focus_idx
            pills: list[tuple[str, int, int, str, bool, bool]] = []
            for idx, base_lbl, act, en, x0, x1 in spans:
                if act == "relay_toggle" and eff_relay != "CLOSED":
                    p_str = RELAY_BOTTOM_OPEN
                    is_foc = False
                elif idx == focus_idx and en and eff_relay == "CLOSED":
                    p_str = f" {base_lbl} ↵ "
                    is_foc = True
                else:
                    p_str = f" {base_lbl}   "
                    is_foc = False
                pills.append((p_str, x0, x1, act, en, is_foc))
                zones.append((bar_y, x0, x1, act, en))

            left_chips_end = pills[-1][2] if pills else (bar_x + 2)
            bar_info["pills"] = pills
            if len(pills) >= 1:
                p0 = pills[0]
                bar_info["chip1"] = (p0[0], p0[1], p0[2], p0[3], p0[4])
            if len(pills) >= 2:
                p1 = pills[1]
                bar_info["chip2"] = (p1[0], p1[1], p1[2], p1[3], p1[4])
            if len(pills) >= 3:
                p2 = pills[2]
                bar_info["chip3"] = (p2[0], p2[1], p2[2], p2[3], p2[4])
            bar_info["bar_x"] = bar_x
            bar_info["bar_y"] = bar_y
            bar_info["left_chips_end"] = left_chips_end
            bar_info["quota_items"] = quota_items

            relay_pill = next((p for p in pills if p[3] == "relay_toggle"), None)
            if relay_pill is not None:
                rp_str, rp_x0, rp_x1, _, _, _ = relay_pill
                c_items, c_hits = (
                    preview_relay_layout(rp_x1 + 2, w - 1, eff_relay, ds_ok=self._deepseek_relay_ok())
                    if eff_relay != "CLOSED"
                    else ([], [])
                )
                r_items = [(rp_x0, rp_str, "relay_toggle")] + c_items
                r_hits = [(rp_x0, rp_x1, "relay_toggle")] + c_hits
                self._relay_hits[bar_y] = r_hits
                bar_info["relay_items"] = r_items
                bar_info["relay_hits"] = r_hits

        self._preview_action_zones = zones
        hover_key = None
        for zy, zx0, zx1, zkey, _ in zones:
            if hy == zy and zx0 <= hx < zx1:
                hover_key = zkey
                break
        relay_hover = None
        for x0, x1, act in self._relay_hits.get(hy, ()):
            if x0 <= hx < x1:
                relay_hover = act
                break
        if hover_key is None and relay_hover == "relay_toggle":
            hover_key = rec_key

        return {
            "pmode": pmode,
            "ext_state": ext_state,
            "can_move": can_move,
            "has_quota_rec": has_quota_rec,
            "rec_target": rec_target,
            "rec_name": rec_name,
            "rec_key": rec_key,
            "cur_rem_wk": cur_rem_wk,
            "target_rem_wk": target_rem_wk,
            "hover_key": hover_key,
            "relay_hover": relay_hover,
            "eff_relay": eff_relay,
            "hdr": hdr_info,
            "bar": bar_info,
        }

    def _execute_preview_action(self, action_key: str, enabled: bool = True) -> bool:
        s = self.current_session()
        if not s:
            return False
        if action_key == "home":
            self.selected_idx = -1
            self.loaded_key = None
            self._write_active_selection(None, activate_reader=True)
            self._dirty = True
            return True
        if action_key == "back":
            env = dict(os.environ)
            env.pop("TMUX", None)
            pool_sess = current_pool_session_name()
            subprocess.Popen(
                ["tmux", "-L", "hubpool", "select-window", "-t", f"{pool_sess}:reader"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
            )
            self._dirty = True
            return True
        if action_key == "resume":
            self._mark_session_seen(s)
            self._write_active_selection(s)
            _pool.activate_session_in_hubpool(s, current_outer_session_name(), force_takeover=True, app=self)
            self._dirty = True
            return True
        if action_key == "goto_tab":
            self.focus_external_session(s)
            return True
        if action_key == "move_here":
            if not enabled:
                return True
            self.takeover_external_session_in_hubpool(s)
            return True
        if action_key == "relay_menu":
            self._handle_relay_action("relay_toggle")
            return True
        if action_key in ("relay_toggle", "relay_pick_claude", "relay_back"):
            return self._handle_relay_action(action_key)
        if action_key.startswith("relay:"):
            return self._handle_relay_action(action_key)
        return False

    def _prompt_rail_ticks(self, top: int, bottom: int, total_lines: int, active_turn: int) -> dict[int, int]:
        """
        Screen row -> user turn index for the right-edge prompt rail over rows `top..bottom - 1`. It spans the whole
        message area, so ticks do not move when the 2-row sticky prompt header appears or goes away.
        """
        span = max(1, bottom - top)
        total = max(1, total_lines - 1)
        ticks: dict[int, int] = {}
        for t_idx, (line_no, _) in enumerate(self.user_turns):
            row = top + min(span - 1, max(0, int(line_no / total * (span - 1))))
            if t_idx == active_turn or row not in ticks:
                ticks[row] = t_idx
        return ticks

    def _jump_to_rail_row(self, row: int, top: int, bottom: int) -> None:
        """Scroll to the prompt of the drawn rail tick nearest `row` (the same ticks `draw` put on screen)."""
        ticks = self._prompt_rail_ticks(top, bottom, len(self.rendered_lines), self._active_turn_index())
        if not ticks:
            return
        nearest = min(ticks, key=lambda r: (abs(r - row), r))
        self.msg_scroll = max(0, self.user_turns[ticks[nearest]][0])
        self.status_msg = ""
        self._dirty = True
