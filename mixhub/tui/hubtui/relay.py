#!/usr/bin/env python3
"""`mixhub.tui.hubtui.relay`: Relay (handoff) to another agent: the selector states, pills, and executing the handoff."""
from __future__ import annotations

try:
    import curses
except ImportError:  # Windows without windows-curses installed
    curses = None
import os
import subprocess
import time

from mixhub.core.textwidth import disp_trunc
from mixhub.tmux import commands as _commands
from mixhub.tmux import headers as _headers
from mixhub.tmux import outer as _outer
from mixhub.tmux import pool as _pool
from mixhub.tmux import workspace as _workspace
from mixhub.tmux.commands import _DEEPSEEK_ENV_SOURCE, _deepseek_window_env_args
from mixhub.tmux.new_sessions import _NEW_HUBPOOL_SESSIONS, _make_new_hubpool_session_dict
from mixhub.tmux.outer import current_outer_session_name, current_pool_session_name
from mixhub.tui import diag as _diag
from mixhub.tui.core.style import (
    P_AGENT_AGY,
    P_AGENT_AGY_HOV,
    P_AGENT_CLAUDE,
    P_AGENT_CLAUDE_HOV,
    P_AGENT_CODEX,
    P_AGENT_CODEX_HOV,
    P_BAND_T4,
    P_DIM,
    P_DONE,
    P_DONE_HOV,
    P_HOVER,
    P_HOVER_DIM,
    P_META,
    P_STAT_T4,
    P_USER_BOX,
    P_WHITE,
)
from mixhub.tui.drafts import load_unsent_drafts, pop_unsent_draft, save_unsent_draft
from mixhub.tui.widgets.header_bar import RELAY_STATES


