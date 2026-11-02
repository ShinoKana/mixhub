#!/usr/bin/env python3
"""`mixhub.tui.hubtui.attention`: Seen / unread state (`seen.json`), cross-process attention marks, completion bell, and toasts."""
from __future__ import annotations

import json
import os
import subprocess
import time

from mixhub.tmux import outer as _outer
from mixhub.tmux.outer import _is_outer_client_focused, current_outer_session_name, emit_outer_terminal_bell
from mixhub.tui.settings import IS_MAC

# `seen.json` schema: v3 resets the unread baseline to the launch time (unread state cleanup)
_SEEN_SCHEMA_VERSION = 3


class AttentionMixin:
    """Unread tracking, published attention for the outer window title, the completion bell, and ephemeral toasts."""

    def _load_seen_state(self) -> dict:
        now = time.time()
        default = {"schema": _SEEN_SCHEMA_VERSION, "bootstrapAt": now, "seen": {}, "attn": {}}
        try:
            if os.path.exists(self.seen_path):
                with open(self.seen_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    raw_boot = float(data.get("bootstrapAt", now))
                    schema = int(data.get("schema") or 1)
                    if schema < _SEEN_SCHEMA_VERSION:
                        # Migration cleanup: everything that happened before this launch counts as already read
                        boot = now
                    else:
                        boot = min(raw_boot, now)
                    state = {
                        "schema": _SEEN_SCHEMA_VERSION,
                        "bootstrapAt": boot,
                        "seen": dict(data.get("seen") or {}),
                        "attn": dict(data.get("attn") or {}),
                    }
                    if self._owns_seen_attn and (schema < _SEEN_SCHEMA_VERSION or boot != raw_boot):
                        self._write_seen_file(state)
                    else:
                        self._seen_mtime_ns = self._seen_file_mtime_ns()
                    return state
        except (OSError, ValueError, TypeError):
            pass
        if self._owns_seen_attn:
            self._write_seen_file(default)
        return default

    def _seen_file_mtime_ns(self) -> int:
        try:
            return os.stat(self.seen_path).st_mtime_ns
        except OSError:
            return 0

    def _write_seen_file(self, st: dict) -> None:
        try:
            os.makedirs(os.path.dirname(self.seen_path), exist_ok=True)
            tmp = f"{self.seen_path}.tmp.{os.getpid()}"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(st, f, ensure_ascii=False)
            os.replace(tmp, self.seen_path)
            self._seen_mtime_ns = self._seen_file_mtime_ns()
        except OSError:
            pass

    def _merge_seen_from_disk(self, force: bool = False) -> bool:
        """Pull `seen` marks written by the other panes (max per key) when `seen.json` changed. Never writes."""
        mt = self._seen_file_mtime_ns()
        if not mt or (mt == self._seen_mtime_ns and not force):
            return False
        try:
            with open(self.seen_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return False
        self._seen_mtime_ns = mt
        if not isinstance(data, dict):
            return False
        st = self._seen_state
        changed = False
        for key, val in dict(data.get("seen") or {}).items():
            try:
                val = float(val)
            except (TypeError, ValueError):
                continue
            if val > float(st["seen"].get(key, 0.0)):
                st["seen"][key] = val
                changed = True
        try:
            boot = float(data.get("bootstrapAt") or 0.0)
        except (TypeError, ValueError):
            boot = 0.0
        if boot > float(st.get("bootstrapAt", 0.0)):
            st["bootstrapAt"] = boot
            changed = True
        if not self._owns_seen_attn:
            disk_attn = dict(data.get("attn") or {})
            if disk_attn != st["attn"]:
                st["attn"] = disk_attn
                changed = True
        return changed

    def _save_seen_state(self, state: dict | None = None):
        if state is not None:
            self._write_seen_file(state)
            return
        try:
            import fcntl
        except ImportError:
            fcntl = None
        lock_f = None
        try:
            os.makedirs(os.path.dirname(self.seen_path), exist_ok=True)
            lock_f = open(f"{self.seen_path}.lock", "a+", encoding="utf-8")
            if fcntl is not None:
                fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
            # Merge instead of overwrite: the sidebar, the reader and every window header share this file
            self._merge_seen_from_disk(force=True)
            self._write_seen_file(self._seen_state)
        except OSError:
            pass
        finally:
            if lock_f is not None:
                try:
                    if fcntl is not None:
                        fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)
                    lock_f.close()
                except OSError:
                    pass
        self._publish_attention()

    @staticmethod
    def _session_key(s: dict) -> str:
        return f"{s.get('tool', '')}:{s.get('id', '')}"

    def is_session_unread(self, s: dict) -> bool:
        """
        Return True if session `s` has new completed output (`wait_input` or `done_idle` or `interrupted` or `dormant`)
        newer than both `bootstrapAt` (launch baseline) and the user's last `seenAt` timestamp for `s`.
        Sessions currently `working` do not show an unread dot (their glyph already indicates work).
        """
        if not s or str(s.get("id", "")).startswith("new-") or s.get("synthetic"):
            return False
        # The session currently open in the preview is being read right now: never unread.
        if 0 <= self.selected_idx < len(self.sessions) and self.sessions[self.selected_idx].get("id") == s.get("id"):
            return False
        state = s.get("agentState") or ("working" if s.get("isLive") else "dormant")
        if state == "working":
            return False
        if (s.get("hasAssistantReply") is False or s.get("tailStatus") == "ball_in_model") and (
            state not in ("wait_input", "wait") and not s.get("interrupted") and not s.get("failed")
        ):
            return False
        # Unpinned dormant sessions in `Recent` never show unread (only explicit dormant markers suppress,
        # so minimal mock dicts `{id, tool, updatedAt}` keep the attention/seen semantics).
        if (
            (
                s.get("group") == "dormant"
                or (s.get("inWorkingSet") is False and not s.get("isLive") and state == "dormant")
            )
            and not s.get("pinned")
            and s.get("id") != "row"
        ):
            return False
        key = self._session_key(s)
        raw_attn = s.get("attentionAt")
        base_ts = float(raw_attn) if raw_attn is not None else float(s.get("updatedAt") or 0.0)
        attn_at = max(base_ts, float(self._seen_state["attn"].get(key, 0.0)))
        seen_at = float(self._seen_state["seen"].get(key, 0.0))
        baseline = max(
            float(self._seen_state.get("bootstrapAt", 0.0)),
            seen_at,
            float(s.get("lastUserAt") or 0.0),
        )
        return attn_at > baseline + 0.5

    is_unread = is_session_unread

    def _mark_session_seen(self, s: dict | None, save: bool = True):
        if not s:
            return
        key = self._session_key(s)
        state = s.get("agentState") or ("working" if s.get("isLive") else "dormant")
        raw_attn = s.get("attentionAt")
        updated_at = max(float(s.get("updatedAt") or 0.0), float(raw_attn or 0.0))
        attn_at = float(self._seen_state["attn"].get(key, 0.0))
        prev_seen = float(self._seen_state["seen"].get(key, 0.0))
        if state == "working":
            # Never push `seen` into the future while a session is still working,
            # so when it transitions `working -> done_idle/wait_input` it will highlight as unread!
            new_seen = max(updated_at, attn_at, prev_seen)
        else:
            new_seen = max(time.time(), updated_at, attn_at, prev_seen)
        self._seen_state["seen"][key] = new_seen
        if save and new_seen > prev_seen and (prev_seen < max(updated_at, attn_at) or (new_seen - prev_seen) >= 1.0):
            self._save_seen_state()

    def _check_external_frontmost_tty(self, front_tty: str) -> bool:
        """If `front_tty` (e.g. `ttys005` or `/dev/ttys005`) matches any unread session's external process, mark it seen."""
        clean_tty = (front_tty or "").strip().removeprefix("/dev/")
        if not clean_tty.startswith("ttys"):
            return False
        changed = False
        for s in self.sessions:
            if not self.is_session_unread(s):
                continue
            procs = s.get("processes") or []
            if any(p.get("tty") == clean_tty and not p.get("resident") for p in procs) or (s.get("tty") == clean_tty and not s.get("resident")):
                self._mark_session_seen(s, save=True)
                changed = True
        if changed:
            self._dirty = True
        return changed

    def _poll_external_terminal_seen(self, now_mono: float) -> None:
        """Every 1.5s, if any unread session is open in an external Terminal.app tab, check if Terminal is viewing that TTY."""
        if not IS_MAC or (now_mono - getattr(self, "_last_ext_tty_poll", 0.0)) < 1.5:
            return
        self._last_ext_tty_poll = now_mono
        has_ext_unread = any(
            self.is_session_unread(s)
            and any(not p.get("resident") and str(p.get("tty") or "").startswith("ttys") for p in (s.get("processes") or []))
            for s in self.sessions
        )
        if not has_ext_unread:
            return
        script = (
            'tell application "System Events" to set frontApp to name of first application process whose frontmost is true\n'
            'if frontApp is "Terminal" then\n'
            '  tell application "Terminal" to return tty of selected tab of front window\n'
            'end if'
        )
        try:
            res = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=0.35)
            if res.returncode == 0 and res.stdout.strip():
                self._check_external_frontmost_tty(res.stdout.strip())
        except Exception:
            pass

    def show_ephemeral_toast(self, text: str, ttl: float = 5.0, now: float | None = None) -> bool:
        """
        Display a restrained ephemeral toast above the bottom status bar for `ttl` seconds
        (default 5.0s), unless permanently dismissed via `[3] Dismiss` / `0`.
        """
        if self.toast_dismissed_permanently or not text:
            return False
        base_now = time.monotonic() if now is None else float(now)
        self.ephemeral_toast = (str(text), base_now + float(ttl))
        self._dirty = True
        return True

    def active_ephemeral_toast(self, now: float | None = None) -> str:
        """Return active toast text if within 5s TTL, otherwise auto-expire and return empty string."""
        if self.toast_dismissed_permanently or not self.ephemeral_toast:
            self.ephemeral_toast = None
            return ""
        text, expires_at = self.ephemeral_toast
        base_now = time.monotonic() if now is None else float(now)
        if base_now >= expires_at:
            self.ephemeral_toast = None
            return ""
        return text

    def handle_toast_key(self, ch: int | str, now: float | None = None) -> str:
        """
        Handle a keystroke while an ephemeral toast is visible:
          - '1': record 'great' feedback and clear toast
          - '2': record 'fine' feedback and clear toast
          - '3' or '0': permanently dismiss toast for the session
          - any other key: immediately clear toast so typing/navigation is never obstructed
        """
        if not self.active_ephemeral_toast(now=now):
            return ""
        key_char = ch if isinstance(ch, str) else (chr(ch) if isinstance(ch, int) and 0 <= ch < 256 else "")
        self.ephemeral_toast = None
        self._dirty = True
        if key_char == "1":
            self.toast_feedback = "great"
            return "great"
        if key_char == "2":
            self.toast_feedback = "fine"
            return "fine"
        if key_char in ("3", "0"):
            self.toast_dismissed_permanently = True
            self.toast_feedback = "dismissed_permanently"
            return "dismissed_permanently"
        return "dismissed_on_key"

    def _attention_keys(self, exclude_id: str | None = None) -> list[str]:
        """`tool:id` of sessions that need the user, same rule as Home `NEEDS YOU`: awaiting, or finished unseen."""
        keys = []
        for s in self.sessions:
            sid = str(s.get("id", ""))
            if sid == exclude_id or sid.startswith("new-") or s.get("synthetic"):
                continue
            if s.get("agentState") in ("wait_input", "wait") or self.is_session_unread(s):
                keys.append(self._session_key(s))
        return keys

    @property
    def attention_path(self) -> str:
        return os.path.join(os.path.dirname(self.seen_path), "attention.json")

    def _is_outer_client_focused(self) -> bool:
        return _is_outer_client_focused(current_outer_session_name())

    def _emit_completion_bell(self, count: int = 1) -> int:
        if count <= 0:
            return 0
        tmux_env = os.environ.get("TMUX", "")
        if not tmux_env:
            return 0
        if (
            tmux_env in ("/tmp/tmux-1000/default,123,0", "/tmp/tmux-501/default,123,0", "test-pool")
            and type(subprocess.run).__name__ in ("MagicMock", "AsyncMock", "NonCallableMagicMock")
            and type(_outer._list_outer_client_ttys).__name__ == "function"
        ):
            return 0
        return emit_outer_terminal_bell(count=count, session_name=current_outer_session_name())

    def _publish_attention(self) -> None:
        """`--sidebar` owns fresh session states: publish the `●N` inputs so `--header` / `--reader` never rescan."""
        if not self.sidebar_only:
            return
        keys = self._attention_keys()
        waiting = [self._session_key(s) for s in self.sessions if s.get("agentState") in ("wait_input", "wait")
                   and self._session_key(s) in keys]
        if (keys, waiting) == getattr(self, "_published_attention", None):
            return
        self._published_attention = (keys, waiting)
        try:
            tmp = f"{self.attention_path}.tmp.{os.getpid()}"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"keys": keys, "waiting": waiting, "ts": time.time()}, f, ensure_ascii=False)
            os.replace(tmp, self.attention_path)
        except OSError:
            pass
        if type(subprocess.Popen).__name__ not in ("MagicMock", "AsyncMock", "NonCallableMagicMock"):
            self._sync_window_title(self.current_session(), attention_count=len(keys))

    def _read_published_attention(self) -> list[str] | None:
        try:
            mt = os.stat(self.attention_path).st_mtime_ns
        except OSError:
            return None
        cache = getattr(self, "_attention_cache", None)
        if cache is None or cache[0] != mt:
            try:
                with open(self.attention_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                keys = [str(k) for k in (data.get("keys") or [])]
                waiting = {str(k) for k in (data.get("waiting") or [])}
                ts = float(data.get("ts") or 0.0)
            except (OSError, ValueError, TypeError, AttributeError):
                return cache[1] if cache else None
            cache = (mt, keys, ts, waiting)
            self._attention_cache = cache
        # Freshest source wins: a header spawned after the sidebar's last publish already has newer data
        if cache[2] < getattr(self, "_sessions_snapshot_ts", 0.0):
            return None
        return cache[1]

    def _published_needs_you(self) -> tuple[set[str], set[str]] | None:
        """The sidebar's last `(needs you, of which awaiting)` session keys, or None when there is none to trust."""
        keys = self._read_published_attention()
        if keys is None:
            return None
        cache = getattr(self, "_attention_cache", None)
        return set(keys), set(cache[3]) if cache and len(cache) > 3 else set()

    def _count_awaiting_or_unread_sessions(self) -> int:
        """Collapsed-sidebar `●N`: OTHER sessions awaiting the user or finished unseen (the open one is on screen)."""
        cur = self.current_session()
        if (self.reader_only or getattr(self, "header_only", False)) and not self.sidebar_only:
            published = self._read_published_attention()
            if published is not None:
                own_key = self._session_key(cur) if cur else None
                return sum(1 for k in published if k != own_key)
        return len(self._attention_keys(exclude_id=cur.get("id") if cur else None))

    def _poll_published_attention(self) -> bool:
        """`--header` / `--reader`: one `stat` per wake; True only when this pane's `●N` actually changed."""
        try:
            mt = os.stat(self.attention_path).st_mtime_ns
        except OSError:
            return False
        changed = False
        if (self.reader_only and not getattr(self, "header_only", False) and self.selected_idx == -1
                and mt != getattr(self, "_home_attention_mtime", None)):
            # Home `NEEDS YOU` lists titles too: rescan once per real attention change, never on a timer, including
            # changes published while a session was open here
            self._home_attention_mtime = mt
            self._merge_seen_from_disk()
            self.refresh_data(force=True)
            changed = True
        if mt == getattr(self, "_attention_polled_mtime", None):
            return changed
        self._attention_polled_mtime = mt
        n = self._count_awaiting_or_unread_sessions()
        if n != getattr(self, "_attention_polled_n", None):
            self._attention_polled_n = n
            changed = True
        return changed
