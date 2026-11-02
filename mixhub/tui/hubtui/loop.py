#!/usr/bin/env python3
"""`mixhub.tui.hubtui.loop`: The event loop: kqueue (or `evq.PollQueue` off macOS) waits for sidebar / reader, terminal resize, dragged paths, and key dispatch."""
from __future__ import annotations

try:
    import curses
except ImportError:  # Windows without windows-curses installed
    curses = None
from datetime import datetime
import os
import signal
import subprocess
import sys
import time

from mixhub.core.textwidth import disp_trunc
from mixhub.tmux import outer as _outer
from mixhub.tmux import pool as _pool
from mixhub.tmux.outer import current_outer_session_name, toggle_outer_sidebar_zoom
from mixhub.tmux.pool import _session_preview_mode_and_state
from mixhub.tui import paths as _paths
from mixhub.tui import quota as _quota
from mixhub.tui.badges import SPINNER_FRAMES, WORKING_DIAMOND_PERIOD
from mixhub.tui.drafts import save_unsent_draft
from mixhub.tui.hubtui import evq
from mixhub.tui.quota import _QUOTA_SUMMARY_CACHE


class EventLoopMixin:
    """Runs the main loop and dispatches keys and events."""

    def _sync_terminal_size(self) -> bool:
        """Query kernel PTY window size directly via TIOCGWINSZ so detached->attached tmux resizes apply before draw()."""
        if getattr(self, "header_only", False):
            self._check_orphan_header_and_exit()
        try:
            if not hasattr(sys.stdout, "fileno"):
                return False
            sz = os.get_terminal_size(sys.stdout.fileno())
            cur_h, cur_w = self.stdscr.getmaxyx()
            if sz.lines > 0 and sz.columns > 0 and (sz.lines, sz.columns) != (cur_h, cur_w):
                if hasattr(curses, "resizeterm"):
                    curses.resizeterm(sz.lines, sz.columns)
                # `redrawwin` rewrites every cell over tmux's reflowed pane without a blank `clear` frame first
                self._force_full_repaint = True
                if hasattr(self.stdscr, "redrawwin"):
                    try:
                        self.stdscr.redrawwin()
                    except Exception:
                        pass
                self._last_resize_ts = time.monotonic()
                self._schedule_post_resize_client_refresh()
                self.loaded_key = None
                self._dirty = True
                return True
        except Exception:
            pass
        return False

    def _wait_sidebar_event_kqueue(self):
        """
        Event-driven wakeup in `--sidebar` mode using macOS `kqueue`:
          1) `sys.stdin` (instant keyboard/mouse/hover response)
          2) `~/.codex/thread-writer-locks`, `~/.claude/sessions`, `~/.gemini/antigravity-cli/presence`
             so externally resumed sessions update without moving existing rows.
        """
        kq = None
        fds = []
        try:
            kq = evq.kqueue()
            evs = [
                evq.kevent(sys.stdin.fileno(), filter=evq.KQ_FILTER_READ, flags=evq.KQ_EV_ADD),
                evq.kevent(signal.SIGWINCH, filter=evq.KQ_FILTER_SIGNAL, flags=evq.KQ_EV_ADD),
            ]
            base_home = getattr(self.hub, "home", None) or os.path.expanduser("~")
            cached_home, codex_sources = _QUOTA_SUMMARY_CACHE.get("codex_sources", (None, ()))
            if cached_home != base_home:
                codex_sources = _quota._codex_quota_sources(base_home)
            codex_root = os.path.join(base_home, ".codex", "sessions")
            quota_paths = {path for path, _ in codex_sources}
            quota_paths.update(os.path.dirname(path) for path, _ in codex_sources)
            day_path = codex_root
            quota_paths.add(day_path)
            for part in datetime.now().strftime("%Y/%m/%d").split("/"):
                day_path = os.path.join(day_path, part)
                quota_paths.add(day_path)
            try:
                # `handle_cli_on_exit` drops `data/last_exit/<sid>.json` on `/exit`: wake within ms, not on the poll
                os.makedirs(_paths.LAST_EXIT_DIR, exist_ok=True)
            except OSError:
                pass
            db_paths = {
                os.path.join(base_home, ".gemini", "antigravity-cli", "conversation_summaries.db"),
                os.path.join(base_home, ".gemini", "antigravity-cli", "conversation_summaries.db-wal"),
            }
            watch_paths = (
                os.path.join(base_home, ".codex", "thread-writer-locks"),
                os.path.join(base_home, ".claude", "sessions"),
                os.path.join(base_home, ".gemini", "antigravity-cli", "presence"),
                *sorted(db_paths),
                _paths.LAST_EXIT_DIR,
                *sorted(quota_paths),
            )
            quota_fds = set()
            db_fds = set()
            for dpath in watch_paths:
                if os.path.exists(dpath):
                    try:
                        dfd = os.open(dpath, os.O_RDONLY)
                        fds.append(dfd)
                        if dpath in quota_paths:
                            quota_fds.add(dfd)
                        elif dpath in db_paths:
                            db_fds.add(dfd)
                        evs.append(
                            evq.kevent(
                                dfd,
                                filter=evq.KQ_FILTER_VNODE,
                                flags=evq.KQ_EV_ADD | evq.KQ_EV_CLEAR,
                                fflags=evq.KQ_NOTE_WRITE | evq.KQ_NOTE_RENAME | evq.KQ_NOTE_EXTEND | evq.KQ_NOTE_DELETE,
                            )
                        )
                    except OSError:
                        pass
            working = self.counts.get("working", 0) > 0 or any(s.get("agentState") == "working" for s in self.sessions)
            now_before = time.monotonic()
            # Idle: 2.0s data poll only (stdin / SIGWINCH / session dirs are kqueue events, no forced redraws).
            base_timeout = max(0.02, round(WORKING_DIAMOND_PERIOD - (now_before % WORKING_DIAMOND_PERIOD), 4)) if working else 2.0
            timeout = base_timeout
            wm = getattr(self, "wordmark", None)
            wm_delay = (
                wm.next_frame_delay(now_before, working=working)
                if (wm is not None and self.selected_idx == -1 and wm.mode != "off")
                else None
            )
            if wm_delay is not None:
                timeout = min(base_timeout, wm_delay)
                self._dirty = True
            hover_live = (
                (self.hover_idx != -1 or self.hover_pos != (-1, -1))
                and getattr(self, "_last_hover_ts", 0.0) > 0.0
            )
            if hover_live:
                # Stale hover auto-expiry: wake up once the 1.8s hover window lapses
                timeout = min(timeout, max(0.05, 1.8 - (now_before - self._last_hover_ts)))
            if self._any_sidebar_popover_open():
                # Poll outer tmux focus while a popover is open (fallback when no FocusOut arrives)
                timeout = min(timeout, 0.25)
            if getattr(self, "_pending_dir_refresh", False):
                # A session dir / exit file changed while input was being handled: refresh right away
                timeout = 0.0
            kq.control(evs, 0, 0)
            # No per-frame `rollout-*.jsonl` glob here: today's `.codex/sessions/Y/M/D` dirs + newest rollouts are
            # kqueue-watched above (`quota_triggered`), and anything else is picked up by the 3s quota cache TTL
            events = kq.control([], 8, timeout)
            now = time.monotonic()
            if hover_live and getattr(self, "_last_hover_ts", 0.0) > 0.0 and time.monotonic() - self._last_hover_ts >= 1.8:
                self._clear_hover_state()
            self._check_sidebar_popover_focus_loss(now)
            sigwinch_triggered = any(getattr(ev, "filter", None) == evq.KQ_FILTER_SIGNAL for ev in events)
            quota_triggered = any(
                getattr(ev, "filter", None) == evq.KQ_FILTER_VNODE and ev.ident in quota_fds
                for ev in events
            )
            dir_triggered = any(
                getattr(ev, "filter", None) == evq.KQ_FILTER_VNODE and ev.ident not in quota_fds
                for ev in events
            )
            proc_dir_triggered = any(
                getattr(ev, "filter", None) == evq.KQ_FILTER_VNODE and ev.ident not in quota_fds and ev.ident not in db_fds
                for ev in events
            )
            if quota_triggered:
                _QUOTA_SUMMARY_CACHE["ts"] = 0.0
                self._dirty = True
            if sigwinch_triggered:
                self._last_resize_ts = now
                self._sync_terminal_size()
                self._dirty = True
            if dir_triggered:
                self._pending_dir_refresh = True
                if proc_dir_triggered:
                    self._pending_proc_dir_refresh = True
            if any(ev.ident == sys.stdin.fileno() for ev in events) or sigwinch_triggered:
                self._last_input_ts = now
                return
            if working:
                frame = int(now / WORKING_DIAMOND_PERIOD) % len(SPINNER_FRAMES)
                if frame != getattr(self, "_spinner_frame", None):
                    self._spinner_frame = frame
                    self._dirty = True
            poll_interval = 2.0 if (self.sidebar_only and os.environ.get("TMUX")) else (6.0 if working else 2.0)
            has_proc_dir_ev = bool(proc_dir_triggered or getattr(self, "_pending_proc_dir_refresh", False))
            has_dir_ev = bool(dir_triggered or getattr(self, "_pending_dir_refresh", False))
            if has_proc_dir_ev or (has_dir_ev and (now - getattr(self, "_last_sidebar_poll", 0.0) >= 0.8)):
                # Session dir / `/exit` file changed: rescan processes and re-bucket NOW (no 0.8s input
                # throttle, no 60s process-poll wait), so the row moves `Active -> Recent` in one frame;
                # pure SQLite WAL writes (`db_fds`) are coalesced to >=0.8s so streaming never floods discover().
                self._last_sidebar_poll = now
                if has_proc_dir_ev:
                    self.hub._proc_scanned = 0
                self.refresh_data(force=True, reorder=True)
                self._pending_dir_refresh = False
                self._pending_proc_dir_refresh = False
            elif getattr(self.hub, "proc_snapshot_seq", 0) != getattr(self, "_seen_proc_seq", 0):
                # The background process scan landed: re-bucket rows with it (cheap, no `ps` on this thread)
                self._seen_proc_seq = self.hub.proc_snapshot_seq
                self._last_sidebar_poll = now
                self.refresh_data(force=True)
            elif (
                now - getattr(self, "_last_sidebar_poll", 0.0) >= poll_interval
                and now - getattr(self, "_last_input_ts", 0.0) >= 0.8
                and now - getattr(self, "_last_resize_ts", 0.0) >= 1.0
            ):
                self._last_sidebar_poll = now
                if now - getattr(self, "_last_proc_poll", 0.0) >= 30.0:
                    self._last_proc_poll = now
                    self.hub._proc_scanned = 0
                self.refresh_data(force=True, reorder=bool(getattr(self, "_pending_dir_refresh", False)))
                self._pending_dir_refresh = False
                self._pending_proc_dir_refresh = False
            self._poll_external_terminal_seen(now)
            if now - getattr(self, "_last_gc_poll", 0.0) >= 60.0:
                self._last_gc_poll = now
                cur_s = self.current_session()
                excl = {cur_s["id"]} if cur_s else set()
                try:
                    self.hub.gc_idle_resident(exclude_ids=excl)
                except Exception:
                    pass
        except Exception:
            time.sleep(0.25)
        finally:
            for fd in fds:
                try:
                    os.close(fd)
                except OSError:
                    pass
            if kq is not None:
                try:
                    kq.close()
                except Exception:
                    pass

    def _consume_dragged_path_burst(self, first_ch: int) -> str | None:
        """
        Detect when the user drags & drops a file/image into macOS Terminal.app while
        the left sidebar or reader pane has focus. Terminal.app pastes the file path as a
        rapid burst starting with `/`, `~`, `'`, or `"`. If >= 2 follow-up characters
        arrive within ~8ms, consume the entire burst and return the path string so it
        never triggers `/` search or single-key shortcuts (`q`, `r`, `n`, `z`, etc.).
        """
        if first_ch not in (ord("/"), ord("~"), ord("'"), ord('"')):
            return None
        chars = [chr(first_ch)]
        try:
            self.stdscr.nodelay(True)
            deadline = time.monotonic() + 0.012
            while time.monotonic() < deadline and len(chars) < 1024:
                nxt = self.stdscr.getch()
                if nxt == -1:
                    if len(chars) == 1:
                        time.sleep(0.002)
                        nxt = self.stdscr.getch()
                        if nxt == -1:
                            break
                    else:
                        break
                if nxt in (10, 13):
                    break
                if 32 <= nxt <= 0x10FFFF:
                    try:
                        chars.append(chr(nxt))
                        deadline = time.monotonic() + 0.006
                    except ValueError:
                        break
                else:
                    break
        except Exception:
            pass
        finally:
            if not (self.reader_only or self.sidebar_only or getattr(self, "header_only", False)):
                try:
                    self.stdscr.timeout(-1)
                except Exception:
                    pass
        if len(chars) >= 3:
            return "".join(chars)
        # If only 1 followup char was read, push it back if possible or return None
        if len(chars) == 2:
            try:
                curses.ungetch(ord(chars[1]))
            except Exception:
                pass
        return None

    def _forward_dragged_path_to_right_pane(self, burst_text: str) -> None:
        """Forward a dragged file/image path burst from the left sidebar or reader pane to the active right pane input."""
        if not burst_text:
            return
        s = self.current_session()
        text_to_send = burst_text if burst_text.endswith(" ") else (burst_text + " ")
        pool_env = dict(os.environ)
        pool_env.pop("TMUX", None)
        if s:
            wname = f"w-{s['tool']}-{s['id'][:12]}"
            res = subprocess.run(
                ["tmux", "-L", "hubpool", "list-windows", "-t", "pool", "-F", "#{window_name}"],
                capture_output=True,
                text=True,
                env=pool_env,
            )
            raw_out = res.stdout if isinstance(getattr(res, "stdout", None), str) else ""
            has_win = [w.strip() for w in raw_out.splitlines() if w.strip()]
            if wname in has_win:
                target_pane = f"pool:{wname}"
                subprocess.run(
                    ["tmux", "-L", "hubpool", "send-keys", "-t", target_pane, "-l", text_to_send],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=pool_env,
                )
                if os.environ.get("TMUX"):
                    _outer.focus_outer_right_pane()
                self.status_msg = f"Forwarded path to right pane: {disp_trunc(burst_text.strip(), 28)}"
                self._dirty = True
                return
            save_unsent_draft(s["id"], text_to_send, path=self.unsent_drafts_path)
        if not self.sidebar_only and not self.reader_only:
            self._prompt_and_send_unified_message(initial_text=text_to_send)
        else:
            self.status_msg = f"Saved dropped path to draft: {disp_trunc(burst_text.strip(), 28)}"
            self._dirty = True

    def _wait_reader_event_kqueue(self):
        """
        Wait for input and relevant file changes using macOS `kqueue`:
          1) `sys.stdin` (keyboard/mouse input)
          2) `os.path.dirname(ACTIVE_SEL_PATH)` (sidebar cursor changes)
          3) Active session's transcript file (live output appended)
        Headers only watch the attention badge file; title metadata is polled every 2s.
        """
        kq = None
        dir_fd = -1
        file_fd = -1
        try:
            kq = evq.kqueue()
            evs = [
                evq.kevent(sys.stdin.fileno(), filter=evq.KQ_FILTER_READ, flags=evq.KQ_EV_ADD),
                evq.kevent(signal.SIGWINCH, filter=evq.KQ_FILTER_SIGNAL, flags=evq.KQ_EV_ADD),
            ]
            is_header = bool(getattr(self, "header_only", False))
            extra_fds: list[int] = []
            if is_header:
                # Avoid waking every header for streaming output or unrelated cache/log writes.
                # Re-open on each wait to follow atomic replacements; missing files use the timer.
                watch_paths = [self.attention_path]
            else:
                sel_file = self._active_selection_path()
                os.makedirs(os.path.dirname(sel_file), exist_ok=True)
                if os.path.exists(sel_file):
                    watch_paths = [sel_file, self.attention_path]
                else:
                    watch_paths = [os.path.dirname(sel_file), self.attention_path]
            for wp in watch_paths:
                if wp and os.path.exists(wp):
                    try:
                        wfd = os.open(wp, os.O_RDONLY)
                        if dir_fd < 0:
                            dir_fd = wfd
                        else:
                            extra_fds.append(wfd)
                        evs.append(
                            evq.kevent(
                                wfd,
                                filter=evq.KQ_FILTER_VNODE,
                                flags=evq.KQ_EV_ADD | evq.KQ_EV_CLEAR,
                                fflags=evq.KQ_NOTE_WRITE | evq.KQ_NOTE_RENAME | evq.KQ_NOTE_EXTEND | evq.KQ_NOTE_DELETE,
                            )
                        )
                    except OSError:
                        pass
            s = self.current_session()
            if s and not is_header:
                skip_tpath_watch = bool(
                    self.reader_only
                    and (s.get("resident") or _pool.find_hubpool_window_for_session(str(s.get("id") or ""), allow_probe=False))
                )
                if not skip_tpath_watch:
                    full = self.hub.sessions.get(s["tool"] + ":" + s["id"]) or s
                    tpath = full.get("path")
                    if tpath and os.path.exists(tpath):
                        file_fd = os.open(tpath, os.O_RDONLY)
                        evs.append(
                            evq.kevent(
                                file_fd,
                                filter=evq.KQ_FILTER_VNODE,
                                flags=evq.KQ_EV_ADD | evq.KQ_EV_CLEAR,
                                fflags=evq.KQ_NOTE_WRITE | evq.KQ_NOTE_EXTEND | evq.KQ_NOTE_ATTRIB,
                            )
                        )
            now_mono = time.monotonic()
            startup_elapsed = now_mono - getattr(self, "_start_mono", 0.0)
            wait_timeout = 0.08 if (not is_header and startup_elapsed < 4.0) else 2.0
            for deadline in (
                getattr(self, "_preview_pulse_until", 0.0),
                getattr(self, "_preview_flash_until", 0.0),
                getattr(self, "_preview_hint_until", 0.0),
            ):
                if deadline > now_mono:
                    wait_timeout = min(wait_timeout, max(0.02, deadline - now_mono + 0.01))
            if getattr(self, "_zoom_check_inflight", False):
                wait_timeout = min(wait_timeout, 0.18)
            kq.control(evs, 0, 0)
            if self.reader_only and not getattr(self, "header_only", False):
                try:
                    st_sel = os.stat(self._active_selection_path())
                    if (st_sel.st_ino, st_sel.st_mtime_ns, st_sel.st_size) != getattr(self, "_last_sel_sig", None):
                        wait_timeout = 0.0
                except OSError:
                    pass
            if wait_timeout > 0.0:
                kq.control([], 4, wait_timeout)
            if any(
                0.0 < d <= time.monotonic()
                for d in (
                    getattr(self, "_preview_pulse_until", 0.0),
                    getattr(self, "_preview_flash_until", 0.0),
                    getattr(self, "_preview_hint_until", 0.0),
                )
            ):
                self._dirty = True
            self._sync_terminal_size()
        except Exception:
            time.sleep(0.25)
        finally:
            if file_fd >= 0:
                try:
                    os.close(file_fd)
                except OSError:
                    pass
            if dir_fd >= 0:
                try:
                    os.close(dir_fd)
                except OSError:
                    pass
            for xfd in locals().get("extra_fds", ()):
                try:
                    os.close(xfd)
                except OSError:
                    pass
            if kq is not None:
                try:
                    kq.close()
                except Exception:
                    pass

    def run(self):
        try:
            curses.curs_set(0)
        except Exception:
            pass
        if hasattr(self.stdscr, "leaveok"):
            try:
                self.stdscr.leaveok(True)
            except Exception:
                pass
        self.stdscr.keypad(True)
        try:
            mouse_mask = (
                getattr(curses, "BUTTON1_CLICKED", 0)
                | getattr(curses, "BUTTON1_PRESSED", 0)
                | getattr(curses, "BUTTON1_RELEASED", 0x1)
                | getattr(curses, "BUTTON1_DOUBLE_CLICKED", 0)
                | getattr(curses, "BUTTON1_TRIPLE_CLICKED", 0)
                | getattr(curses, "BUTTON4_PRESSED", 0x80000)
                | getattr(curses, "BUTTON5_PRESSED", 0x200000)
                | getattr(curses, "REPORT_MOUSE_POSITION", 0x8000000)
            )
            curses.mousemask(mouse_mask)
            curses.mouseinterval(0)
            # Enable SGR mouse protocol (?1006h) + any-event hover motion (?1003h) so Wheel Down (65),
            # hover highlight on ◧ / sidebar rows / ⋮ divider, and divider drag work on macOS ncurses 5.4
            sys.stdout.write("\x1b[?1000h\x1b[?1002h\x1b[?1003h\x1b[?1004h\x1b[?1006h")
            sys.stdout.flush()
        except Exception:
            pass

        if getattr(self, "header_only", False):
            self._yield_header_focus_to_cli()
        if self.reader_only or self.sidebar_only or getattr(self, "header_only", False):
            self.stdscr.nodelay(True)
            self._ensure_live_tmux_mouse_bindings()
            if self.reader_only and not getattr(self, "header_only", False):
                self._sync_reader_selection()
                self._sync_home_shell()
        else:
            self.stdscr.timeout(-1)

        try:
            while True:
                if self.reader_only and not getattr(self, "header_only", False):
                    self._sync_reader_selection()
                    self._sync_home_shell()
                elif getattr(self, "header_only", False):
                    self._check_orphan_header_and_exit()
                    self._sync_header_session_meta()
                elif self.sidebar_only:
                    self._sync_sidebar_selection()
                    if self._merge_seen_from_disk():
                        self._dirty = True
                        self._publish_attention()
                if (self.reader_only or getattr(self, "header_only", False)) and self._poll_published_attention():
                    self._dirty = True
                if self._dirty:
                    self.draw()

                if self.reader_only or getattr(self, "header_only", False):
                    ch = self.stdscr.getch()
                    if ch == -1:
                        self._wait_reader_event_kqueue()
                        continue
                elif self.sidebar_only:
                    ch = self.stdscr.getch()
                    if ch == -1:
                        self._wait_sidebar_event_kqueue()
                        continue
                else:
                    ch = self.stdscr.getch()
                    if ch == -1:
                        continue

                self._last_input_ts = time.monotonic()
                if ch == 27:
                    sgr_ev = self._consume_sgr_mouse_if_any()
                    if sgr_ev is not None:
                        self._handle_mouse(parsed_event=sgr_ev)
                        if (
                            getattr(self, "header_only", False)
                            and not getattr(self, "_relay_menu", False)
                            and sgr_ev[0] == 0
                            and not sgr_ev[3]
                        ):
                            self._yield_header_focus_to_cli()
                        continue
                    # Bare Esc: close floating menu or clear filter/message, never kill a split pane!
                    self._dirty = True
                    if getattr(self, "provider_dropdown_open", False):
                        self.provider_dropdown_open = False
                        continue
                    if getattr(self, "_new_session_menu", False):
                        self._new_session_menu = False
                        continue
                    if getattr(self, "_relay_menu", False):
                        self._relay_menu = False
                        if getattr(self, "header_only", False):
                            self._yield_header_focus_to_cli()
                        continue
                    if self.query:
                        self.query = ""
                        self.refresh_data(fast_only=True)
                    self.status_msg = ""
                    if getattr(self, "header_only", False):
                        self._yield_header_focus_to_cli()
                    continue

                if ch == curses.KEY_MOUSE:
                    self._handle_mouse()
                    if (
                        getattr(self, "header_only", False)
                        and not getattr(self, "_relay_menu", False)
                        and getattr(self, "_last_mouse_was_click", False)
                    ):
                        self._yield_header_focus_to_cli()
                    continue

                if getattr(self, "provider_dropdown_open", False):
                    self._dirty = True
                    prov_key_map = {
                        ord("1"): None, ord("a"): None, ord("A"): None,
                        ord("2"): "claude", ord("c"): "claude", ord("C"): "claude",
                        ord("3"): "codex", ord("x"): "codex", ord("X"): "codex",
                        ord("4"): "gemini", ord("g"): "gemini", ord("G"): "gemini",
                        ord("5"): "deepseek", ord("d"): "deepseek", ord("D"): "deepseek",
                    }
                    if ch in prov_key_map:
                        self._select_provider_filter(prov_key_map[ch])
                    else:
                        self.provider_dropdown_open = False
                    continue

                if getattr(self, "_new_session_menu", False):
                    self._dirty = True
                    new_key_map = {
                        ord("1"): "claude", ord("c"): "claude", ord("C"): "claude",
                        ord("2"): "codex", ord("x"): "codex", ord("X"): "codex",
                        ord("3"): "agy", ord("g"): "agy", ord("G"): "agy",
                        ord("4"): "opencode", ord("o"): "opencode", ord("O"): "opencode",
                        ord("d"): "deepseek", ord("D"): "deepseek",
                        10: "claude", 13: "claude",
                    }
                    shown = [row[0] for row in self.new_session_items()]
                    if ord("1") <= ch <= ord("9"):  # digits follow the rows actually shown
                        new_key_map.pop(ch, None)
                        if ch - ord("1") < len(shown):
                            new_key_map[ch] = shown[ch - ord("1")]
                    if ch in new_key_map:
                        self._new_session_menu = False
                        self._launch_new_agent_session(new_key_map[ch])
                        continue
                    self._new_session_menu = False
                    continue

                if getattr(self, "_relay_menu", False):
                    self._dirty = True
                    relay_st = self._eff_relay_state()
                    enter_keys = (10, 13, curses.KEY_ENTER)
                    if relay_st == "PICK_CLAUDE_BACKEND":
                        if ch in (ord("1"), ord("a"), ord("A")) + enter_keys:
                            self._handle_relay_action("relay:claude")
                        elif ch in (ord("2"), ord("d"), ord("D")):
                            self._handle_relay_action("relay:deepseek")
                        elif ch in (ord("<"), ord("b"), ord("B"), curses.KEY_LEFT, curses.KEY_BACKSPACE, 127):
                            self._handle_relay_action("relay_back")
                        else:
                            self._set_relay_state("CLOSED")
                    elif relay_st == "PICK_AGENT":
                        s = self.current_session()
                        cur_tool = ((s or {}).get("tool") or "").lower()
                        default_target = "codex" if cur_tool != "codex" else "claude"
                        if ch in (ord("1"), ord("c"), ord("C")):
                            self._handle_relay_action("relay_pick_claude")
                        elif ch in (ord("2"), ord("g"), ord("G")):
                            self._handle_relay_action("relay:agy")
                        elif ch in (ord("3"), ord("x"), ord("X")):
                            self._handle_relay_action("relay:codex")
                        elif ch in (ord("4"), ord("d"), ord("D")):
                            self._handle_relay_action("relay:deepseek")
                        elif ch in enter_keys:
                            self._handle_relay_action(f"relay:{default_target}")
                        else:
                            self._set_relay_state("CLOSED")
                    if getattr(self, "header_only", False) and self._eff_relay_state() == "CLOSED":
                        self._yield_header_focus_to_cli()
                    continue

                if getattr(self, "header_only", False) and ch != curses.KEY_RESIZE:
                    env = dict(os.environ)
                    env.pop("TMUX", None)
                    subprocess.Popen(
                        ["tmux", "-L", "hubpool", "select-pane", "-t", ".{bottom}"],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        close_fds=False,
                        env=env,
                    )
                    continue

                if ch in (ord("/"), ord("~"), ord("'"), ord('"')):
                    drag_burst = self._consume_dragged_path_burst(ch)
                    if drag_burst is not None:
                        self._forward_dragged_path_to_right_pane(drag_burst)
                        continue

                if self.active_ephemeral_toast():
                    toast_act = self.handle_toast_key(ch)
                    if toast_act in ("great", "fine", "dismissed_permanently"):
                        continue

                self._dirty = True
                h, w = self.stdscr.getmaxyx()
                body_h = max(4, h - 6)

                if ch == curses.KEY_RESIZE:
                    self._force_full_repaint = True
                    try:
                        self.stdscr.redrawwin()
                    except Exception:
                        pass
                    self._last_resize_ts = time.monotonic()
                    self._schedule_debounced_sidebar_rebalance(self.stdscr.getmaxyx()[1])
                    self.loaded_key = None
                    continue

                if ch == 12:  # Ctrl+L explicit full repaint
                    self._force_full_repaint = True
                    self._dirty = True
                    continue

                if ch == curses.KEY_UP:
                    if self.reader_only:
                        # `--reader`: arrows only scroll the preview, never change the selection
                        if not self.current_session():
                            self.welcome_scroll = max(0, int(getattr(self, "welcome_scroll", 0)) - 1)
                        else:
                            new_scroll = max(0, self.msg_scroll - 3)
                            if new_scroll == 0 and self.is_tail_truncated:
                                self._expand_tail_preserving_viewport()
                            else:
                                self.msg_scroll = new_scroll
                    elif self.sidebar_only:
                        # `--sidebar`: NEVER switch sessions on KEY_UP (Terminal.app alternate-screen scroll sends
                        # KEY_UP/KEY_DOWN to the focused pane). If hovering inside sidebar, scroll list_scroll;
                        # otherwise forward the scroll to `pool:reader` and hand outer tmux focus to the right pane.
                        hov_in_sidebar = (
                            self.hover_pos != (-1, -1)
                            and 0 <= self.hover_pos[0] < max(1, w - 1)
                        )
                        if (
                            not hov_in_sidebar
                            and getattr(self, "_active_hubpool_win", "reader") == "reader"
                            and self._forward_preview_scroll_from_sidebar("Up")
                        ):
                            pass
                        else:
                            self.list_scroll = max(0, self.list_scroll - 3)
                elif ch == curses.KEY_DOWN:
                    if self.reader_only:
                        # `--reader`: arrows only scroll the preview, never change the selection
                        if not self.current_session():
                            self.welcome_scroll = int(getattr(self, "welcome_scroll", 0)) + 1
                        else:
                            self.msg_scroll += 3
                    elif self.sidebar_only:
                        # `--sidebar`: NEVER switch sessions on KEY_DOWN. If hovering inside sidebar, scroll list_scroll;
                        # otherwise forward the scroll to `pool:reader` and hand outer tmux focus to the right pane.
                        hov_in_sidebar = (
                            self.hover_pos != (-1, -1)
                            and 0 <= self.hover_pos[0] < max(1, w - 1)
                        )
                        if (
                            not hov_in_sidebar
                            and getattr(self, "_active_hubpool_win", "reader") == "reader"
                            and self._forward_preview_scroll_from_sidebar("Down")
                        ):
                            pass
                        else:
                            _, _, list_h = self._list_bounds(h)
                            drows = self._sidebar_display_rows()
                            max_list_scroll = max(0, len(drows) - list_h)
                            self.list_scroll = min(max_list_scroll, self.list_scroll + 3)
                elif self.reader_only and not getattr(self, "header_only", False) and ch == curses.KEY_LEFT:
                    s_cur = self.current_session()
                    if s_cur and self._eff_relay_state() != "CLOSED":
                        self._set_relay_state("CLOSED")
                    elif s_cur:
                        pmode, _, can_move = _session_preview_mode_and_state(s_cur)
                        specs = self._sync_preview_focus(s_cur, pmode, can_move)
                        prev_cands = [i for i, (_, _, en) in enumerate(specs) if en and i < self._preview_focus_idx]
                        if prev_cands:
                            self._preview_focus_idx = prev_cands[-1]
                            self.hover_pos = (-1, -1)
                            self._dirty = True
                        elif os.environ.get("TMUX"):
                            _outer.focus_outer_left_pane(current_outer_session_name())
                    elif os.environ.get("TMUX"):
                        _outer.focus_outer_left_pane(current_outer_session_name())
                elif self.reader_only and not getattr(self, "header_only", False) and ch == curses.KEY_RIGHT:
                    s_cur = self.current_session()
                    if s_cur and self._eff_relay_state() == "CLOSED":
                        pmode, _, can_move = _session_preview_mode_and_state(s_cur)
                        specs = self._sync_preview_focus(s_cur, pmode, can_move)
                        next_cands = [i for i, (_, _, en) in enumerate(specs) if en and i > self._preview_focus_idx]
                        if next_cands:
                            self._preview_focus_idx = next_cands[0]
                            self.hover_pos = (-1, -1)
                            self._dirty = True
                elif ch == curses.KEY_PPAGE:
                    new_scroll = max(0, self.msg_scroll - max(5, body_h // 2))
                    if new_scroll == 0 and self.is_tail_truncated:
                        self._expand_tail_preserving_viewport()
                    else:
                        self.msg_scroll = new_scroll
                elif ch == curses.KEY_NPAGE:
                    self.msg_scroll += max(5, body_h // 2)
                elif ch in (29, ord("∫")):
                    if (self.sidebar_only or self.reader_only) and os.environ.get("TMUX"):
                        toggle_outer_sidebar_zoom()
                    else:
                        self.sidebar_collapsed = not self.sidebar_collapsed
                        self.loaded_key = None
                elif ch in (curses.KEY_RIGHT, 10, 13, curses.KEY_ENTER):
                    if self.selected_idx < 0 and self.sessions:
                        self.selected_idx = self.list_scroll
                    s = self.current_session()
                    if s:
                        self._mark_session_seen(s)
                        if self.reader_only and not getattr(self, "header_only", False):
                            pmode, _, can_move = _session_preview_mode_and_state(s)
                            specs = self._sync_preview_focus(s, pmode, can_move)
                            _, act_key, act_en = specs[self._preview_focus_idx]
                            self._execute_preview_action(act_key, act_en)
                        elif self.sidebar_only:
                            self._on_sidebar_selection_changed(activate_live=True)
                elif self.sidebar_only and ch in (ord("n"), ord("N")):
                    self._new_session_menu = True
                    self._note_sidebar_popover_opened("new_session")
                elif (
                    self.reader_only
                    and not getattr(self, "header_only", False)
                    and self.current_session()
                    and 32 <= ch <= 0x10FFFF
                    and ch not in (
                        curses.KEY_UP,
                        curses.KEY_DOWN,
                        curses.KEY_LEFT,
                        curses.KEY_RIGHT,
                        curses.KEY_PPAGE,
                        curses.KEY_NPAGE,
                        curses.KEY_RESIZE,
                        curses.KEY_MOUSE,
                    )
                ):
                    now_m = time.monotonic()
                    if now_m >= getattr(self, "_preview_pulse_until", 0.0):
                        self._preview_pulse_until = now_m + 0.18
                        self._preview_flash_until = self._preview_pulse_until
                    self._preview_hint_until = now_m + 2.5
                    self._dirty = True
                elif not self.current_session() and ch == ord("m"):
                    self.mark_welcome_unread_seen()
        finally:
            try:
                sys.stdout.write("\x1b[?1003l\x1b[?1004l\x1b[?1006l")
                sys.stdout.flush()
            except Exception:
                pass
