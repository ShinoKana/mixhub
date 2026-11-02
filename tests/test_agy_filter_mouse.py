"""
Click-to-move-cursor regression tests for `agy_claude_filter.py` in agy's no-flicker (altscreen) mode.

The fixtures are real `agy-bin` 1.4.7 output captured through a PTY that answered queries like
Terminal.app (DA1 `\x1b[?1;2c`, no XTVERSION / kitty replies); the account e-mail is replaced with a
same-length placeholder. Chunk boundaries are kept exactly as `os.read` returned them.
"""
import struct
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import agy_claude_filter as f  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
LEFT, RIGHT = b"\x1b[D", b"\x1b[C"


def load_chunks(name: str) -> list[bytes]:
    data = (FIXTURES / name).read_bytes()
    chunks, i = [], 0
    while i < len(data):
        _, n = struct.unpack(">dI", data[i : i + 12])
        i += 12
        chunks.append(data[i : i + n])
        i += n
    return chunks


def replay(name: str, rows: int, cols: int) -> "f.PromptCursorTracker":
    tracker = f.PromptCursorTracker()
    tracker.term_rows, tracker.term_cols = rows, cols
    tracker._pending_size = (rows, cols)
    for chunk in load_chunks(name):
        tracker.update_from_output(chunk)
    return tracker


def click(col: int, row: int) -> bytes:
    return b"\x1b[<0;%d;%dM\x1b[<0;%d;%dm" % (col, row, col, row)


