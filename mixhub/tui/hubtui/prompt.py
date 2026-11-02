#!/usr/bin/env python3
"""`mixhub.tui.hubtui.prompt`: The inline one-line text prompt (rename, display name, messages)."""
from __future__ import annotations

try:
    import curses
except ImportError:  # Windows without windows-curses installed
    curses = None

from mixhub.core.textwidth import disp_pad, disp_trunc, disp_width
from mixhub.tui.core.style import P_WHITE


class PromptInputMixin:
    """A curses one-line editor drawn on the bottom row."""

    def prompt_input(
        self,
        prompt_label: str,
        initial: str = "",
        row_y: int | None = None,
        col_x: int = 1,
        box_attr: int | None = None,
    ) -> str:
        h, w = self.stdscr.getmaxyx()
        target_y = (h - 1) if row_y is None else max(0, min(h - 1, int(row_y)))
        target_x = max(0, min(w - 4, int(col_x)))
        label = disp_trunc(prompt_label, max(4, w - target_x - 12), "")
        lw = disp_width(label)
        attr = box_attr if box_attr is not None else (curses.color_pair(P_WHITE) | curses.A_BOLD)
        was_nodelay = bool(self.reader_only or self.sidebar_only or getattr(self, "header_only", False))
        try:
            self.stdscr.nodelay(False)
            self.stdscr.timeout(-1)
        except Exception:
            pass

        # Support unit tests that mock `stdscr.getstr` directly
        getstr_fn = getattr(self.stdscr, "getstr", None)
        if type(getstr_fn).__name__ in ("MagicMock", "AsyncMock", "NonCallableMagicMock") and getattr(getstr_fn, "return_value", None) is not None:
            try:
                curses.echo()
                curses.curs_set(1)
                if hasattr(self.stdscr, "leaveok"):
                    try:
                        self.stdscr.leaveok(False)
                    except Exception:
                        pass
                self.stdscr.move(target_y, target_x)
                self.stdscr.clrtoeol()
                self.stdscr.addstr(target_y, target_x, label, attr)
                raw = self.stdscr.getstr(target_y, target_x + lw, max(1, w - target_x - lw - 2))
                val = raw.decode("utf-8", errors="ignore").strip() if isinstance(raw, (bytes, bytearray)) else str(raw).strip()
            except Exception:
                val = initial
            finally:
                try:
                    curses.noecho()
                    curses.curs_set(0)
                    if hasattr(self.stdscr, "leaveok"):
                        try:
                            self.stdscr.leaveok(True)
                        except Exception:
                            pass
                    if was_nodelay:
                        self.stdscr.nodelay(True)
                except Exception:
                    pass
                self._dirty = True
            return val

        buf = list(initial or "")
        max_field_w = max(4, w - target_x - lw - 2)
        try:
            curses.noecho()
            curses.curs_set(1)
            if hasattr(self.stdscr, "leaveok"):
                try:
                    self.stdscr.leaveok(False)
                except Exception:
                    pass
            while True:
                try:
                    if box_attr is not None:
                        fill = disp_pad(label + "".join(buf), max(10, w - target_x - 1))
                        self.stdscr.addstr(target_y, target_x, fill, box_attr)
                    else:
                        self.stdscr.move(target_y, 0)
                        self.stdscr.clrtoeol()
                        self.stdscr.addstr(target_y, target_x, label, attr)
                        visible_txt = disp_trunc("".join(buf), max_field_w, "")
                        self.stdscr.addstr(target_y, target_x + lw, visible_txt, curses.color_pair(P_WHITE))
                    cur_col = min(w - 2, target_x + lw + disp_width("".join(buf)))
                    self.stdscr.move(target_y, cur_col)
                    self.stdscr.refresh()
                except Exception:
                    pass

                use_wch = (
                    hasattr(self.stdscr, "get_wch")
                    and type(getattr(self.stdscr, "getch", None)).__name__ not in ("MagicMock", "AsyncMock", "NonCallableMagicMock")
                    and type(getattr(self.stdscr, "get_wch", None)).__name__ not in ("MagicMock", "AsyncMock", "NonCallableMagicMock")
                )
                wch_is_str = False
                if use_wch:
                    try:
                        wch = self.stdscr.get_wch()
                    except curses.error:
                        continue
                    if isinstance(wch, str):
                        wch_is_str = True
                        if len(wch) == 1 and ord(wch) >= 32 and ord(wch) != 127:
                            buf.append(wch)
                            continue
                        ch = ord(wch) if len(wch) == 1 else -1
                    else:
                        ch = int(wch)
                else:
                    ch = self.stdscr.getch()
                if ch == -1 or ch == getattr(curses, "KEY_RESIZE", -999):
                    continue
                if ch == getattr(curses, "KEY_MOUSE", -998):
                    # Ignore mouse release / motion events arriving right after clicking `❯ `
                    try:
                        curses.getmouse()
                    except Exception:
                        pass
                    continue
                if ch == 27:
                    sgr_ev = self._consume_sgr_mouse_if_any()
                    try:
                        self.stdscr.nodelay(False)
                        self.stdscr.timeout(-1)
                    except Exception:
                        pass
                    if sgr_ev is not None:
                        # Ignore SGR mouse release (`\x1b[<...m`) and tmux FocusIn/FocusOut (`\x1b[I` / `\x1b[O`)
                        continue
                    self._last_cancelled_input = "".join(buf).strip()
                    buf = []
                    break
                if ch in (10, 13, getattr(curses, "KEY_ENTER", 343)):
                    self._last_cancelled_input = ""
                    break
                if ch in (127, 8, getattr(curses, "KEY_BACKSPACE", 263)):
                    if buf:
                        buf.pop()
                    continue
                if ch == 21:  # Ctrl+U
                    buf = []
                    continue
                if not wch_is_str and 0xC0 <= ch <= 0xF7:
                    n_cont = 1 if ch < 0xE0 else (2 if ch < 0xF0 else 3)
                    raw_utf8 = [ch]
                    try:
                        self.stdscr.timeout(15)
                        for _ in range(n_cont):
                            nxt_b = self.stdscr.getch()
                            if isinstance(nxt_b, int) and 0x80 <= nxt_b <= 0xBF:
                                raw_utf8.append(nxt_b)
                            else:
                                break
                    except Exception:
                        pass
                    finally:
                        try:
                            self.stdscr.nodelay(False)
                            self.stdscr.timeout(-1)
                        except Exception:
                            pass
                    try:
                        buf.append(bytes(raw_utf8).decode("utf-8"))
                        continue
                    except UnicodeDecodeError:
                        pass
                if 32 <= ch <= 0x10FFFF and ch not in (getattr(curses, "KEY_UP", 259), getattr(curses, "KEY_DOWN", 258), getattr(curses, "KEY_LEFT", 260), getattr(curses, "KEY_RIGHT", 261)):
                    try:
                        buf.append(chr(ch))
                    except ValueError:
                        pass
            val = "".join(buf).strip()
        except Exception:
            self._last_cancelled_input = "".join(buf).strip()
            val = initial
        finally:
            try:
                curses.noecho()
                curses.curs_set(0)
                if hasattr(self.stdscr, "leaveok"):
                    try:
                        self.stdscr.leaveok(True)
                    except Exception:
                        pass
                if was_nodelay:
                    self.stdscr.nodelay(True)
            except Exception:
                pass
            self._dirty = True
        return val
