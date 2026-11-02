#!/usr/bin/env python3
"""
Unit and golden tests for the TUI core primitives:
  - `Rect` geometry (`(y, x, h, w)` vs `.contains(x, y)`, `intersect`, `clamp`)
  - `Style` & `Theme` (`age.*` 5-state roles guaranteed `bold=False, bg=-1`)
  - `Canvas` & `Cell` (CJK wide-character left/right half-cell overwrite clearing, clip boundaries, `row_text`, `flush`)
  - `Hit` & `HitMap` (z-order priority, same-z tie-breaking, dict payloads)
  - `InputDecoder` (SGR mouse 1-based -> 0-based, `\x1b[O` `FocusOut`, `\x1b[I` `FocusIn`, arrow keys, dragged path burst)
  - `PopoverItem`, `PopoverSpec`, `PopoverManager` (unified render, HitMap z=11, hotkeys, Esc, FocusOut, outside click)
  - `TmuxGateway` & `FakeTmux` (`outer` vs `pool` `-L hubpool`, `clean_tmux_env`, deterministic in-memory fake)
  - `mixhub.core.textwidth` unification across `tui.py` and `agy_claude_filter.py`
"""
from __future__ import annotations

import os
import sys
import unittest
from collections import deque
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mixhub.core.textwidth import char_width, disp_pad, disp_trunc, disp_width, disp_wrap
from mixhub.tmux.gateway import FakeTmux, TmuxGateway, clean_tmux_env
from mixhub.tui.core.canvas import Canvas
from mixhub.tui.core.events import DraggedPath, FocusIn, FocusOut, Key, Mouse
from mixhub.tui.core.geometry import Rect
from mixhub.tui.core.hitmap import HitMap
from mixhub.tui.core.input import KEY_DOWN, KEY_UP, InputDecoder
from mixhub.tui.core.style import (
    AGE_ROLES,
    DEFAULT_THEME,
    P_AGENT_CLAUDE,
    P_DONE,
    P_META,
    P_STAT_FAIL,
    P_STAT_T3,
    P_STAT_T4,
    P_UNSEL,
    P_USER_BOX,
    P_WHITE,
    Style,
    Theme,
)
from mixhub.tui.core.widget import AppCtx, RenderCtx
from mixhub.tui.widgets.popover import (
    PopoverManager,
    new_session_menu_spec,
    provider_menu_spec,
    relay_menu_spec,
)


class _FakeStdscr:
    """Minimal headless stdscr double feeding queued getch() ints and recording addstr() calls."""

    def __init__(self, chars: list[int] | None = None, h: int = 40, w: int = 32):
        self._q: deque[int] = deque(chars or [])
        self._h = h
        self._w = w
        self.calls: list[tuple[int, int, str, int]] = []
        self.nodelay_flag = False

    def getch(self) -> int:
        return self._q.popleft() if self._q else -1

    def ungetch(self, ch: int) -> None:
        self._q.appendleft(ch)

    def nodelay(self, flag: bool) -> None:
        self.nodelay_flag = bool(flag)

    def timeout(self, ms: int) -> None:
        pass

    def getmaxyx(self) -> tuple[int, int]:
        return (self._h, self._w)

    def addstr(self, y: int, x: int, text: str, attr: int = 0) -> None:
        self.calls.append((y, x, text, attr))