class AltScreenSingleLineTest(unittest.TestCase):
    """Screen: row 13 border, row 14 `> hello world 你好 abc` (caret at col 23), row 15 border."""

    def setUp(self):
        self.t = replay("agy_1_4_7_altscreen_typed_120x40.bin", 40, 120)

    def test_click_on_word_moves_cursor_in_altscreen(self):
        # Root cause of the Terminal.app report: agy runs in the alternate screen, and the filter
        # used to forward every mouse byte untouched there, so agy (which ignores clicks) did nothing.
        self.assertEqual(f.filter_stdin_mouse(click(9, 14), self.t), LEFT * 12)  # 'w' (index 6) from index 18
        self.assertTrue(self.t._alt_screen)
        self.assertEqual(self.t.vs.shown_caret, (13, 22))

    def test_click_either_half_of_cjk_and_past_end(self):
        self.assertEqual(f.filter_stdin_mouse(click(16, 14), self.t), LEFT * 6)  # right half of 你 -> index 12
        self.assertEqual(f.filter_stdin_mouse(click(3, 14), self.t), LEFT * 18)  # first char
        self.assertEqual(f.filter_stdin_mouse(click(1, 14), self.t), LEFT * 18)  # on the `>` marker -> start
        self.assertEqual(f.filter_stdin_mouse(click(80, 14), self.t), b"")  # right of the text = caret already at end

    def test_wheel_and_clicks_outside_prompt_reach_agy(self):
        # agy enabled ?1002h/?1006h itself (scrolling, pinned prompt header, copy-on-select)
        self.assertEqual(self.t.vs.mouse_tracking, {1002})
        for raw in (b"\x1b[<64;30;5M", b"\x1b[<65;30;5M", click(10, 5), click(10, 13), click(10, 15)):
            self.assertEqual(f.filter_stdin_mouse(raw, self.t), raw)

    def test_drag_from_prompt_is_handed_back_to_agy(self):
        press, motion, release = b"\x1b[<0;9;14M", b"\x1b[<32;14;14M", b"\x1b[<0;14;14m"
        out = f.filter_stdin_mouse(press, self.t)
        out += f.filter_stdin_mouse(motion, self.t)
        out += f.filter_stdin_mouse(release, self.t)
        self.assertEqual(out, press + motion + release)

    def test_one_cell_jitter_is_still_a_click_and_split_reads_are_joined(self):
        out = f.filter_stdin_mouse(b"\x1b[<0;9;1", self.t)
        out += f.filter_stdin_mouse(b"4M\x1b[<32;10;14M\x1b[<0;10;14m", self.t)
        self.assertEqual(out, LEFT * 12)

    def test_modified_click_is_not_intercepted(self):
        raw = b"\x1b[<4;9;14M\x1b[<4;9;14m"  # shift+click
        self.assertEqual(f.filter_stdin_mouse(raw, self.t), raw)

    def test_x10_encoding_is_handled(self):
        raw = b"\x1b[M" + bytes([32, 32 + 9, 32 + 14]) + b"\x1b[M" + bytes([35, 32 + 9, 32 + 14])
        self.assertEqual(f.filter_stdin_mouse(raw, self.t), LEFT * 12)

    def test_keyboard_and_paste_pass_through(self):
        self.assertEqual(f.filter_stdin_mouse(b"x\x1b[D", self.t), b"x\x1b[D")
        paste = b"\x1b[200~a\x1b[<0;9;14Mb\x1b[201~"
        self.assertEqual(f.filter_stdin_mouse(paste, self.t), paste)

    def test_dsr_shaped_keys_are_not_swallowed(self):
        # Shift+F3 is `\x1b[1;2R`, the same shape as a cursor-position reply
        self.assertEqual(f.filter_stdin_mouse(b"\x1b[1;2R", self.t), b"\x1b[1;2R")

    def test_verification_corrects_a_misplaced_caret(self):
        self.assertEqual(f.filter_stdin_mouse(click(9, 14), self.t), LEFT * 12)
        # agy lands two cells off (e.g. a width mismatch): re-render puts the caret at col 11
        self.t.update_from_output(b"\x1b[?25l\x1b[14;11H\x1b[?25h")
        self.assertEqual(self.t.tick(time.monotonic() + 0.3), LEFT * 2)
        self.t.update_from_output(b"\x1b[?25l\x1b[14;9H\x1b[?25h")
        self.assertEqual(self.t.tick(time.monotonic() + 0.3), b"")
        self.assertIsNone(self.t._verify)

    def test_verification_never_resends_when_agy_has_not_moved(self):
        self.assertEqual(f.filter_stdin_mouse(click(9, 14), self.t), LEFT * 12)
        self.t.update_from_output(b"\x1b[?25l\x1b[14;23H\x1b[?25h")  # unrelated frame, caret unchanged
        self.assertEqual(self.t.tick(time.monotonic() + 0.5), b"")
        self.assertIsNone(self.t._verify)

    def test_typing_cancels_verification(self):
        f.filter_stdin_mouse(click(9, 14), self.t)
        f.filter_stdin_mouse(b"z", self.t)
        self.assertIsNone(self.t._verify)

    def test_drag_select_then_backspace_or_delete_wipes_entire_selection(self):
        # Drag across `world` on row 14: col 9 ('w', idx 6) -> col 14 (' ', idx 11); caret is at idx 18
        press, motion, release = b"\x1b[<0;9;14M", b"\x1b[<32;14;14M", b"\x1b[<0;14;14m"
        out = f.filter_stdin_mouse(press + motion + release, self.t)
        self.assertEqual(out, press + motion + release)
        self.assertEqual(self.t.prompt_sel, (6, 11))
        # Next key is Backspace: moves caret from idx 18 to end_idx 11 (LEFT * 7), then emits 5 Backspaces
        bs_out = f.filter_stdin_mouse(b"\x7f", self.t)
        self.assertEqual(bs_out, (LEFT * 7) + (b"\x7f" * 5))
        self.assertIsNone(self.t.prompt_sel)
        # Subsequent Backspace only deletes 1 character
        self.assertEqual(f.filter_stdin_mouse(b"\x7f", self.t), b"\x7f")

        # Reverse drag (right-to-left: col 14 -> col 9) + Delete (`\x1b[3~`) works identically
        f.filter_stdin_mouse(b"\x1b[<0;14;14M\x1b[<32;9;14M\x1b[<0;9;14m", self.t)
        self.assertEqual(self.t.prompt_sel, (6, 11))
        del_out = f.filter_stdin_mouse(b"\x1b[3~", self.t)
        self.assertEqual(del_out, (LEFT * 7) + (b"\x7f" * 5))
        self.assertIsNone(self.t.prompt_sel)

    def test_drag_select_then_typing_or_paste_replaces_entire_selection(self):
        # Select `world` (idx 6..11) and type replacement text `there`
        f.filter_stdin_mouse(b"\x1b[<0;9;14M\x1b[<32;14;14M\x1b[<0;14;14m", self.t)
        self.assertEqual(self.t.prompt_sel, (6, 11))
        rep_out = f.filter_stdin_mouse(b"there", self.t)
        self.assertEqual(rep_out, (LEFT * 7) + (b"\x7f" * 5) + b"there")
        self.assertIsNone(self.t.prompt_sel)

        # Select `world` (idx 6..11) and bracketed-paste replacement
        f.filter_stdin_mouse(b"\x1b[<0;9;14M\x1b[<32;14;14M\x1b[<0;14;14m", self.t)
        paste = b"\x1b[200~ pasted \x1b[201~"
        paste_out = f.filter_stdin_mouse(paste, self.t)
        self.assertEqual(paste_out, (LEFT * 7) + (b"\x7f" * 5) + paste)
        self.assertIsNone(self.t.prompt_sel)

    def test_drag_select_canceled_by_arrows_or_click(self):
        f.filter_stdin_mouse(b"\x1b[<0;9;14M\x1b[<32;14;14M\x1b[<0;14;14m", self.t)
        self.assertEqual(self.t.prompt_sel, (6, 11))
        # Pressing Left arrow clears selection without deleting
        self.assertEqual(f.filter_stdin_mouse(LEFT, self.t), LEFT)
        self.assertIsNone(self.t.prompt_sel)
        self.assertEqual(f.filter_stdin_mouse(b"\x7f", self.t), b"\x7f")

        # Single click also clears selection
        f.filter_stdin_mouse(b"\x1b[<0;9;14M\x1b[<32;14;14M\x1b[<0;14;14m", self.t)
        self.assertEqual(self.t.prompt_sel, (6, 11))
        self.assertEqual(f.filter_stdin_mouse(click(9, 14), self.t), LEFT * 12)
        self.assertIsNone(self.t.prompt_sel)


