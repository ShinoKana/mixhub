#!/usr/bin/env python3
"""`mixhub.tui.hubtui.navigation`: Back / forward history over Home and sessions, persisted for the sidebar."""
from __future__ import annotations

import json
import os


class NavigationMixin:
    """Browser-style back / forward navigation between Home and sessions."""

    NAV_BACK_X = 22
    NAV_BACK_HIT = (21, 23)
    NAV_FWD_X = 25
    NAV_FWD_HIT = (24, 26)

    def _nav_history_persist_path(self) -> str | None:
        if not getattr(self, "sidebar_only", False):
            return None
        return os.path.join(os.path.dirname(self.seen_path), "nav_history.json")

    def _load_persisted_nav_history(self, current_target: str) -> None:
        path = self._nav_history_persist_path()
        if not path or not os.path.isfile(path):
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            hist = data.get("history")
            pos = data.get("pos")
            if isinstance(hist, list) and hist and isinstance(pos, int):
                self.nav_history = [str(x) for x in hist if x][-50:]
                self.nav_pos = max(0, min(pos, len(self.nav_history) - 1))
                self._prune_nav_history()
                if current_target and (not self.nav_history or self.nav_history[self.nav_pos] != current_target):
                    self._nav_record(current_target)
        except Exception:
            pass

    def _save_persisted_nav_history(self) -> None:
        path = self._nav_history_persist_path()
        if not path:
            return
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            tmp = f"{path}.tmp.{os.getpid()}"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"history": self.nav_history[-50:], "pos": self.nav_pos}, f, ensure_ascii=False)
            os.replace(tmp, path)
        except OSError:
            pass

    def _prune_nav_history(self) -> None:
        valid_ids = {"__home__"} | {str(s.get("id")) for s in self.sessions if s.get("id")}
        if not hasattr(self, "nav_history") or not self.nav_history:
            cur = self.current_session()
            self.nav_history = [cur["id"] if cur else "__home__"]
            self.nav_pos = 0
            return
        new_hist: list[str] = []
        new_pos = 0
        for i, tid in enumerate(self.nav_history):
            if tid not in valid_ids:
                continue
            if new_hist and new_hist[-1] == tid:
                if i <= self.nav_pos:
                    new_pos = len(new_hist) - 1
                continue
            new_hist.append(tid)
            if i <= self.nav_pos:
                new_pos = len(new_hist) - 1
        if not new_hist:
            cur = self.current_session()
            new_hist = [cur["id"] if cur else "__home__"]
            new_pos = 0
        self.nav_history = new_hist[-50:]
        self.nav_pos = max(0, min(new_pos, len(self.nav_history) - 1))

    def can_nav_back(self) -> bool:
        self._prune_nav_history()
        return self.nav_pos > 0

    def can_nav_forward(self) -> bool:
        self._prune_nav_history()
        return self.nav_pos + 1 < len(self.nav_history)

    def _nav_record(self, target_id: str, from_keyboard: bool = False) -> None:
        """Record navigation to `target_id` ('__home__' or session id) and enforce Home-only wordmark rule."""
        if target_id != "__home__" or self.selected_idx != -1:
            if self.wordmark.mode != "off":
                self.wordmark.set_mode("off")
        self._prune_nav_history()
        if from_keyboard and getattr(self, "_last_nav_was_key", False) and len(self.nav_history) > 1 and self.nav_pos == len(self.nav_history) - 1:
            if self.nav_history[self.nav_pos - 1] == target_id:
                self.nav_history.pop()
                self.nav_pos = len(self.nav_history) - 1
            else:
                self.nav_history[self.nav_pos] = target_id
            return
        self._last_nav_was_key = bool(from_keyboard)
        if self.nav_pos < len(self.nav_history) - 1:
            self.nav_history = self.nav_history[: self.nav_pos + 1]
        if not self.nav_history or self.nav_history[-1] != target_id:
            self.nav_history.append(target_id)
            if len(self.nav_history) > 50:
                self.nav_history = self.nav_history[-50:]
        self.nav_pos = len(self.nav_history) - 1
        self._save_persisted_nav_history()

    def _apply_nav_target(self, target_id: str) -> bool:
        """Jump to `target_id` ('__home__' or session id) without pushing a new history entry."""
        self._last_nav_was_key = False
        if self.wordmark.mode != "off":
            self.wordmark.set_mode("off")
        if target_id == "__home__":
            self.selected_idx = -1
            self.loaded_key = None
            self._on_sidebar_selection_changed(activate_live=False)
            self._dirty = True
            return True
        for idx, s in enumerate(self.sessions):
            if s.get("id") == target_id:
                self.selected_idx = idx
                self._ensure_selected_visible()
                self.tail_step_idx = 0
                self.msg_scroll = 999999
                self.status_msg = ""
                if self.sidebar_only:
                    self._on_sidebar_selection_changed(activate_live=False)
                else:
                    self._write_active_selection(s)
                self._mark_session_seen(s)
                self._dirty = True
                return True
        return False

    def nav_back(self) -> bool:
        if not self.can_nav_back():
            return False
        self.nav_pos -= 1
        self._save_persisted_nav_history()
        return self._apply_nav_target(self.nav_history[self.nav_pos])

    def nav_forward(self) -> bool:
        if not self.can_nav_forward():
            return False
        self.nav_pos += 1
        self._save_persisted_nav_history()
        return self._apply_nav_target(self.nav_history[self.nav_pos])
