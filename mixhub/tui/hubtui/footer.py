#!/usr/bin/env python3
"""`mixhub.tui.hubtui.footer`: Sidebar footer: per-agent quota glyphs and cached cpu / mem metrics."""
from __future__ import annotations

try:
    import curses
except ImportError:  # Windows without windows-curses installed
    curses = None
import os
import time

from mixhub.hub.hub import Hub
from mixhub.tui import quota as _quota
from mixhub.tui.core.style import P_DIM, P_STAT_LBL, P_STAT_T1, P_STAT_T2, P_STAT_T3, P_STAT_T4
from mixhub.tui.quota import _QUOTA_SUMMARY_CACHE
from mixhub.tui.widgets.quota_table import _FOOTER_AGENTS, _morandi_quota_pair


class FooterMixin:
    """Draws the sidebar's bottom quota and system-metrics rows."""

    def _get_cached_system_metrics(self, now_mono: float) -> dict:
        """Return cached system CPU (`0..100%`) and Memory Used/Total/Pressure matching macOS Activity Monitor."""
        cached_ts = getattr(self, "_sys_metrics_ts", 0.0)
        if now_mono - cached_ts < 2.5 and hasattr(self, "_sys_metrics_val"):
            return self._sys_metrics_val
        try:
            m = Hub.get_system_resource_metrics(now_mono)
        except Exception:
            m = {
                "sysCpuPercent": 0,
                "sysMemoryUsedMB": 0.0,
                "sysMemoryTotalMB": 16384.0,
                "sysMemoryPercent": 0,
                "sysMemoryPressureLevel": 1,
            }
        self._sys_metrics_ts = now_mono
        self._sys_metrics_val = m
        return m

    def _get_cached_cpu_percent(self, now_mono: float) -> int:
        """Return non-blocking cached system CPU load percentage (`0..100%`, matching Activity Monitor)."""
        cached_ts = getattr(self, "_cpu_pct_ts", 0.0)
        if now_mono - cached_ts < 2.5 and hasattr(self, "_cpu_pct_val"):
            return self._cpu_pct_val
        sys_m = self._get_cached_system_metrics(now_mono)
        pct = min(100, max(0, int(sys_m.get("sysCpuPercent", 0))))
        self._cpu_pct_ts = now_mono
        self._cpu_pct_val = pct
        return pct

    def _draw_footer_quota(self, h: int, left_w: int) -> None:
        """
        Render bottom sidebar quota as a clean number table displaying Used % (`0..100%`, matching
        `claude.ai/settings/usage`, `gemini.google.com/usage`, and bottom `cpu`/`mem`) with 4-tier
        Morandi colored percentages (`P_STAT_T1..T4`: lower used % = sage green, higher used % = coral red)
        and brand-colored `claude` / `codex` / `gemini` labels:
          `  claude    ·          94% 10/28`
          `  codex    67% 04:21   21% 10/31`
          `  gemini   26% 02:29   38% 10/25`
        """
        q_home = getattr(self.hub, "home", None)
        q_env_sl = os.environ.get("MIXHUB_STATUSLINE_JSON", "")
        q_cache_ts = float(_QUOTA_SUMMARY_CACHE.get("ts") or 0.0)
        if type(_quota.get_cli_quota_metrics).__name__ == "function":
            now_m = time.monotonic()
            q_cache = getattr(self, "_footer_quota_cache", None)
            if (
                q_cache is not None
                and q_cache_ts > 0.0
                and q_cache[0] == (q_home, q_env_sl, q_cache_ts)
                and (now_m - q_cache[1]) < 0.25
            ):
                q_items = q_cache[2]
            else:
                q_items = _quota.get_cli_quota_metrics(home_dir=q_home)
                self._footer_quota_cache = ((q_home, q_env_sl, float(_QUOTA_SUMMARY_CACHE.get("ts") or 0.0)), now_m, q_items)
        else:
            q_items = _quota.get_cli_quota_metrics(home_dir=q_home)
        by_key = {str(it.get("key")): it for it in q_items}
        active = []
        for key, label, glyph, pair, x in self._footer_agents():
            it = by_key.get(key) or {}
            r5, rwk = it.get("rem_5h"), it.get("rem_wk")
            known = [p for p in (r5, rwk) if p is not None]
            if known:
                active.append((key, label, glyph, pair, x, it, known))
        if not active:
            return
        if h >= 16:
            start_y = h - 1 - len(active)
            for idx, (_, label, _, pair, _, it, _) in enumerate(active):
                ry = start_y + idx
                r5, rwk = it.get("rem_5h"), it.get("rem_wk")
                t5 = str(it.get("reset_5h") or "")
                twk = str(it.get("reset_wk") or "")
                u5 = max(0, min(100, 100 - int(r5))) if r5 is not None else None
                uwk = max(0, min(100, 100 - int(rwk))) if rwk is not None else None
                has_u5 = u5 is not None and (u5 > 0 or (bool(t5) and bool(it.get("started_5h"))))
                has_uwk = uwk is not None and (uwk > 0 or (bool(twk) and bool(it.get("started_wk"))))
                p5_s = f"{u5:>3d}%" if has_u5 else "  · "
                pw_s = f"{uwk:>3d}%" if has_uwk else "  · "
                p5_pair = _morandi_quota_pair(r5) if has_u5 else P_DIM
                pw_pair = _morandi_quota_pair(rwk) if has_uwk else P_DIM
                try:
                    self.stdscr.addstr(ry, 2, label, curses.color_pair(pair))
                    self.stdscr.addstr(ry, 9, p5_s, curses.color_pair(p5_pair))
                    if has_u5 and t5 and 14 + len(t5) < left_w:
                        self.stdscr.addstr(ry, 14, t5, curses.color_pair(P_DIM))
                    if 23 < left_w:
                        self.stdscr.addstr(ry, 20, pw_s, curses.color_pair(pw_pair))
                    if has_uwk and twk and 25 + len(twk) < left_w:
                        self.stdscr.addstr(ry, 25, twk, curses.color_pair(P_DIM))
                except curses.error:
                    pass
            return
        for _, label, _, pair, x, it, known in active:
            eff_rem = min(int(p) for p in known)
            eff_used = max(0, min(100, 100 - eff_rem))
            val_s = f"{eff_used}%"
            try:
                self.stdscr.addstr(h - 2, x, label[:2], curses.color_pair(pair))
                self.stdscr.addstr(h - 2, x + 3, val_s, curses.color_pair(_morandi_quota_pair(eff_rem)))
            except curses.error:
                pass

    @staticmethod
    def _morandi_stat_attrs(
        cpu_pct: int,
        mem_mb: float,
        total_mem_mb: float = 0.0,
        pressure_level: int = 1,
    ) -> tuple[int, int, int, int]:
        """
        Return `(cpu_lbl_attr, cpu_val_attr, mem_lbl_attr, mem_val_attr)` with Morandi 4-tier colors and ZERO bold.
        The `cpu ` / `mem ` labels always stay dim gray (`P_STAT_LBL`); only the values move across `P_STAT_T1..T4`.
        """
        if cpu_pct < 40:
            c_val = curses.color_pair(P_STAT_T1)
        elif cpu_pct < 65:
            c_val = curses.color_pair(P_STAT_T2)
        elif cpu_pct < 85:
            c_val = curses.color_pair(P_STAT_T3)
        else:
            c_val = curses.color_pair(P_STAT_T4)

        if pressure_level >= 4:
            m_val = curses.color_pair(P_STAT_T4)
        elif pressure_level >= 2:
            m_val = curses.color_pair(P_STAT_T3)
        elif total_mem_mb > 0:
            mem_pct = (mem_mb * 100.0) / total_mem_mb
            if mem_pct < 50.0:
                m_val = curses.color_pair(P_STAT_T1)
            elif mem_pct < 65.0:
                m_val = curses.color_pair(P_STAT_T2)
            elif mem_pct < 85.0:
                m_val = curses.color_pair(P_STAT_T3)
            else:
                m_val = curses.color_pair(P_STAT_T4)
        else:
            mem_gb = mem_mb / 1024.0
            if mem_gb < 2.5:
                m_val = curses.color_pair(P_STAT_T1)
            elif mem_gb < 4.0:
                m_val = curses.color_pair(P_STAT_T2)
            elif mem_gb < 5.5:
                m_val = curses.color_pair(P_STAT_T3)
            else:
                m_val = curses.color_pair(P_STAT_T4)
        lbl = curses.color_pair(P_STAT_LBL)
        return lbl, c_val, lbl, m_val

    def _footer_agents(self) -> list[tuple]:
        """Quota footer rows only for agents that have sessions on this machine (running or not); headless script
        runs and subagents do not count, so an agent only ever driven by scripts gets no row."""
        sessions = list(getattr(self.hub, "sessions", {}).values()) + list(self.sessions or [])
        present = {str(s.get("tool") or "").lower() for s in sessions
                   if not (s.get("automated") or s.get("isTemp") or s.get("isSubagent"))}
        tool_of = {"cc": "claude", "cx": "codex", "agy": "agy"}
        return [row for row in _FOOTER_AGENTS if tool_of.get(row[0], row[0]) in present]
