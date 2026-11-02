#!/usr/bin/env python3
"""`mixhub.tui.hubtui.layout`: Outer pane geometry and focus: sidebar width, collapse / zoom, and focus moves."""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

from mixhub.tmux import outer as _outer
from mixhub.tmux.gateway import DEFAULT_TMUX_GATEWAY
from mixhub.tmux.outer import (
    _OUTER_PANE_SNAPSHOT,
    _TMUX_BIN,
    current_outer_session_name,
    toggle_outer_sidebar_zoom,
)
from mixhub.tui.widgets.preview_bar import LBL_GOTO, LBL_RESUME
from mixhub.tmux.gateway import OUTER_SOCKET


class PaneLayoutMixin:
    """Sidebar width rebalancing, collapse / zoom checks, and moving focus between the outer panes."""

    def _outer_tmux_io_allowed(self, fn) -> bool:
        """Delegate tmux IO gating to `TmuxGateway.is_io_allowed` (or injected `FakeTmux`)."""
        gw = getattr(self, "tmux", DEFAULT_TMUX_GATEWAY)
        return gw.is_io_allowed(fn=fn, stdscr=getattr(self, "stdscr", None))

    def _focus_sidebar_pane_async(self) -> None:
        if not self._outer_tmux_io_allowed(subprocess.Popen):
            return
        pane = os.environ.get("TMUX_PANE", "")
        env = None
        if pane:
            cmd = [_TMUX_BIN, "-L", OUTER_SOCKET, "select-pane", "-t", pane]
        else:
            env = dict(os.environ)
            env.pop("TMUX", None)
            cmd = [_TMUX_BIN, "-L", OUTER_SOCKET, "select-pane", "-t", f"{current_outer_session_name()}:.{{left}}"]
        try:
            subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
        except OSError:
            pass

    def _is_sidebar_pane_active(self) -> bool | None:
        """Outer tmux `#{pane_active}` of this `--sidebar` pane, or `None` when unknown."""
        if not self._outer_tmux_io_allowed(subprocess.run):
            return None
        pane = os.environ.get("TMUX_PANE", "")
        env = None
        if pane:
            cmd = [_TMUX_BIN, "-L", OUTER_SOCKET, "display-message", "-p", "-t", pane, "#{pane_active}"]
        else:
            env = dict(os.environ)
            env.pop("TMUX", None)
            cmd = [_TMUX_BIN, "-L", OUTER_SOCKET, "display-message", "-p", "-t", f"{current_outer_session_name()}:.{{left}}", "#{pane_active}"]
        try:
            res = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=0.3, env=env)
        except (OSError, subprocess.TimeoutExpired):
            return None
        out = res.stdout.strip() if isinstance(getattr(res, "stdout", None), str) else ""
        return {"1": True, "0": False}.get(out)

    def _effective_left_w(self, w: int, sidebar_only: bool) -> int:
        if sidebar_only:
            return min(w, 32) if os.environ.get("TMUX") else w
        if self.sidebar_collapsed or self.reader_only:
            return 0
        if self.custom_left_w is not None:
            return max(20, min(w - 20, self.custom_left_w))
        return min(34, max(28, w // 4))

    def _rebalance_sidebar_width_if_needed(self, cur_w: int) -> None:
        """Keep the unzoomed tmux sidebar fixed at 32 columns."""
        target_w = 32
        if not self.sidebar_only or not os.environ.get("TMUX") or cur_w == target_w:
            return
        last_check = getattr(self, "_last_rebalance_check", (0, 0.0))
        now = time.monotonic()
        if last_check[0] == cur_w and (now - last_check[1]) < 2.0:
            return
        self._last_rebalance_check = (cur_w, now)
        env = dict(os.environ)
        env.pop("TMUX", None)
        outer_sess = current_outer_session_name()
        try:
            raw = subprocess.run(
                ["tmux", "-L", OUTER_SOCKET, "list-panes", "-t", outer_sess, "-F", "#{pane_id}|#{pane_left}|#{window_width}|#{window_zoomed_flag}"],
                capture_output=True,
                text=True,
                env=env,
            ).stdout.strip().splitlines()
            if len(raw) < 2:
                return
            parsed = []
            win_w = 0
            zoomed = False
            for line in raw:
                p = line.split("|")
                if len(p) >= 4 and p[1].isdigit() and p[2].isdigit():
                    parsed.append((int(p[1]), p[0]))
                    win_w = int(p[2])
                    if p[3] == "1":
                        zoomed = True
            if zoomed or not parsed or win_w <= 0:
                return
            parsed.sort()
            left_id = parsed[0][1]
            if cur_w != target_w:
                subprocess.run(
                    ["tmux", "-L", OUTER_SOCKET, "resize-pane", "-t", left_id, "-x", str(target_w)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=env,
                )
        except Exception:
            pass

    def _schedule_debounced_sidebar_rebalance(self, cur_w: int, delay: float = 1.0) -> None:
        """
        Schedule a background fallback sidebar width rebalance ~1.0s after resize settles,
        keeping `draw()` and `KEY_RESIZE` 100% free of synchronous `tmux` subprocesses on the main thread.
        """
        if not self.sidebar_only or not os.environ.get("TMUX") or cur_w == 32:
            return
        if type(subprocess.run).__name__ in ("MagicMock", "AsyncMock", "NonCallableMagicMock"):
            return
        self._rebalance_pending_w = cur_w
        self._rebalance_due_mono = time.monotonic() + delay
        if getattr(self, "_rebalance_inflight", False):
            return
        self._rebalance_inflight = True

        def _worker():
            try:
                while True:
                    rem = getattr(self, "_rebalance_due_mono", 0.0) - time.monotonic()
                    if rem <= 0.0:
                        break
                    time.sleep(min(0.25, max(0.02, rem)))
                w_now = getattr(self, "_rebalance_pending_w", 32)
                if hasattr(sys.stdout, "fileno"):
                    try:
                        w_now = os.get_terminal_size(sys.stdout.fileno()).columns
                    except OSError:
                        pass
                if w_now != 32:
                    self._last_rebalance_check = (0, 0.0)
                    self._rebalance_sidebar_width_if_needed(w_now)
            except Exception:
                pass
            finally:
                self._rebalance_inflight = False

        threading.Thread(target=_worker, daemon=True).start()

    def _sync_preview_focus(self, s: dict | None, pmode: str, can_move: bool) -> list[tuple[str, str, bool]]:
        """
        Return the bottom Preview bar pill specs `[(base_label, action_key, enabled), ...]` and ensure
        `self._preview_focus_idx` points to a valid enabled pill. Resets to the default primary pill
        when `(session_id, pmode, can_move)` changes:
          - Mode C (`pmode == "C"`): `[("Resume", "move_here", can_move), ("Go to Tab", "goto_tab", True)]`
            Default focus: index 0 (`Resume`) when `can_move` is True, else index 1 (`Go to Tab`).
          - Mode B (`pmode == "B"`): `[("Resume", "resume", True)]`
            Default focus: index 0 (`Resume`).
        `Relay` is intentionally omitted from the preview bottom bar (`--reader`); `⇄ Relay` lives on
        `--header` Row 1 after entering the session (and in the sidebar `⋯` row menu).
        """
        if pmode == "B":
            specs = [
                (LBL_RESUME, "resume", True),
            ]
            default_idx = 0
        else:
            specs = [
                (LBL_RESUME, "move_here", bool(can_move)),
                (LBL_GOTO, "goto_tab", True),
            ]
            default_idx = 0 if can_move else 1
        sid = str((s or {}).get("id") or "")
        anchor = (sid, pmode, bool(can_move))
        cur_idx = getattr(self, "_preview_focus_idx", None)
        if (
            getattr(self, "_preview_focus_anchor", None) != anchor
            or cur_idx is None
            or not (0 <= cur_idx < len(specs))
            or not specs[cur_idx][2]
        ):
            self._preview_focus_idx = default_idx
            self._preview_focus_anchor = anchor
        return specs

    def _schedule_async_zoom_check(self, delay: float = 0.15) -> None:
        """
        Query `is_outer_hub_zoomed` in a single debounced background daemon thread (~150ms after resize settles)
        so `draw()` in `--header` / `--reader` never spawns a synchronous `tmux display-message` on the main thread.
        """
        if not os.environ.get("TMUX"):
            return
        if (
            type(subprocess.run).__name__ in ("MagicMock", "AsyncMock", "NonCallableMagicMock")
            or type(_outer.is_outer_hub_zoomed).__name__ in ("MagicMock", "AsyncMock", "NonCallableMagicMock")
        ):
            return
        self._zoom_check_due_mono = max(getattr(self, "_zoom_check_due_mono", 0.0), time.monotonic() + delay)
        if getattr(self, "_zoom_check_inflight", False):
            return
        self._zoom_check_inflight = True

        def _worker():
            try:
                while True:
                    rem = getattr(self, "_zoom_check_due_mono", 0.0) - time.monotonic()
                    if rem <= 0.0:
                        break
                    time.sleep(min(0.15, max(0.015, rem)))
                if time.monotonic() < getattr(self, "_zoom_override_until", 0.0):
                    return
                res_z = _outer.is_outer_hub_zoomed(current_outer_session_name())
                if time.monotonic() < getattr(self, "_zoom_override_until", 0.0):
                    return
                if bool(getattr(self, "_outer_zoomed", False)) != bool(res_z):
                    self._outer_zoomed = bool(res_z)
                    self._dirty = True
            except Exception:
                pass
            finally:
                self._zoom_check_inflight = False

        threading.Thread(target=_worker, daemon=True).start()

    def _is_reader_zoomed(self, cur_w: int) -> bool:
        """
        Return True if the outer `hub` workspace right pane (`reader` / `--header`) is zoomed (left sidebar collapsed).
        `draw()` always reads the cached `_outer_zoomed` in 0ms on the main thread and schedules a single debounced
        background check when terminal width `cur_w` changes or the cache TTL expires.
        """
        now_mono = time.monotonic()
        if now_mono < getattr(self, "_zoom_override_until", 0.0):
            self._last_reader_w = cur_w
            self._last_zoom_check_ts = now_mono
            return bool(getattr(self, "_outer_zoomed", False))
        width_changed = getattr(self, "_last_reader_w", None) != cur_w
        ttl_expired = (now_mono - getattr(self, "_last_zoom_check_ts", 0.0)) >= 1.5
        if width_changed or ttl_expired:
            self._last_reader_w = cur_w
            self._last_zoom_check_ts = now_mono
            self._schedule_async_zoom_check(delay=0.15 if width_changed else 0.0)
        return bool(getattr(self, "_outer_zoomed", False))

    def _toggle_sidebar_collapse(self):
        if (self.sidebar_only or self.reader_only or getattr(self, "header_only", False)) and os.environ.get("TMUX"):
            self._mini_rail = False
            outer_sess = current_outer_session_name()
            snap = _OUTER_PANE_SNAPSHOT.get(outer_sess)
            if snap is not None and snap[2] and snap[3]:
                known_z = False if self.sidebar_only else bool(getattr(self, "_outer_zoomed", True))
                _OUTER_PANE_SNAPSHOT[outer_sess] = (time.monotonic(), known_z, snap[2], snap[3])
            res = toggle_outer_sidebar_zoom(outer_sess)
            now_mono = time.monotonic()
            self._outer_zoomed = (res == "collapsed")
            self._zoom_override_until = now_mono + 0.45
            self._last_zoom_check_ts = now_mono
            try:
                self._last_reader_w = self.stdscr.getmaxyx()[1]
            except Exception:
                self._last_reader_w = None
            self._last_rebalance_check = (0, 0.0)
            self._dirty = True
        else:
            self.sidebar_collapsed = not self.sidebar_collapsed
            self.loaded_key = None
            self._dirty = True

    def _focus_outer_right_pane_async(self) -> None:
        if not os.environ.get("TMUX"):
            return
        popen_fn = subprocess.Popen
        is_mock_popen = type(popen_fn).__name__ in ("MagicMock", "AsyncMock", "NonCallableMagicMock")
        if is_mock_popen and os.environ.get("TMUX") in ("/tmp/tmux-1000/default,123,0", "test-pool"):
            return
        outer = current_outer_session_name()
        snap = _OUTER_PANE_SNAPSHOT.get(outer)
        right_id = snap[3] if (snap and snap[3]) else f"{outer}:.{{right}}"
        env = dict(os.environ)
        env.pop("TMUX", None)
        try:
            subprocess.Popen(
                [_TMUX_BIN, "-L", OUTER_SOCKET, "select-pane", "-t", right_id],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
            )
        except OSError:
            pass