class TuiCorePrimitivesTests(unittest.TestCase):
    def test_rect_geometry_contains_intersect_and_clamp(self) -> None:
        # Non-square rect: y=2..5 (h=3), x=10..18 (w=8)
        r = Rect(y=2, x=10, h=3, w=8)
        self.assertEqual(r.right, 18)
        self.assertEqual(r.bottom, 5)
        self.assertFalse(r.is_empty)

        # Inside half-open [10, 18) x [2, 5)
        self.assertTrue(r.contains(10, 2))
        self.assertTrue(r.contains(17, 4))
        # Boundary exclusions (right=18, bottom=5)
        self.assertFalse(r.contains(18, 2))
        self.assertFalse(r.contains(10, 5))
        # Transposed coordinates must not match
        self.assertFalse(r.contains(2, 10))

        # Intersection
        r2 = Rect(y=4, x=15, h=4, w=6)
        inter = r.intersect(r2)
        self.assertEqual(inter, Rect(y=4, x=15, h=1, w=3))

        disjoint = r.intersect(Rect(y=10, x=0, h=2, w=2))
        self.assertTrue(disjoint.is_empty)
        self.assertEqual((disjoint.h, disjoint.w), (0, 0))

        # Clamp inside bounds
        clamped = Rect(y=-2, x=25, h=5, w=10).clamp(Rect(y=0, x=0, h=20, w=30))
        self.assertEqual(clamped, Rect(y=0, x=20, h=5, w=10))

    def test_theme_enforces_five_state_age_roles_zero_bold_and_bg_minus_one(self) -> None:
        theme = Theme()
        self.assertEqual(
            AGE_ROLES,
            ("age.working", "age.wait", "age.unread", "age.failed", "age.seen"),
        )
        for role in AGE_ROLES:
            for state in ("rest", "selected", "hover", "active"):
                st = theme.style(role, state)
                self.assertFalse(st.bold, f"{role} ({state}) must have bold=False")
                self.assertEqual(st.bg, -1, f"{role} ({state}) must have bg=-1")

        self.assertEqual(theme.style("age.working").pair, P_DONE)
        self.assertIn(theme.style("age.wait").pair, (P_STAT_T3, P_STAT_T4))
        self.assertIn(theme.style("age.unread").pair, (35, P_STAT_T3))
        self.assertIn(theme.style("age.failed").pair, (P_STAT_T4, P_STAT_FAIL))
        self.assertEqual(theme.style("age.seen", "rest").pair, 4)
        self.assertEqual(theme.style("age.seen", "selected").pair, P_META)

        # Menu item hover uses P_USER_BOX + bold
        hov = theme.style("menu.item", "hover")
        self.assertEqual(hov.pair, P_USER_BOX)
        self.assertTrue(hov.bold)

    def test_canvas_cjk_wide_char_half_overwrite_clears_other_half(self) -> None:
        cv = Canvas(4, 12)
        base_st = Style(P_UNSEL)
        over_st = Style(P_WHITE, bold=True)

        # "你好世界" occupies cols 0..7: 你(0,1) 好(2,3) 世(4,5) 界(6,7)
        written = cv.put(0, 0, "你好世界", base_st)
        self.assertEqual(written, 8)
        self.assertEqual(cv.row_text(0), "你好世界    ")
        self.assertEqual(cv.cell(0, 0).width, 2)
        self.assertEqual(cv.cell(0, 1).width, 0)

        # Case 1: Overwrite LEFT half of '好' (col 2) with 1-col ASCII 'X' -> col 3 must become space ' '!
        cv.put(0, 2, "X", over_st)
        self.assertEqual(cv.cell(0, 2).ch, "X")
        self.assertEqual(cv.cell(0, 3).ch, " ")
        self.assertEqual(cv.cell(0, 3).width, 1)
        self.assertEqual(cv.row_text(0), "你X 世界    ")

        # Case 2: Overwrite RIGHT half (continuation cell, col 5) of '世' (cols 4,5) with 'Y' -> col 4 becomes ' '!
        cv.put(0, 5, "Y", over_st)
        self.assertEqual(cv.cell(0, 4).ch, " ")
        self.assertEqual(cv.cell(0, 4).width, 1)
        self.assertEqual(cv.cell(0, 5).ch, "Y")
        self.assertEqual(cv.row_text(0), "你X  Y界    ")

        # Case 3: Overwrite with another 2-col wide char '中' straddling col 5 and col 6 (left half of '界' at 6,7)
        # -> col 7 (right half of '界') must also be cleared to space!
        cv.put(0, 5, "中", over_st)
        self.assertEqual(cv.cell(0, 5).ch, "中")
        self.assertEqual(cv.cell(0, 5).width, 2)
        self.assertEqual(cv.cell(0, 6).width, 0)
        self.assertEqual(cv.cell(0, 7).ch, " ")
        self.assertEqual(cv.cell(0, 7).width, 1)
        self.assertEqual(cv.row_text(0), "你X  中     ")

        # Case 4: Wide char at right edge of canvas (col 11 of width 12) cannot split -> pads with space
        cv.put(1, 11, "中", base_st)
        self.assertEqual(cv.cell(1, 11).ch, " ")
        self.assertEqual(cv.cell(1, 11).width, 1)

        # Case 5: fill() cutting across wide chars at both left and right borders repairs both halves
        cv.put(2, 0, "你好世界", base_st)
        cv.fill(Rect(y=2, x=1, h=1, w=6), ".", over_st)
        # col 1 was right half of '你' -> col 0 becomes ' '
        # col 6 was left half of '界' -> col 7 becomes ' '
        self.assertEqual(cv.row_text(2), " ......     ")

        # Flush coalesces runs and skips width=0 continuation cells
        fake_scr = _FakeStdscr(h=4, w=12)
        cv.flush(fake_scr)
        self.assertTrue(any("你" in call[2] for call in fake_scr.calls if call[0] == 0))

    def test_hitmap_z_order_and_tie_breaking_with_dict_payload(self) -> None:
        hm = HitMap()
        hm.add(Rect(0, 0, 20, 32), "row:select", payload={"idx": 1}, z=1)
        hm.add(Rect(2, 4, 6, 22), "pop:border", payload={"kind": "provider"}, z=10)
        hm.add(Rect(3, 4, 1, 22), "pop:claude", payload="claude", z=11)
        hm.add(Rect(3, 4, 1, 22), "pop:claude_override", payload="claude2", z=11)

        # Highest z (11) and latest registered at same z wins
        hit_item = hm.at(10, 3)
        self.assertIsNotNone(hit_item)
        assert hit_item is not None
        self.assertEqual(hit_item.action, "pop:claude_override")
        self.assertEqual(hit_item.payload, "claude2")

        # Border row (y=2) hits z=10 over z=1
        hit_border = hm.at(10, 2)
        self.assertIsNotNone(hit_border)
        assert hit_border is not None
        self.assertEqual(hit_border.action, "pop:border")

        # Outside popover (y=15, x=5) hits z=1 row
        hit_row = hm.at(5, 15)
        self.assertIsNotNone(hit_row)
        assert hit_row is not None
        self.assertEqual(hit_row.action, "row:select")
        self.assertEqual(hit_row.payload, {"idx": 1})

        # Miss returns None
        self.assertIsNone(hm.at(50, 50))

    def test_input_decoder_sgr_mouse_focus_out_arrows_and_dragged_path(self) -> None:
        # 1. \x1b[O -> FocusOut
        scr_fo = _FakeStdscr([27, ord("["), ord("O")])
        dec = InputDecoder(ungetch_fn=scr_fo.ungetch)
        self.assertEqual(dec.feed(scr_fo), [FocusOut()])

        # 2. \x1b[I -> FocusIn
        scr_fi = _FakeStdscr([27, ord("["), ord("I")])
        self.assertEqual(dec.feed(scr_fi), [FocusIn()])

        # 3. SGR Mouse click \x1b[<0;15;8M -> 0-based (x=14, y=7)
        sgr_seq = [27] + [ord(c) for c in "[<0;15;8M"]
        scr_m = _FakeStdscr(sgr_seq)
        evs = dec.feed_char(scr_m, scr_m.getch(), now_mono=100.0)
        self.assertEqual(evs, [Mouse(kind="click", x=14, y=7, button=0)])

        # Immediate second click at same position within 0.40s -> double click
        scr_m2 = _FakeStdscr(sgr_seq)
        evs2 = dec.feed_char(scr_m2, scr_m2.getch(), now_mono=100.15)
        self.assertEqual(evs2, [Mouse(kind="double", x=14, y=7, button=0)])

        # 4. CSI / SS3 arrow keys
        scr_up = _FakeStdscr([27, ord("["), ord("A")])
        self.assertEqual(dec.feed(scr_up), [Key(KEY_UP)])
        scr_ss3 = _FakeStdscr([27, ord("O"), ord("B")])
        self.assertEqual(dec.feed(scr_ss3), [Key(KEY_DOWN)])

        # 5. Bare Esc
        scr_esc = _FakeStdscr([27])
        self.assertEqual(dec.feed(scr_esc), [Key(27)])

        # 6. Dragged file path burst
        path_str = "/Users/alice/screenshot.png"
        scr_drag = _FakeStdscr([ord(c) for c in path_str])
        self.assertEqual(dec.feed(scr_drag), [DraggedPath(path_str)])

    def test_popover_manager_unifies_four_popovers_draw_hit_hotkey_esc_and_focus_out(self) -> None:
        focus_requests: list[int] = []
        pm = PopoverManager(on_focus_request=lambda: focus_requests.append(1))
        picked: list[tuple[object, object]] = []

        # 1. Open provider filter popover
        spec = provider_menu_spec(active_key="claude")
        pm.open(spec, on_pick=lambda k, p: picked.append((k, p)), payload="prov_ctx")
        self.assertTrue(pm.is_open("provider"))
        self.assertEqual(len(focus_requests), 1)

        # Render onto a Canvas pre-populated with CJK rows to verify blanking + border + HitMap
        cv = Canvas(20, 32)
        for ry in range(10):
            cv.put(ry, 0, "你好世界测试长标题一二三四五六", Style(P_UNSEL))
        hm = HitMap()
        rctx = RenderCtx(h=20, w=32, left_w=32, hx=8, hy=3, theme=DEFAULT_THEME)
        pm.render(cv, hm, rctx)

        # Row 1 is top border `╭─ Filter ───────────╮` at x=4..25, and CJK outside card is blanked
        self.assertIn("╭─ Filter ", cv.row_text(1))
        self.assertIn("Claude Code", cv.row_text(3))
        self.assertIn("✓", cv.row_text(3))

        # HitMap has item hits at z=11 and backdrop at z=9
        hit = hm.at(10, 3)
        self.assertIsNotNone(hit)
        assert hit is not None
        self.assertEqual(hit.action, "pop:claude")

        # Click item -> closes BEFORE callback and picks "claude"
        consumed = pm.handle(Mouse(kind="click", x=10, y=3), AppCtx())
        self.assertTrue(consumed)
        self.assertFalse(pm.is_open())
        self.assertEqual(picked, [("claude", "prov_ctx")])

        # 2. New session popover + hotkey ('x' -> codex)
        pm.open(new_session_menu_spec(), on_pick=lambda k, p: picked.append((k, p)), payload="new")
        self.assertTrue(pm.is_open("new_session"))
        self.assertTrue(pm.handle(Key(ord("x")), AppCtx()))
        self.assertFalse(pm.is_open())
        self.assertEqual(picked[-1], ("codex", "new"))

        # 3. Relay popover + FocusOut closes automatically
        pm.open(relay_menu_spec(), on_pick=lambda k, p: picked.append((k, p)))
        self.assertTrue(pm.is_open("relay"))
        self.assertTrue(pm.handle(FocusOut(), AppCtx()))
        self.assertFalse(pm.is_open())

    def test_tmux_gateway_and_fake_tmux(self) -> None:
        with mock.patch.dict(os.environ, {"TMUX": "/tmp/tmux-501/default,123,0"}):
            cleaned = clean_tmux_env()
            self.assertNotIn("TMUX", cleaned)
            self.assertIn("TMUX", os.environ)

        gw = TmuxGateway(tmux_bin="/opt/homebrew/bin/tmux", pool_socket="hubpool")
        self.assertEqual(gw.build_argv(["list-panes"], server="outer"), ["tmux", "-L", "mixhub", "list-panes"])
        self.assertEqual(
            gw.build_argv(["list-windows"], server="pool"),
            ["tmux", "-L", "hubpool", "list-windows"],
        )

        fake = FakeTmux()
        self.assertTrue(fake.is_io_allowed())
        self.assertTrue(fake.has_session("hub", server="outer"))
        self.assertFalse(fake.has_session("nonexistent", server="outer"))
        fake.windows["pool"] = ["reader", "w-claude-1234567890ab"]
        self.assertEqual(fake.list_windows("pool", server="pool"), ["reader", "w-claude-1234567890ab"])
        fake.select_pane("%1", server="outer", async_run=True)
        self.assertEqual(fake.selected_panes, ["%1"])
        fake.send_keys("pool:w-claude-1234567890ab", "hello", server="pool", literal=True)
        self.assertEqual(fake.sent_keys, [("pool:w-claude-1234567890ab", "hello", True)])

    def test_disp_wrap_breaks_english_between_words(self) -> None:
        # The preview cut words mid-way ("instead of f / ailing"); CJK has no spaces and still wraps per character
        text = "so a short overlap waits instead of failing, and add a nightly job"
        lines = disp_wrap(text, 24)
        self.assertEqual(lines, ["so a short overlap waits", "instead of failing, and", "add a nightly job"])
        self.assertEqual(disp_wrap("x" * 30, 12), ["x" * 12, "x" * 12, "x" * 6])  # a word wider than the line
        cjk = disp_wrap("数据库迁移需求说明与回滚方案", 10)
        self.assertEqual("".join(cjk), "数据库迁移需求说明与回滚方案")
        self.assertTrue(all(disp_width(line) <= 10 for line in cjk))
        self.assertEqual(disp_wrap("  ● indented reply that wraps here", 20)[0], "  ● indented reply")

    def test_textwidth_deduplicated_across_tui_and_agy_claude_filter(self) -> None:
        import agy_claude_filter
        import tui

        self.assertIs(tui.char_width, char_width)
        self.assertIs(tui.disp_width, disp_width)
        self.assertIs(tui.disp_trunc, disp_trunc)
        self.assertIs(tui.disp_pad, disp_pad)
        self.assertIs(tui.disp_wrap, disp_wrap)
        self.assertIs(agy_claude_filter.char_width, char_width)
        self.assertEqual(agy_claude_filter._char_disp_width("你"), 2)
        self.assertEqual(agy_claude_filter._char_disp_width("a"), 1)
        self.assertEqual(agy_claude_filter._char_disp_width("◈"), 1)


if __name__ == "__main__":
    unittest.main()
