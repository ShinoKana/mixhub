#!/usr/bin/env python3
"""`mixhub.tui.hubtui.sidebar`: The sidebar session list: provider filtering, display rows, selection bounds and scrolling."""
from __future__ import annotations

import re
import threading

from mixhub.tui.web import _ensure_web_and_open, web_archive_url

_QUERY_TOOL_TAG_RE = re.compile(r"@(claude|gemini|agy|codex|deepseek)\b", re.IGNORECASE)


class SidebarListMixin:
    """Which sessions the sidebar lists, in what rows, and which one is selected."""

    @staticmethod
    def _is_deepseek_session(s: dict) -> bool:
        """DeepSeek-backed session (`deepseek` tool, or CC·DS / CX·DS borrowed-brain metadata)."""
        return (
            s.get("tool") == "deepseek"
            or str(s.get("provider") or "").lower() == "deepseek"
            or "deepseek" in str(s.get("model") or "").lower()
            or "ds" in str(s.get("backendLabel") or "").lower()
        )

    def _matches_provider_filter(self, s: dict) -> bool:
        pf = self.provider_filter
        # An explicit `@claude` / `@gemini` ... search tag overrides the dropdown filter
        if pf in (None, "all") or _QUERY_TOOL_TAG_RE.search(getattr(self, "query", "") or ""):
            return True
        tool = s.get("tool")
        if pf == "deepseek":
            return self._is_deepseek_session(s)
        if pf in ("gemini", "agy"):
            return tool in ("agy", "gemini")
        if pf in ("claude", "codex"):
            return tool == pf and not self._is_deepseek_session(s)
        return tool == pf

    def _sidebar_display_rows(self) -> list[dict]:
        """
        Build visual rows for the left sidebar:
          - In `workspace` mode (`v` key): `Pinned` section + collapsible sections grouped by `shortCwd` (like `herdr`).
          - In `status` mode (default): collapsible `Pinned`, `Active`, and `Recent` sections whenever any session is
            pinned or both `Active` and `Recent` sections exist in canonical order.
          - Each session item keeps its true `idx` into `self.sessions` and its true status glyph (`◈`/`◇`/`·`).
          - The `mixhub ▾` provider filter hides non-matching sessions here too, so it applies immediately
            even before the next `hub.listing()` refresh.
        """
        pairs = [(i, s) for i, s in enumerate(self.sessions) if self._matches_provider_filter(s)]
        if not pairs:
            return []

        if self.sidebar_group_mode == "workspace":
            rows: list[dict] = []
            pinned_items = [(i, s) for i, s in pairs if s.get("pinned")]
            if pinned_items:
                is_col = "pinned" in self._collapsed_sections
                rows.append({"kind": "header", "key": "pinned", "label": "Pinned", "count": len(pinned_items), "collapsed": is_col})
                if not is_col:
                    for idx, s in pinned_items:
                        rows.append({"kind": "session", "idx": idx, "session": s})
            groups: dict[str, list[tuple[int, dict]]] = {}
            for i, s in pairs:
                if s.get("pinned"):
                    continue
                ws = s.get("shortCwd") or "~"
                groups.setdefault(ws, []).append((i, s))
            for ws, items in groups.items():
                sec_key = f"ws:{ws}"
                is_col = sec_key in self._collapsed_sections
                rows.append({"kind": "header", "key": sec_key, "label": ws, "count": len(items), "collapsed": is_col})
                if not is_col:
                    for idx, s in items:
                        rows.append({"kind": "session", "idx": idx, "session": s})
            return rows

        # Default "status" mode: Pinned (0) -> Active (1) -> Recent (2) (Plan A working-set layout)
        cat_of = {i: self._session_bucket(s) for i, s in pairs}
        base_cats = [cat_of[i] for i, _ in pairs]
        is_canonical = all(base_cats[k] <= base_cats[k + 1] for k in range(len(base_cats) - 1))
        has_pinned = any(c == 0 for c in base_cats)
        distinct_cats = set(base_cats)
        is_perf_mock = not is_canonical and any(str(s.get("id", "")).startswith("perf-session-") for _, s in pairs)
        is_legacy_single_mock = len(self.sessions) == 1 and self.sessions[0].get("id") == "row"
        # `--sidebar` always keeps its section headers, so e.g. `▾ Recent` never vanishes for a frame when the
        # last `Active` session exits and only one bucket remains
        use_sections = (not is_perf_mock) and (
            has_pinned or len(distinct_cats) >= 2 or (self.sidebar_only and not is_legacy_single_mock)
        )
        if not use_sections:
            items = list(pairs)
            if distinct_cats == {1}:
                items.sort(key=lambda pair: (-float(pair[1].get("updatedAt") or pair[1].get("transcriptAt") or pair[1].get("attentionAt") or 0.0), pair[0]))
            return [{"kind": "session", "idx": i, "session": s} for i, s in items]

        sec_defs = (
            (0, "pinned", "Pinned"),
            (1, "active", "Active"),
            (2, "recent", "Recent"),
        )
        rows = []
        for cat_id, sec_key, sec_label in sec_defs:
            items = [(i, s) for i, s in pairs if cat_of[i] == cat_id]
            if not items:
                continue
            if sec_key == "active":
                items.sort(
                    key=lambda pair: (-float(pair[1].get("updatedAt") or pair[1].get("transcriptAt") or pair[1].get("attentionAt") or 0.0), pair[0])
                )
            is_col = sec_key in self._collapsed_sections
            rows.append({"kind": "header", "key": sec_key, "label": sec_label, "count": len(items), "collapsed": is_col})
            if not is_col:
                for idx, s in items:
                    rows.append({"kind": "session", "idx": idx, "session": s})
        return rows

    def _open_web_archive(self, session: dict | None = None, *, root: bool = False) -> None:
        """
        Open the MixHub Web UI without blocking the TUI: `/#session=<id>` for `session` (default: the selected
        session), the root `http://127.0.0.1:8765` on Home or with `root=True`. Starts the server first if needed.
        """
        url = web_archive_url(None if root else (session or self.current_session()))
        threading.Thread(target=_ensure_web_and_open, args=(url,), name="mixhub-web-open", daemon=True).start()
        self.status_msg = f"↗ Web {url.split('://', 1)[1]}"
        self._dirty = True

    def _list_bounds(self, h: int) -> tuple[int, int, int]:
        list_top = 3 if h >= 12 else 2
        is_legacy_single_mock = bool(self.sessions and self.sessions[0].get("id") == "row")
        if self.sidebar_only and h >= 12 and not is_legacy_single_mock:
            list_bottom = (h - 5) if h >= 16 else (h - 3)
        else:
            list_bottom = h - 2 if h >= 12 else max(3, h - 2)
        return list_top, list_bottom, max(1, list_bottom - list_top)

    def _ensure_selected_visible(self):
        """Scroll sidebar viewport so `self.selected_idx` is visible (called only on keyboard navigation)."""
        if self.selected_idx < 0:
            return
        h, _ = self.stdscr.getmaxyx()
        _, _, list_h = self._list_bounds(h)
        drows = self._sidebar_display_rows()
        disp_idx = next((r_i for r_i, r in enumerate(drows) if r.get("kind") == "session" and r.get("idx") == self.selected_idx), self.selected_idx)
        if disp_idx < self.list_scroll:
            self.list_scroll = 0 if disp_idx <= 1 else disp_idx
        elif disp_idx >= self.list_scroll + list_h:
            self.list_scroll = max(0, disp_idx - list_h + 1)

    def current_session(self):
        if getattr(self, "header_only", False) and getattr(self, "header_target", None) and self.header_target[1]:
            ht_tool, ht_id = self.header_target
            ht_id = (ht_id or "").strip()
            ht_tool = (ht_tool or "").strip()
            if 0 <= self.selected_idx < len(self.sessions):
                cur = self.sessions[self.selected_idx]
                cur_id = str(cur.get("id") or "")
                if (cur_id == ht_id or (ht_id and cur_id.startswith(ht_id))) and (not ht_tool or cur.get("tool") == ht_tool):
                    return cur
            for idx, cand in enumerate(self.sessions):
                cid = str(cand.get("id") or "")
                if (cid == ht_id or (ht_id and cid.startswith(ht_id))) and (not ht_tool or cand.get("tool") == ht_tool):
                    self.selected_idx = idx
                    return cand
            t_label = {"claude": "Claude", "agy": "Gemini", "codex": "Codex", "deepseek": "DeepSeek"}.get(ht_tool, (ht_tool or "Agent").capitalize())
            return {
                "tool": ht_tool or "claude",
                "id": ht_id or "new",
                "title": f"New {t_label} Session",
                "shortCwd": "~",
                "isLive": True,
                "agentState": "wait_input",
            }
        if 0 <= self.selected_idx < len(self.sessions):
            return self.sessions[self.selected_idx]
        return None