class AltScreenWrappedPromptTest(unittest.TestCase):
    """
    40x30 screen. Row 22 `> alpha beta gamma delta epsilon zeta ` (soft wrap; the space after `zeta`
    stays on row 22), row 23 `  eta theta iota kappa`, hard newline, row 24 `  line two 你好`, caret at
    the end (24, 16). agy's Left key walk (probe) gave: (24,3) -11-> ... -12-> (23,23); (23,3) -1-> (22,38).
    """

    def setUp(self):
        self.t = replay("agy_1_4_7_altscreen_wrap_paste_40x30.bin", 30, 40)

    def test_layout_is_replayed_from_cell_diffs(self):
        rows = ["".join(self.t.vs.buf[y]).rstrip() for y in range(20, 25)]
        self.assertEqual(rows[1], "> alpha beta gamma delta epsilon zeta")
        self.assertEqual(rows[2], "  eta theta iota kappa")
        self.assertEqual(rows[3], "  line two 你好")
        self.assertEqual(self.t.vs.shown_caret, (23, 15))
        self.assertEqual(self.t._alt_prompt_region(), (21, 23))

    def test_cross_row_clicks(self):
        self.assertEqual(f.filter_stdin_mouse(click(9, 22), self.t), LEFT * 62)  # 'b' of beta, index 6 of 68
        self.t._verify = None
        self.t.vs.shown_caret = (23, 15)
        self.assertEqual(f.filter_stdin_mouse(click(30, 23), self.t), LEFT * 12)  # past `kappa` -> before the newline
        self.t._verify = None
        self.t.vs.shown_caret = (23, 15)
        self.assertEqual(f.filter_stdin_mouse(click(40, 22), self.t), LEFT * 33)  # past `zeta` -> on the wrapped space
        self.t._verify = None
        self.t.vs.shown_caret = (21, 4)  # caret on `p` of alpha
        self.assertEqual(f.filter_stdin_mouse(click(5, 23), self.t), RIGHT * 36)  # 'a' of eta: index 38 from 2


