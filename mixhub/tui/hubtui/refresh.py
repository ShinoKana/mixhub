#!/usr/bin/env python3
"""`mixhub.tui.hubtui.refresh`: Session discovery merge: pulling `server.Hub` sessions, live windows and new-session rows into the list."""
from __future__ import annotations

import os
import subprocess
import time

from mixhub.tmux import new_sessions as _new_sessions
from mixhub.tmux import pool as _pool
from mixhub.tmux.commands import _header_cmd, hubpool_win_name
from mixhub.tmux.new_sessions import _NEW_HUBPOOL_SESSIONS
from mixhub.tmux.pool import follow_opencode_tab
from mixhub.tmux.session_state import _record_promoted_new_session, _update_working_set_file
from mixhub.tui import diag as _diag
from mixhub.tui import paths as _paths
from mixhub.tui.hubtui.sidebar import _QUERY_TOOL_TAG_RE


class RefreshMixin:
    """Rebuilds the session list from discovery, the working set and live `hubpool` windows."""

    @staticmethod
    def _session_bucket(s: dict) -> int:
        return (
            0
            if s.get("pinned")
            else (
                1
                if (
                    s.get("isLive")
                    or s.get("inWorkingSet")
                    or s.get("agentState") in ("working", "wait_input", "interrupted")
                )
                else 2
            )
        )

    def _diag_mode_label(self) -> str:
        if getattr(self, "header_only", False):
            return "header"
        if self.reader_only:
            return "reader"
        if self.sidebar_only:
            return "sidebar"
        return "full"

    def refresh_data(self, force: bool = False, fast_only: bool = False, reorder: bool = False):
        _t0_refresh = time.monotonic()
        cur_s = self.current_session()
        keep_id = cur_s["id"] if cur_s else None
        prev_placeholder = dict(cur_s) if (cur_s and cur_s.get("_placeholder")) else None
        _, _, list_h = self._list_bounds(self.stdscr.getmaxyx()[0])
        prev_vis_idx = self._visual_row_index(keep_id) if keep_id is not None else -1
        prev_offset = (prev_vis_idx if prev_vis_idx >= 0 else self.selected_idx) - self.list_scroll
        was_visible = keep_id is not None and 0 <= prev_offset < list_h
        filter_key = (self.tool_filter, self.query, self.show_temp)
        prev_filter_key = getattr(self, "_last_filter_key", None)
        filter_changed = prev_filter_key is not None and filter_key != prev_filter_key
        if force:
            self.hub.scanned = 0
        elif fast_only and len(self.hub.sessions) > 0:
            self.hub.scanned = time.monotonic()
        is_legacy_single_mock = (len(self.sessions) == 1 and self.sessions[0].get("id") == "row")
        # `--sidebar` lists everything unless the `mixhub ▾` provider filter is active (`deepseek` has no server-side tool)
        sb_provider_filtered = self.sidebar_only and not is_legacy_single_mock and self.provider_filter is not None
        if self.sidebar_only and not is_legacy_single_mock:
            eff_tool = "all" if (not sb_provider_filtered or self.tool_filter in ("all", "deepseek")) else self.tool_filter
        else:
            eff_tool = self.tool_filter
        eff_q = self.query or ""
        if eff_q:
            m_tag = _QUERY_TOOL_TAG_RE.search(eff_q)
            if m_tag:
                tag = m_tag.group(1).lower()
                eff_tool = "agy" if tag == "gemini" else tag
                eff_q = (eff_q[:m_tag.start()] + eff_q[m_tag.end():]).strip()
        q = {"tool": [eff_tool], "q": [eff_q], "show_temp": ["1" if self.show_temp else "0"]}
        data = self.hub.listing(q)
        fresh = [dict(item) for item in data["sessions"]]
        live_new_list = [
            ns for ns in _new_sessions._collect_live_new_hubpool_sessions(
                allow_probe=(force and not fast_only and self._allow_tmux_side_effects())
            )
            if eff_tool in ("all", ns.get("tool"))
            and (not eff_q or eff_q.lower() in str(ns.get("title", "")).lower() or eff_q.lower() in str(ns.get("id", "")).lower())
        ]
        if sb_provider_filtered:
            # The open session stays in `self.sessions` (hidden by `_sidebar_display_rows`) so filtering the list
            # never silently moves the selection away from what the right pane shows.
            fresh = [item for item in fresh if self._matches_provider_filter(item) or item.get("id") == keep_id]
            live_new_list = [ns for ns in live_new_list if self._matches_provider_filter(ns) or ns.get("id") == keep_id]
        promoted_map: dict[tuple[str, str], dict] = {}
        if live_new_list:
            remaining_new = []
            for ns in live_new_list:
                ns_tool = ns.get("tool")
                ns_ts = float(ns.get("updatedAt") or 0.0)
                new_wname = str(ns.get("hubpoolWindow") or f"w-{ns_tool}-{ns['id']}")
                candidates = [
                    item for item in fresh
                    if item.get("tool") == ns_tool
                    and not str(item.get("id", "")).startswith("new-")
                    and item.get("isLive")
                    and int(item.get("messageCount") or item.get("stepCount") or 0) <= 2
                    and float(item.get("updatedAt") or 0.0) >= ns_ts - 0.5
                    and any(p.get("resident") for p in (item.get("processes") or []))
                    and _pool.find_hubpool_window_for_session(item["id"], allow_probe=False) is None
                ]
                if candidates:
                    candidates.sort(key=lambda x: float(x.get("updatedAt") or 0.0), reverse=True)
                    real_s = candidates[0]
                    target_wname = hubpool_win_name(real_s)
                    env = dict(os.environ)
                    env.pop("TMUX", None)
                    try:
                        subprocess.run(
                            ["tmux", "-L", "hubpool", "rename-window", "-t", f"pool:{new_wname}", target_wname],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            env=env,
                        )
                        hdr_cmd = _header_cmd(str(real_s["tool"]), str(real_s["id"]))
                        subprocess.run(
                            ["tmux", "-L", "hubpool", "respawn-pane", "-k", "-t", f"pool:{target_wname}.{{top}}", hdr_cmd],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            env=env,
                        )
                    except Exception:
                        pass
                    _NEW_HUBPOOL_SESSIONS.pop(new_wname, None)
                    _pool._record_hubpool_window(new_wname, present=False)
                    _pool._record_hubpool_window(target_wname, present=True)
                    _record_promoted_new_session(
                        str(ns_tool),
                        str(ns["id"]),
                        str(real_s["tool"]),
                        str(real_s["id"]),
                        target_wname,
                    )
                    wset_target = getattr(self.hub, "working_set_path", None)
                    if wset_target:
                        _update_working_set_file(
                            str(real_s["tool"]),
                            str(real_s["id"]),
                            wset_target,
                            keep=True,
                        )
                    real_s["resident"] = True
                    promoted_map[(str(ns_tool), str(ns["id"]))] = real_s
                    if keep_id == ns["id"]:
                        keep_id = real_s["id"]
                        self._write_active_selection(real_s)
                    if getattr(self, "_active_hubpool_win", None) == new_wname:
                        self._active_hubpool_win = target_wname
                else:
                    remaining_new.append(ns)
            live_new_list = remaining_new
        if self.sidebar_only and self._allow_tmux_side_effects():
            for item in fresh:
                old_wname = str(item.get("hubpoolWindow") or "")
                new_wname = item.get("isLive") and follow_opencode_tab(item)
                if not new_wname:
                    continue
                item.pop("hubpoolWindow", None)
                _pool._record_hubpool_window(old_wname, present=False)
                _pool._record_hubpool_window(new_wname, present=True)
                if getattr(self, "_active_hubpool_win", None) == old_wname:
                    # that window is on screen: the row to highlight is now the tab it shows
                    self._active_hubpool_win = new_wname
                    keep_id = item["id"]
                    self._write_active_selection(item)
        if live_new_list:
            existing_ids = {item["id"] for item in fresh}
            to_insert = [ns for ns in live_new_list if ns["id"] not in existing_ids]
            if to_insert:
                pin_end = 0
                while pin_end < len(fresh) and fresh[pin_end].get("pinned"):
                    pin_end += 1
                fresh[pin_end:pin_end] = to_insert
        if reorder or not self.sessions or filter_changed:
            self.sessions = fresh
        else:
            # Stable merge: update retained rows, remove vanished rows, append new ones.
            by_id = {(item["tool"], item["id"]): item for item in fresh}
            stable = []
            for item in self.sessions:
                key = (item["tool"], item["id"])
                if key in promoted_map:
                    real_target = promoted_map[key]
                    updated = by_id.pop((real_target["tool"], real_target["id"]), real_target)
                else:
                    updated = by_id.pop(key, None)
                if updated is not None:
                    item.clear()
                    item.update(updated)
                    stable.append(item)
            for rem in by_id.values():
                if str(rem.get("id", "")).startswith("new-"):
                    pin_idx = sum(1 for x in stable if x.get("pinned"))
                    stable.insert(pin_idx, rem)
                else:
                    stable.append(rem)
            self.sessions = stable
        self._last_filter_key = filter_key
        self.counts = data["counts"]
        self._last_refreshed_live_mem = self.counts.get("liveMemoryMB", 0.0)
        if not getattr(self, "header_only", False) and not getattr(self, "reader_only", False):
            self.caf.sync(self.counts.get("working", 0))
        # Level-triggered unread attention tracking: when a session transitions `working -> done_idle/wait_input`
        # or its `updatedAt` advances while idle, record `attn` only for completed output or a structured wait.
        attn_dirty = False
        bell_count = 0
        outer_focused_val: bool | None = None
        now_wall = time.time()
        prev_map = getattr(self, "_prev_agent_states", None)
        if prev_map is None:
            prev_map = {}
            self._prev_agent_states = prev_map
        notified_bells = getattr(self, "_notified_bell_turns", None)
        if notified_bells is None:
            notified_bells = {}
            self._notified_bell_turns = notified_bells
        for item in (self.sessions if self._owns_seen_attn else ()):
            skey = self._session_key(item)
            cur_st = item.get("agentState") or ("working" if item.get("isLive") else "dormant")
            cur_ts = float(item.get("updatedAt") or 0.0)
            prev_entry = prev_map.get(skey)
            is_new = str(item.get("id", "")).startswith("new-")
            no_reply = item.get("hasAssistantReply") is False
            suppress_attn = (
                cur_st == "working" or is_new or item.get("synthetic")
                or (no_reply and cur_st != "wait_input")
                or (item.get("tailStatus") == "ball_in_model" and not item.get("interrupted") and cur_st != "wait_input")
            )
            is_selected = bool(keep_id and item.get("id") == keep_id)
            if cur_st == "working" or is_new or item.get("synthetic") or no_reply:
                if self._seen_state["attn"].pop(skey, None) is not None:
                    attn_dirty = True
            elif (
                cur_st == "dormant"
                and not item.get("isLive")
                and not item.get("inWorkingSet")
                and os.path.isfile(os.path.join(_paths.LAST_EXIT_DIR, f"{item.get('id', '')}.json"))
            ):
                if self._seen_state["attn"].pop(skey, None) is not None:
                    attn_dirty = True
                prev_seen_v = float(self._seen_state["seen"].get(skey, 0.0))
                if cur_ts > prev_seen_v:
                    self._seen_state["seen"][skey] = max(now_wall, cur_ts)
                    attn_dirty = True
            if not suppress_attn and prev_entry is not None:
                prev_st, prev_ts = prev_entry
                if (prev_st == "working" and cur_st in ("done_idle", "wait_input")) or (
                    cur_st in ("done_idle", "wait_input") and cur_ts > prev_ts + 1.0
                ):
                    should_ring = False
                    if is_selected:
                        if outer_focused_val is None:
                            outer_focused_val = self._is_outer_client_focused()
                        if outer_focused_val:
                            self._seen_state["seen"][skey] = max(
                                now_wall, cur_ts, float(self._seen_state["seen"].get(skey, 0.0))
                            )
                            self._seen_state["attn"].pop(skey, None)
                            attn_dirty = True
                        else:
                            self._seen_state["attn"][skey] = max(now_wall, cur_ts)
                            attn_dirty = True
                            should_ring = True
                    else:
                        self._seen_state["attn"][skey] = max(now_wall, cur_ts)
                        attn_dirty = True
                        should_ring = True
                    if should_ring:
                        turn_sig = round(max(cur_ts, float(item.get("attentionAt") or 0.0), 1.0), 3)
                        if notified_bells.get(skey) != turn_sig:
                            notified_bells[skey] = turn_sig
                            bell_count += 1
            if is_selected and cur_st == "done_idle" and skey in self._seen_state["attn"]:
                if outer_focused_val is None:
                    outer_focused_val = self._is_outer_client_focused()
                if outer_focused_val:
                    self._seen_state["attn"].pop(skey, None)
                    self._seen_state["seen"][skey] = max(
                        now_wall, cur_ts, float(self._seen_state["seen"].get(skey, 0.0))
                    )
                    attn_dirty = True
            if cur_st == "working" and (prev_entry is None or prev_entry[0] != "working"):
                # Evidence for "finished session still spinning": which signal made this row `working`
                _diag.log_tui_diag(
                    "became_working",
                    session_id=str(item.get("id", "")),
                    tool=str(item.get("tool", "")),
                    source=str(item.get("stateSource") or ("transcript" if item.get("agentState") else "live_fallback")),
                    tail=str(item.get("tailStatus") or ""),
                    probe=str(item.get("probeState") or ""),
                    idle_s=round(max(0.0, now_wall - cur_ts), 1) if cur_ts else None,
                    subagents=int(item.get("activeSubagents") or 0),
                )
            prev_map[skey] = (cur_st, cur_ts)
        if attn_dirty:
            self._save_seen_state()
        if bell_count > 0:
            self._emit_completion_bell(bell_count)
        if keep_id:
            found = False
            for idx, item in enumerate(self.sessions):
                if item["id"] == keep_id:
                    self.selected_idx = idx
                    found = True
                    break
            if not found:
                if filter_changed:
                    self.selected_idx = 0 if self.sessions else -1
                elif self.reader_only and prev_placeholder and prev_placeholder.get("id") == keep_id:
                    self.sessions.insert(0, prev_placeholder)
                    self.selected_idx = 0
                elif self.selected_idx >= len(self.sessions):
                    self.selected_idx = max(-1, len(self.sessions) - 1)
        elif filter_changed and self.sessions:
            self.selected_idx = 0
        elif self.selected_idx >= len(self.sessions):
            self.selected_idx = max(-1, len(self.sessions) - 1)
        if was_visible and not filter_changed and self.current_session() and self.current_session()["id"] == keep_id:
            new_vis_idx = self._visual_row_index(keep_id)
            new_idx = new_vis_idx if new_vis_idx >= 0 else self.selected_idx
            if self.list_scroll == 0 and new_idx < list_h:
                # Viewed from the top (Pinned on screen): keep the list still and let the selection move instead
                pass
            else:
                self.list_scroll = max(0, new_idx - prev_offset)
        try:
            drows_after = self._sidebar_display_rows()
            total_rows = len(drows_after) if drows_after else len(self.sessions)
        except Exception:
            total_rows = len(self.sessions)
        self.list_scroll = max(0, min(self.list_scroll, max(0, total_rows - list_h)))
        self.loaded_key = None
        self._dirty = True
        self._sessions_snapshot_ts = time.time()
        self._publish_attention()
        _refresh_elapsed = time.monotonic() - _t0_refresh
        if _refresh_elapsed > 0.200:
            _cur_after = self.current_session()
            _diag.log_tui_diag(
                "slow_refresh",
                elapsed_ms=round(_refresh_elapsed * 1000.0, 1),
                mode=self._diag_mode_label(),
                session_id=(str(_cur_after.get("id", "")) if _cur_after else ""),
                force=bool(force),
                fast_only=bool(fast_only),
            )

    def _visual_row_index(self, session_id) -> int:
        """Visual row index of `session_id` in `_sidebar_display_rows()` (section headers included), or -1.

        Only the real sidebar pane (`sidebar_only`) anchors by visual rows; other modes keep the legacy
        session-index anchor (returns -1 so callers fall back to `selected_idx`).
        """
        if session_id is None or not getattr(self, "sidebar_only", False):
            return -1
        try:
            for r_i, r in enumerate(self._sidebar_display_rows()):
                if r.get("kind") == "session" and r.get("session", {}).get("id") == session_id:
                    return r_i
        except Exception:
            pass
        return -1

    def _schedule_post_resize_client_refresh(self) -> None:
        """Outer `window-resized` / `client-resized` hooks already refresh `main-vertical` natively; keep main thread 0-subprocess."""
        return
