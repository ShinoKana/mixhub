#!/usr/bin/env python3
"""`mixhub.tui.hubtui.selection_sync`: Keeping sidebar, reader and the outer window in step through `active_selection.json`."""
from __future__ import annotations

import os
import subprocess
import time

from mixhub.tmux import new_sessions as _new_sessions
from mixhub.tmux import outer as _outer
from mixhub.tmux import pool as _pool
from mixhub.tmux.commands import hubpool_win_name
from mixhub.tmux.home_shell import sync_home_shell
from mixhub.tmux.new_sessions import _NEW_HUBPOOL_SESSIONS, _NEW_WIN_RE, _make_new_hubpool_session_dict
from mixhub.tmux.outer import current_outer_session_name, current_pool_session_name
from mixhub.tmux.pool import _HUBPOOL_WINS_CACHE
from mixhub.tmux.session_state import _remove_from_working_set_file
from mixhub.tui import paths as _paths
from mixhub.tui import selection as _selection


class SelectionSyncMixin:
    """Publishes and adopts the selected session across the sidebar and reader processes."""

    def _sync_home_shell(self) -> None:
        """`--reader`: show the Home shell on Home, park it while a session is shown (tmux calls only on change)."""
        if not os.environ.get("TMUX") or getattr(self, "_isolated_state", False):
            return
        home = self.selected_idx == -1
        if home == getattr(self, "_home_shell_home", None):
            return
        self._home_shell_home = home
        socket = os.path.basename(os.environ.get("TMUX", "").split(",")[0]) or "hubpool"
        sync_home_shell(home, os.environ.get("TMUX_PANE", ""), socket=socket)

    def _allow_tmux_side_effects(self) -> bool:
        if not getattr(self, "_isolated_state", False):
            return True
        return (
            type(subprocess.Popen).__name__ in ("MagicMock", "AsyncMock", "NonCallableMagicMock")
            or type(subprocess.run).__name__ in ("MagicMock", "AsyncMock", "NonCallableMagicMock")
        )

    def _active_selection_path(self) -> str:
        root = getattr(self.hub, "root", _paths.ROOT)
        return _paths._effective_active_sel_path(root) if os.path.abspath(root) != os.path.abspath(_paths.ROOT) else _paths._effective_active_sel_path()

    def _write_active_selection(self, session: dict | None, **kwargs) -> None:
        _selection.write_active_selection(session, path=self._active_selection_path(),
                               sync_title=self._allow_tmux_side_effects(), **kwargs)

    def _read_active_selection(self) -> dict | None:
        return _selection.read_active_selection(path=self._active_selection_path())

    def _sync_window_title(self, session: dict | None, **kwargs) -> None:
        if self._allow_tmux_side_effects():
            _outer._sync_outer_window_title(session, **kwargs)

    def _adopt_reader_selection(self, sel: dict) -> None:
        """Load just the requested session; a reader never needs a process or transcript inventory."""
        sid, tool = str(sel.get("id") or ""), str(sel.get("tool") or "")
        meta = self.hub.get_lightweight_session_meta(tool, sid)
        s = dict(meta) if isinstance(meta, dict) else {
            "id": sid, "tool": tool or "claude", "title": sel.get("title") or sid[:12],
            "path": sel.get("path") or "", "cwd": self.hub.home, "updatedAt": 0,
            "_placeholder": True,
        }
        # Lightweight metadata alone is not evidence of a running CLI.
        s.update(isLive=False, processes=[])
        if sel.get("hubpoolWindow"):
            s["hubpoolWindow"] = sel["hubpoolWindow"]
        self.sessions = [s]
        self.selected_idx = 0
        self.loaded_key = None
        self.tail_step_idx = 0
        self.msg_scroll = 999999

    def _on_sidebar_selection_changed(self, activate_live: bool = False) -> bool:
        """Select an existing CLI or ask the reader to draw a preview before showing it."""
        s = self.current_session()
        if not os.environ.get("TMUX"):
            self._write_active_selection(s, activate_reader=True)
            return False

        env = dict(os.environ)
        env.pop("TMUX", None)
        prev_wname = str(getattr(self, "_active_hubpool_win", None) or "")
        is_mock_run = type(subprocess.run).__name__ in ("MagicMock", "AsyncMock", "NonCallableMagicMock")
        close_windows = []
        if not is_mock_run:
            for cand_w in dict.fromkeys([prev_wname, *list(_NEW_HUBPOOL_SESSIONS.keys())]):
                if not _NEW_WIN_RE.match(cand_w) or (s and hubpool_win_name(s) == cand_w):
                    continue
                try:
                    if _new_sessions._inspect_new_hubpool_window_draft(cand_w) != "":
                        continue
                    # An empty box may mean the first message was just sent: same guard as the periodic sweep.
                    if _new_sessions._new_window_in_use(cand_w, _NEW_WIN_RE.match(cand_w).group(1)):
                        continue
                    close_windows.append(cand_w)
                except (OSError, RuntimeError, subprocess.SubprocessError):
                    pass

        hp_win = str((s or {}).get("hubpoolWindow") or "")
        known_wins = _HUBPOOL_WINS_CACHE.get("wins")
        native = bool(s and (
            activate_live
            or (hp_win and isinstance(known_wins, set) and hp_win in known_wins)
            or _pool.find_hubpool_window_for_session(s.get("id", "")) is not None
        ))
        self._write_active_selection(s, activate_reader=not native,
                               close_window=close_windows[0] if close_windows else "",
                               close_windows=close_windows[1:])
        if native:
            activated = _pool.activate_session_in_hubpool(s, current_outer_session_name(), force_takeover=activate_live, app=self, focus_outer=activate_live)
            if not activated:
                return False
            for close_window in close_windows:
                subprocess.Popen(
                    ["tmux", "-L", "hubpool", "kill-window", "-t", f"pool:{close_window}"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
                )
        else:
            # Closing the old window here would let tmux expose a stale reader frame.
            # The reader selects itself and closes all abandoned blank windows after draw().
            self._active_hubpool_win = "reader"
        for close_window in close_windows:
            _NEW_HUBPOOL_SESSIONS.pop(close_window, None)
            _pool._record_hubpool_window(close_window, present=False)
            m_new = _NEW_WIN_RE.match(close_window)
            _remove_from_working_set_file(m_new.group(1), m_new.group(2), path=self.hub.working_set_path)
        if close_windows:
            self.sessions = [item for item in self.sessions if hubpool_win_name(item) not in close_windows]
            sid = s.get("id") if s else None
            self.selected_idx = next(
                (i for i, item in enumerate(self.sessions) if item is s or (sid and item.get("id") == sid)),
                -1,
            )
        return True

    def _sync_reader_selection(self) -> bool:
        """In `--reader` mode, check if `ACTIVE_SEL_PATH` or active transcript changed. Returns True if changed."""
        changed = False
        activation = None
        try:
            st = os.stat(self._active_selection_path())
            sel_sig = (st.st_ino, st.st_mtime_ns, st.st_size)
            if st.st_mtime != self._last_sel_mtime or sel_sig != getattr(self, "_last_sel_sig", None):
                self._last_sel_mtime = st.st_mtime
                self._last_sel_sig = sel_sig
                sel = self._read_active_selection()
                if sel:
                    if sel.get("activateReader"):
                        activation = sel
                    if sel.get("empty") or not sel.get("id"):
                        if self.selected_idx != -1:
                            self.selected_idx = -1
                            self.loaded_key = None
                            changed = True
                    else:
                        target_id = str(sel.get("id") or "")
                        target_tool = str(sel.get("tool") or "")
                        if target_id.startswith("new-") and target_tool:
                            wname = str(sel.get("hubpoolWindow") or f"w-{target_tool}-{target_id}")
                            if wname not in _NEW_HUBPOOL_SESSIONS:
                                adopted = _make_new_hubpool_session_dict(target_tool, target_id, wname)
                                if sel.get("title"):
                                    adopted["title"] = str(sel["title"])
                                _NEW_HUBPOOL_SESSIONS[wname] = adopted
                                _pool._record_hubpool_window(wname, present=True)
                        found = False
                        for idx, s in enumerate(self.sessions):
                            if s["id"] == target_id and (not target_tool or s["tool"] == target_tool):
                                found = True
                                if sel.get("title") and s.get("title") != sel.get("title"):
                                    s["title"] = sel["title"]
                                    self.loaded_key = None
                                    changed = True
                                if self.selected_idx != idx:
                                    self.selected_idx = idx
                                    self.tail_step_idx = 0
                                    self.msg_scroll = 999999
                                    changed = True
                                break
                        if not found:
                            _HUBPOOL_WINS_CACHE["ts"] = 0.0
                            self.refresh_data(force=True)
                            for idx, s in enumerate(self.sessions):
                                if s["id"] == target_id and (not target_tool or s["tool"] == target_tool):
                                    found = True
                                    self.selected_idx = idx
                                    self.tail_step_idx = 0
                                    self.msg_scroll = 999999
                                    changed = True
                                    break
                        if not found and target_id:
                            placeholder_s = {
                                "id": target_id,
                                "tool": target_tool or "claude",
                                "title": str(sel.get("title") or target_id[:12]),
                                "path": str(sel.get("path") or ""),
                                "cwd": os.path.expanduser("~"),
                                "updatedAt": time.time(),
                                "isLive": False,
                                "processes": [],
                                "_placeholder": True,
                            }
                            if sel.get("hubpoolWindow"):
                                placeholder_s["hubpoolWindow"] = str(sel["hubpoolWindow"])
                            self.sessions.insert(0, placeholder_s)
                            self.selected_idx = 0
                            self.loaded_key = None
                            self.tail_step_idx = 0
                            self.msg_scroll = 999999
                            changed = True
        except OSError:
            pass

        s = self.current_session()
        if s:
            skip_bg_resident_poll = bool(
                self.reader_only
                and not getattr(self, "header_only", False)
                and not changed
                and not activation
                and (s.get("resident") or _pool.find_hubpool_window_for_session(str(s.get("id") or ""), allow_probe=False))
            )
            if not skip_bg_resident_poll:
                full = self.hub.sessions.get(s["tool"] + ":" + s["id"]) or s
                tpath = full.get("path")
                if tpath and os.path.exists(tpath):
                    try:
                        tst = os.stat(tpath)
                        sig = (tpath, tst.st_mtime_ns, tst.st_size)
                        if sig != self._last_transcript_sig:
                            self._last_transcript_sig = sig
                            s["updatedAt"] = tst.st_mtime
                            self.loaded_key = None
                            changed = True
                    except OSError:
                        pass
        if changed:
            self._dirty = True
        if activation and self.reader_only and not getattr(self, "header_only", False):
            current = self.current_session()
            matches = ((not current and activation.get("empty")) or
                       (current and current.get("id") == activation.get("id")
                        and current.get("tool") == activation.get("tool")))
            if matches:
                self.draw()
                # A newer click/native activation cancels an in-flight preview request.
                if os.environ.get("TMUX") and self._read_active_selection() == activation:
                    env = dict(os.environ)
                    env.pop("TMUX", None)
                    cmd = ["tmux", "-L", "hubpool", "select-window", "-t", f"{current_pool_session_name()}:reader"]
                    close_windows = [activation.get("closeWindow"), *(activation.get("closeWindows") or [])]
                    for close_window in dict.fromkeys(str(w or "") for w in close_windows):
                        if _NEW_WIN_RE.fullmatch(close_window):
                            cmd += [";", "kill-window", "-t", f"pool:{close_window}"]
                    try:
                        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
                    except OSError:
                        pass
        elif changed and self.reader_only and not getattr(self, "header_only", False):
            # Keep background `pool:reader` framebuffer synced to the active selection so switching back never flashes Home.
            self.draw()
        return changed

    def _sync_sidebar_selection(self) -> bool:
        """In `--sidebar` mode, sync selection immediately when `--reader` updates `ACTIVE_SEL_PATH` (e.g. launching a new session)."""
        try:
            st = os.stat(self._active_selection_path())
            sel_sig = (st.st_ino, st.st_mtime_ns, st.st_size)
            if st.st_mtime == self._last_sel_mtime and sel_sig == getattr(self, "_last_sel_sig", None):
                return False
            self._last_sel_mtime = st.st_mtime
            self._last_sel_sig = sel_sig
            sel = self._read_active_selection()
            if not sel:
                return False
            if sel.get("empty") or not sel.get("id"):
                if self._allow_tmux_side_effects() and os.environ.get("TMUX") and getattr(self, "_active_hubpool_win", None) != "reader":
                    self._active_hubpool_win = "reader"
                if self.selected_idx != -1:
                    self.selected_idx = -1
                    self._nav_record("__home__")
                    self._sync_window_title(None)
                    self._dirty = True
                    return True
                return False
            target_id = str(sel.get("id") or "")
            target_tool = str(sel.get("tool") or "")
            if target_id.startswith("new-") and target_tool:
                # Relayed windows keep their own name (`w-<tool>-<src id12>`), carried as `hubpoolWindow`
                wname = str(sel.get("hubpoolWindow") or f"w-{target_tool}-{target_id}")
                if wname not in _NEW_HUBPOOL_SESSIONS:
                    adopted = _make_new_hubpool_session_dict(target_tool, target_id, wname)
                    if sel.get("hubpoolWindow") and sel.get("title"):
                        adopted["title"] = str(sel["title"])
                    _NEW_HUBPOOL_SESSIONS[wname] = adopted
                    _pool._record_hubpool_window(wname, present=True)
            for idx, s in enumerate(self.sessions):
                if s["id"] == target_id and (not target_tool or s["tool"] == target_tool):
                    if self.selected_idx != idx:
                        self.selected_idx = idx
                        self._nav_record(s["id"])
                        self._ensure_selected_visible()
                        self._sync_window_title(s)
                        self._dirty = True
                        return True
                    return False
            if target_id.startswith("new-"):
                self.refresh_data(force=True)
                for idx, s in enumerate(self.sessions):
                    if s["id"] == target_id and (not target_tool or s["tool"] == target_tool):
                        self.selected_idx = idx
                        self._nav_record(s["id"])
                        self._ensure_selected_visible()
                        self._sync_window_title(s)
                        self._dirty = True
                        return True
        except OSError:
            pass
        return False