class StreamAndModeTest(unittest.TestCase):
    def test_alt_screen_switch_split_across_reads(self):
        t = f.PromptCursorTracker()
        t.update_from_output(b"\x1b[?10")
        self.assertFalse(t._alt_screen)
        t.update_from_output(b"49h\x1b[H\x1b[2J")
        self.assertTrue(t._alt_screen)
        t.update_from_output(b"\x1b[?1049l")
        self.assertFalse(t._alt_screen)

    def test_back_tab_used_by_agy_renderer(self):
        # agy 1.4.7 moves the caret with `CSI Z` (tab stops every 8 after `\x1b[?5W`)
        vs = f.VScreen(30, 40)
        vs.feed(b"\x1b[?1049h\x1b[H\x1b[2J\x1b[?5W\x1b[24;16H\x1b[Z\x1b[?25h")
        self.assertEqual(vs.shown_caret, (23, 8))

    def test_mouse_modes_are_only_forced_before_agy_uses_the_alt_screen(self):
        out, _ = f.transform_chunk(b"\x1b[?1002l\x1b[?1049l", b"", force_mouse=False)
        self.assertEqual(out, b"\x1b[?1002l\x1b[?1049l")
        out, _ = f.transform_chunk(b"\x1b[?1002l")
        self.assertIn(b"\x1b[?1000h", out)

    def test_exit_restore_turns_every_mouse_mode_off(self):
        t = f.PromptCursorTracker()
        t.update_from_output(b"\x1b[?1049h\x1b[?1002h\x1b[?1006h\x1b[?2004h\x1b[?25l")
        tail = f.exit_restore_bytes(t)
        for seq in (b"\x1b[?1049l", b"\x1b[?1000l", b"\x1b[?1002l", b"\x1b[?1003l", b"\x1b[?1006l", b"\x1b[?2004l", b"\x1b[?25h"):
            self.assertIn(seq, tail)
        self.assertLess(tail.index(b"\x1b[?1049l"), tail.index(b"\x1b[?1002l"))

    def test_legacy_inline_prompt_click_still_works(self):
        t = f.PromptCursorTracker()
        t.term_rows, t.term_cols = 30, 100
        t.update_from_output("\x1b[K> 你好世界测试光标\x1b[?7h\x1b[19G".encode())
        self.assertEqual(f.filter_stdin_mouse(click(8, 27), t), LEFT * 6)

    def test_inline_drag_select_delete_and_replace(self):
        t = f.PromptCursorTracker()
        t.term_rows, t.term_cols = 30, 100
        # `> 你好世界测试光标` (8 CJK chars, cursor at idx 8, col 19)
        t.update_from_output("\x1b[K> 你好世界测试光标\x1b[?7h\x1b[19G".encode())
        # Drag from col 7 ('世', idx 2) to col 15 ('光', idx 6) on row 27
        drag_out = f.filter_stdin_mouse(b"\x1b[<0;7;27M\x1b[<32;11;27M\x1b[<0;15;27m", t)
        # Press moves 8->2 (LEFT * 6), release moves 2->6 (RIGHT * 4) so cursor rests at end_idx=6
        self.assertEqual(drag_out, (LEFT * 6) + (RIGHT * 4))
        self.assertEqual(t.prompt_sel, (2, 6))
        self.assertEqual(t.cur_idx, 6)
        # Backspace deletes the 4 selected CJK chars ('世界测试')
        self.assertEqual(f.filter_stdin_mouse(b"\x7f", t), b"\x7f" * 4)
        self.assertIsNone(t.prompt_sel)
        self.assertEqual(t.text, "你好光标")
        self.assertEqual(t.cur_idx, 2)

        # Drag backwards from col 11 (end of '你好光标', idx 4) to col 7 ('光', idx 2) and type '真棒'
        drag2_out = f.filter_stdin_mouse(b"\x1b[<0;11;27M\x1b[<0;7;27m", t)
        self.assertEqual(drag2_out, RIGHT * 2)
        self.assertEqual(t.prompt_sel, (2, 4))
        self.assertEqual(t.cur_idx, 4)
        rep_bytes = "真棒".encode("utf-8")
        self.assertEqual(f.filter_stdin_mouse(rep_bytes, t), (b"\x7f" * 2) + rep_bytes)
        self.assertIsNone(t.prompt_sel)
        self.assertEqual(t.text, "你好真棒")
        self.assertEqual(t.cur_idx, 4)

    def test_sel_clear_suppressed_while_prompt_sel_active(self):
        sc = f._SelClear()
        sc.on_output(b"\x1b[?1049h\x1b[?1002h\x1b[?1006h")
        sent = []
        drag = b"\x1b[<0;9;14M\x1b[<32;14;14M\x1b[<0;14;14m"
        sc.on_stdin_bytes(drag, 40, sent.append, prompt_sel_active=True)
        self.assertIsNone(sc._due)
        sc.on_stdin_bytes(drag, 40, sent.append, prompt_sel_active=False)
        self.assertIsNotNone(sc._due)


if __name__ == "__main__":
    unittest.main()