class RelayMixin:
    """Relay selector state and layout, and handing a session off to another agent window."""


    # ------------------------------------------------------------------ 2-level Relay state machine
    def _eff_relay_state(self) -> str:
        """
        Effective Relay state. `_relay_menu` is the open flag (kept for backward compatibility: setting it to
        True while `_relay_state == "CLOSED"` means `PICK_AGENT`, setting it False closes), `_relay_state` the level.
        The menu auto-closes as soon as the selected session changes (anchor id mismatch).
        """
        if not getattr(self, "_relay_menu", False):
            if getattr(self, "_relay_state", "CLOSED") != "CLOSED":
                self._relay_state = "CLOSED"
                self._relay_anchor_id = None
            return "CLOSED"
        anchor = getattr(self, "_relay_anchor_id", None)
        if anchor is not None:
            cur = self.current_session()
            if not cur or cur.get("id") != anchor:
                self._set_relay_state("CLOSED")
                return "CLOSED"
        st = getattr(self, "_relay_state", "CLOSED")
        return st if st in ("PICK_AGENT", "PICK_CLAUDE_BACKEND") else "PICK_AGENT"

    def _set_relay_state(self, state: str) -> None:
        state = state if state in RELAY_STATES else "CLOSED"
        self._relay_state = state
        self._relay_menu = state != "CLOSED"
        if state == "CLOSED":
            self._relay_anchor_id = None
        elif getattr(self, "_relay_anchor_id", None) is None:
            cur = self.current_session()
            self._relay_anchor_id = cur.get("id") if cur else None
        self._dirty = True

    # Inline Relay provider chips: `(brand pair on the Row 1 bg 235 band, brand pair on a transparent row)`
    _RELAY_BRAND_PAIRS = {
        "relay_pick_claude": (P_AGENT_CLAUDE_HOV, P_AGENT_CLAUDE),
        "relay:claude": (P_AGENT_CLAUDE_HOV, P_AGENT_CLAUDE),
        "relay:agy": (P_AGENT_AGY_HOV, P_AGENT_AGY),
        "relay:codex": (P_AGENT_CODEX_HOV, P_AGENT_CODEX),
        "relay:deepseek": (P_DONE_HOV, P_DONE),
    }

    def _relay_item_attr(
        self, r_act: str, r_hov: bool, eff_relay: str, has_quota_rec: bool, on_header_band: bool = False
    ) -> int:
        """
        Attr of one inline Relay item, shared by Row 1 (`--header` / `--reader`, `on_header_band=True`) and the
        Preview bar on Row h-1. Every item keeps the row's own background (the bg 235 band on Row 1, transparent
        on the Preview bar): no nested bg 237 box. Hover / open only brighten and bold the text; providers keep
        their brand fg and add an underline on hover. Never `P_BTN_HOT` (blue) and never `A_REVERSE`.
        """
        bright = curses.color_pair(P_HOVER if on_header_band else P_WHITE) | curses.A_BOLD
        dim = curses.color_pair(P_HOVER_DIM if on_header_band else P_META)
        if r_act == "relay_toggle":
            if eff_relay != "CLOSED" or r_hov:
                return bright
            if has_quota_rec:
                return curses.color_pair(P_BAND_T4 if on_header_band else P_STAT_T4) | curses.A_BOLD
            return dim
        if r_act == "label":
            return curses.color_pair(P_HOVER_DIM if on_header_band else P_DIM)
        if r_act == "relay_back":
            return bright if r_hov else dim
        band_pair, rest_pair = self._RELAY_BRAND_PAIRS.get(r_act, (P_HOVER, P_WHITE))
        attr = curses.color_pair(band_pair if on_header_band else rest_pair) | curses.A_BOLD
        return attr | curses.A_UNDERLINE if r_hov else attr

    @staticmethod
    def _deepseek_relay_ok() -> bool:
        """`providers.is_deepseek_configured()` when available, otherwise assume DeepSeek is selectable."""
        try:
            from mixhub.providers import deepseek as _deepseek

            fn = getattr(_deepseek, "is_deepseek_configured", None)
            return bool(fn()) if callable(fn) else True
        except Exception:
            return True

    def _handle_relay_action(self, act: str) -> bool:
        """Dispatch one Relay hit (`relay_toggle` / `relay_pick_claude` / `relay_back` / `relay:<tool>`)."""
        cur_state = self._eff_relay_state()
        if act == "relay_toggle":
            self._set_relay_state("PICK_AGENT" if cur_state == "CLOSED" else "CLOSED")
            return True
        if act == "relay_pick_claude":
            if self._deepseek_relay_ok():
                self._set_relay_state("PICK_CLAUDE_BACKEND")
                return True
            act = "relay:claude"
        if act == "relay_back":
            self._set_relay_state("PICK_AGENT")
            return True
        if act.startswith("relay:"):
            tool = act.split(":", 1)[1]
            s = self.current_session()
            self._set_relay_state("CLOSED")
            if s and tool:
                self._execute_handoff(s, tool)
            return True
        return False

    def _prompt_and_send_unified_message(self, initial_text: str | None = None):
        s = self.current_session()
        if not s:
            return
        if self.reader_only and os.environ.get("TMUX"):
            try:
                _outer.focus_outer_right_pane()
            except Exception:
                pass
        h, w = self.stdscr.getmaxyx()
        sidebar_only = self.sidebar_only or (w < 64 and not self.reader_only)
        left_w = self._effective_left_w(w, sidebar_only)
        right_x = 1 if (self.sidebar_collapsed or self.reader_only) else (left_w + 2)
        prefill = initial_text if initial_text is not None else load_unsent_drafts().get(s["id"], "")
        self._last_cancelled_input = ""
        msg = self.prompt_input(
            " ❯ ",
            prefill,
            row_y=max(0, h - 2),
            col_x=right_x,
            box_attr=curses.color_pair(P_USER_BOX),
        ).strip()
        if not msg:
            if getattr(self, "_last_cancelled_input", ""):
                save_unsent_draft(s["id"], self._last_cancelled_input, path=self.unsent_drafts_path)
            self.status_msg = ""
            self._dirty = True
            return
        pop_unsent_draft(s["id"], path=self.unsent_drafts_path)
        try:
            res = self.hub.action(s["tool"], s["id"], "send", {"text": msg})
            self.refresh_data(force=True)
            self.loaded_key = None
            self.msg_scroll = 999999
            self.status_msg = f"Sent to {s['tool']} ({res.get('status', 'ok')}): {disp_trunc(msg, 28)}"
        except Exception as exc:
            self.status_msg = f"Send failed: {exc}"
        self._dirty = True

    def _execute_handoff(self, s: dict, to_tool: str):
        """Compile `<1.5k tok` handoff bundle, pre-seed workspace trust, and launch target Agent CLI in `hubpool`."""
        self._relay_menu = False
        self._relay_state = "CLOSED"
        self._relay_anchor_id = None
        try:
            cwd = s.get("cwd") or os.path.expanduser("~")
            if not os.path.isdir(cwd):
                cwd = os.path.expanduser("~")
            try:
                from mixhub.rules.trust import ensure_workspace_trusted
                ensure_workspace_trusted(cwd, home_dir=getattr(self.hub, "home", None))
            except Exception:
                pass
            res = self.hub.handoff(
                {
                    "id": s["id"],
                    "from_tool": s["tool"],
                    "to": to_tool,
                }
            )
            self.refresh_data(force=True)
            toast = res.get("ephemeral_toast")
            if toast:
                self.show_ephemeral_toast(toast)
            if os.environ.get("TMUX"):
                outer_sess = current_outer_session_name()
                pool_sess = current_pool_session_name()
                _workspace.ensure_outer_dual_pane_layout(outer_sess, pool_sess)
                self._ensure_hubpool_ready()
                env = dict(os.environ)
                env.pop("TMUX", None)
                new_wname = f"w-{to_tool}-{s['id'][:12]}"
                subprocess.run(
                    ["tmux", "-L", "hubpool", "kill-window", "-t", f"pool:{new_wname}"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=env,
                )
                launch_cmd = res.get("command") or ""
                env_args: list[str] = []
                if to_tool == "deepseek":
                    # `Claude Code -> [2]DeepSeek`: CC·DS forks the Claude session (lossless brain switch) and boots
                    # from the `<1.5k tok` handoff prompt; other sources start a fresh CC·DS on the prompt alone.
                    from mixhub.providers.deepseek import build_cc_ds_launch_cmd

                    env_args = _deepseek_window_env_args(env)
                    from_claude = s.get("tool") == "claude"
                    launch_cmd = _DEEPSEEK_ENV_SOURCE + build_cc_ds_launch_cmd(
                        session_id=(s["id"] if from_claude else ""),
                        cwd=cwd,
                        model="deepseek-chat",
                        fork_session=from_claude,
                        prompt=(res.get("prompt") or ""),
                    )
                if launch_cmd:
                    wrapped_launch_cmd = _commands._build_hubpool_cli_wrapper_cmd(to_tool, s["id"], new_wname, cwd, launch_cmd)
                    subprocess.run(
                        ["tmux", "-L", "hubpool", "new-window", "-t", "pool:", "-n", new_wname, "-c", cwd, *env_args, wrapped_launch_cmd],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        env=env,
                    )
                    _headers.ensure_hubpool_window_header(new_wname, to_tool, s["id"], env=env)
                    subprocess.run(
                        ["tmux", "-L", "hubpool", "select-window", "-t", f"{pool_sess}:{new_wname}"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        env=env,
                    )
                    self._active_hubpool_win = new_wname
                    self._register_relayed_window(new_wname, to_tool, cwd, res)
                    _outer.focus_outer_right_pane(outer_sess)
            self.status_msg = (
                f"⇄ Relayed {s['tool']} → {to_tool} ({res['token_estimate']} tok handoff.yaml)"
            )
            _diag.log_tui_diag(
                "execute_handoff",
                session_id=str(s.get("id", "")),
                from_tool=str(s.get("tool", "")),
                to_tool=str(to_tool),
                token_estimate=int(res.get("token_estimate") or 0),
            )
        except Exception as exc:
            self.status_msg = f"Relay failed: {exc}"
            _diag.log_tui_diag(
                "execute_handoff_error",
                session_id=str(s.get("id", "")) if s else "",
                from_tool=str(s.get("tool", "")) if s else "",
                to_tool=str(to_tool),
                error=str(exc),
            )
        self._dirty = True

    _execute_relay_for_session = _execute_handoff

    def _register_relayed_window(self, wname: str, to_tool: str, cwd: str, res: dict) -> dict:
        """
        Track a freshly relayed `hubpool` window as a synthetic `new-...` Active row and select it. The selection
        carries `hubpoolWindow`, so a `--sidebar` in another process (relay fired from `--header` / `--reader`)
        adopts and highlights the same row; `refresh_data` later promotes it to the real session.
        """
        relay_sid = f"new-{int(time.time()) % 100000}"
        relay_s = _make_new_hubpool_session_dict(to_tool, relay_sid, wname, cwd=cwd, created_ts=time.time())
        relay_s["title"] = res.get("relay_title") or relay_s["title"]
        self._mark_session_seen(relay_s)
        _NEW_HUBPOOL_SESSIONS[wname] = dict(relay_s)
        _pool._record_hubpool_window(wname, present=True)
        self._collapsed_sections.discard("active")
        if not getattr(self, "header_only", False):
            self.sessions = [x for x in self.sessions if x.get("hubpoolWindow") != wname]
            pin_end = 0
            while pin_end < len(self.sessions) and self.sessions[pin_end].get("pinned"):
                pin_end += 1
            self.sessions.insert(pin_end, relay_s)
            self.selected_idx = pin_end
            self._ensure_selected_visible()
            self.loaded_key = None
        self._write_active_selection(relay_s)
        return relay_s

    def _trigger_handoff_interactive(self, to_tool: str | None = None):
        """
        Interactive Cross-Agent Handoff (`h` / `H` or clicking `⇄ Relay`):
          1. If `to_tool` is given (clicked from the inline/floating Relay Agent Selector), execute immediately.
          2. Otherwise prompt target agent (`1/c:claude`, `2/g:agy`, `3/x:codex`, `4/d:deepseek`, or Enter for smart default).
          3. Never kill any external live process, pre-seed workspace trust, and launch in `hubpool`.
        """
        s = self.current_session()
        if not s:
            self.status_msg = "Select a session first before triggering ⇄ Relay"
            self._dirty = True
            return
        cur_tool = (s.get("tool") or "").lower()
        default_target = "codex" if cur_tool != "codex" else "claude"
        if not to_tool:
            raw = self.prompt_input(
                f"⇄ Relay [1/c:Claude 2/g:Gemini 3/x:Codex 4/d:DeepSeek] (default {default_target}): ",
                "",
            ).strip().lower()
            alias_map = {
                "": default_target,
                "1": "claude",
                "c": "claude",
                "claude": "claude",
                "2": "agy",
                "g": "agy",
                "gemini": "agy",
                "agy": "agy",
                "3": "codex",
                "x": "codex",
                "codex": "codex",
                "4": "deepseek",
                "d": "deepseek",
                "deepseek": "deepseek",
            }
            to_tool = alias_map.get(raw, raw)
        if to_tool not in ("claude", "agy", "codex", "deepseek"):
            self._relay_menu = False
            self.status_msg = ""
            self._dirty = True
            return
        self._execute_handoff(s, to_tool)
