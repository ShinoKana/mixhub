#!/usr/bin/env python3
"""`mixhub.tui.hubtui.actions`: Session actions: new session, pin / rename, hibernate / move to Recent, takeover, and quit."""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time

from mixhub.core.textwidth import disp_trunc
from mixhub.tmux import commands as _commands
from mixhub.tmux import headers as _headers
from mixhub.tmux import outer as _outer
from mixhub.tmux import pool as _pool
from mixhub.tmux import workspace as _workspace
from mixhub.tmux.commands import _DEEPSEEK_ENV_SOURCE, _deepseek_window_env_args
from mixhub.tmux.headers import _install_hubpool_header_hooks
from mixhub.tmux.new_sessions import _NEW_HUBPOOL_SESSIONS, _make_new_hubpool_session_dict
from mixhub.tmux.outer import current_outer_session_name, current_pool_session_name
from mixhub.tmux.session_state import _clear_hibernating
from mixhub.tmux.pool import (
    _unlink_stale_codex_lock_file,
    focus_external_session,
    takeover_external_session_in_hubpool,
)
from mixhub.tui import diag as _diag
from mixhub.core.launch import AGY_FILTER_MODULE, module_command


class SessionActionsMixin:
    """What the sidebar does to sessions and to the workspace."""

    def toggle_pin_session(self, target_s: dict):
        new_pin = not bool(target_s.get("pinned"))
        self.hub.action(target_s["tool"], target_s["id"], "pin", {"pinned": new_pin})
        self.refresh_data(fast_only=True, reorder=True)
        self.status_msg = f"{'Pinned (★)' if new_pin else 'Unpinned'}: {disp_trunc(target_s.get('title', ''), 24)}"

    def focus_external_session(self, s: dict | None = None) -> bool:
        return focus_external_session(s or self.current_session(), app=self)

    def takeover_external_session_in_hubpool(self, s: dict | None = None) -> bool:
        return takeover_external_session_in_hubpool(s or self.current_session(), app=self)

    def _launch_new_agent_session(self, to_tool: str | None = None):
        """Launch a brand-new session in the right pane for the selected primary Agent (`claude` / `codex` / `agy` / `deepseek`)."""
        self._new_session_menu = False
        target = (to_tool or "claude").lower()
        if target == "gemini":
            target = "agy"
        if target not in ("claude", "agy", "codex", "deepseek", "opencode"):
            self._dirty = True
            return
        cur_s = self.current_session()
        cwd = (cur_s.get("cwd") if cur_s else None) or os.getcwd() or os.path.expanduser("~")
        if not os.path.isdir(cwd):
            cwd = os.path.expanduser("~")
        try:
            from mixhub.rules.trust import ensure_workspace_trusted
            ensure_workspace_trusted(cwd, home_dir=getattr(self.hub, "home", None))
        except Exception:
            pass
        if target == "deepseek":
            # CC·DS: Claude Code harness on DeepSeek, key from `~/.config/deepseek/env` / env / settings.json
            from mixhub.providers.deepseek import build_cc_ds_launch_cmd

            inner_cmd = (
                f'export PATH="$HOME/.local/bin:$PATH"; {_DEEPSEEK_ENV_SOURCE}'
                f'{build_cc_ds_launch_cmd(model="deepseek-chat")}'
            )
        else:
            cmd_map = {
                "claude": "claude",
                "agy": shlex.join(module_command(AGY_FILTER_MODULE)),
                "codex": "codex",
                "opencode": "opencode",
            }
            inner_cmd = f'export PATH="$HOME/.local/bin:$PATH"; {cmd_map[target]}'
        suffix = f"new-{int(time.time()) % 100000}"
        new_wname = f"w-{target}-{suffix}"
        launch_cmd = _commands._build_hubpool_cli_wrapper_cmd(target, suffix, new_wname, cwd, inner_cmd)
        self._show_new_hubpool_window(target, suffix, new_wname, cwd, launch_cmd)

    def _show_new_hubpool_window(self, target: str, suffix: str, new_wname: str, cwd: str, launch_cmd: str | None):
        """List a new `w-<tool>-new-N` window at the top of `Active`, select it and switch the right pane to it.
        `launch_cmd` starts the window; None means it already exists (an agent moved out of the Home shell)."""
        new_s = _make_new_hubpool_session_dict(target, suffix, new_wname, cwd=cwd, created_ts=time.time())
        self._mark_session_seen(new_s)
        _NEW_HUBPOOL_SESSIONS[new_wname] = dict(new_s)
        _pool._record_hubpool_window(new_wname, present=True)
        self._collapsed_sections.discard("active")
        self.sessions = [s for s in self.sessions if s.get("id") != suffix]
        pin_end = 0
        while pin_end < len(self.sessions) and self.sessions[pin_end].get("pinned"):
            pin_end += 1
        self.sessions.insert(pin_end, new_s)
        self.selected_idx = pin_end
        self._ensure_selected_visible()
        self.loaded_key = None
        self._write_active_selection(new_s)
        if os.environ.get("TMUX"):
            outer_sess = current_outer_session_name()
            pool_sess = current_pool_session_name()
            _workspace.ensure_outer_dual_pane_layout(outer_sess, pool_sess)
            self._ensure_hubpool_ready()
            env = dict(os.environ)
            env.pop("TMUX", None)
            local_bin = os.path.expanduser("~/.local/bin")
            cur_path = env.get("PATH", "")
            if local_bin not in cur_path.split(":"):
                env["PATH"] = f"{local_bin}:{cur_path}" if cur_path else local_bin
            if launch_cmd is not None:
                env_args = _deepseek_window_env_args(env) if target == "deepseek" else []
                subprocess.run(
                    ["tmux", "-L", "hubpool", "new-window", "-t", "pool:", "-n", new_wname, "-c", cwd, *env_args, launch_cmd],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=env,
                )
            _headers.ensure_hubpool_window_header(new_wname, target, suffix, env=env)
            subprocess.run(
                ["tmux", "-L", "hubpool", "select-window", "-t", f"{pool_sess}:{new_wname}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
            )
            self._active_hubpool_win = new_wname
            _outer.focus_outer_right_pane(outer_sess)
        self._dirty = True


    _NEW_SESSION_BINS = {"claude": "claude", "codex": "codex", "agy": "agy", "opencode": "opencode"}

    def new_session_items(self) -> list[tuple]:
        """`New Session` rows for agents this machine actually uses (CLI on PATH or sessions on disk), in the fixed
        `NEW_SESSION_ITEMS` order."""
        cache = getattr(self, "_new_session_items_cache", None)
        now_mono = time.monotonic()
        loaded = len(getattr(self.hub, "sessions", {}) or {})  # a cache from before the first scan would stick 30 s
        if cache is not None and now_mono - cache[0] < 30.0 and cache[2] == loaded:
            return cache[1]
        used = self._available_agent_tools(default=set())
        self._no_agent_cli = not any(shutil.which(b) for b in self._NEW_SESSION_BINS.values())
        items = []
        for row in self.NEW_SESSION_ITEMS:
            key = row[0]
            if key in used or shutil.which(self._NEW_SESSION_BINS.get(key, key)):
                items.append(row)
        items = items or [self.NEW_SESSION_ITEMS[0]]
        self._new_session_items_cache = (now_mono, items, loaded)
        return items

    def _available_agent_tools(self, default: set[str] | None = None) -> set[str]:
        """Return set of agent tools present on this machine (from hub sessions or default)."""
        tools = {str(s.get("tool") or "").lower() for s in getattr(self.hub, "sessions", {}).values() if s.get("tool")}
        if not tools:
            tools = {str(s.get("tool") or "").lower() for s in self.sessions if s.get("tool")}
        if tools:
            return tools
        return {"claude", "agy", "codex"} if default is None else set(default)

    def _ensure_hubpool_ready(self):
        """Ensure the inner `tmux -L hubpool` server and `reader` window exist."""
        env = dict(os.environ)
        env.pop("TMUX", None)
        has_pool = subprocess.run(
            ["tmux", "-L", "hubpool", "has-session", "-t", "pool"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        ).returncode == 0
        if not has_pool:
            reader_cmd = _commands._reader_cmd()
            subprocess.run(
                ["tmux", "-L", "hubpool", "new-session", "-d", "-s", "pool", "-n", "reader", reader_cmd],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
            )
            _workspace.configure_tmux_mouse_and_copy("hubpool", env=env, show_status=False)
        if not getattr(self, "_hdr_hooks_installed", False):
            # Pin every `--header` pane to HUB_HDR_ROWS rows across fullscreen / client resizes
            _install_hubpool_header_hooks(env=env)
            self._hdr_hooks_installed = True

    def _release_external_locks_before_takeover(self, s: dict, force_takeover: bool = False) -> tuple[bool, str]:
        """
        Before opening session `s` inside `hubpool` right pane, check if an older external
        CLI process is still holding the conversation lock in another Terminal tab.
        - By default (`force_takeover=False`), NEVER silently kill/hibernate an external Terminal tab!
          Instead, focus that external Terminal tab so we never accidentally kill an open tab or its subagents.
        - Only if `force_takeover=True` AND the session is not working / has no active subagents,
          gracefully terminate the idle external process (`SIGTERM`) so `hubpool` can take over.
        """
        home_dir = getattr(getattr(self, "hub", None), "home", None)
        ext_procs = [p for p in s.get("processes", []) if not p.get("resident")]
        if not ext_procs:
            unlinked = _unlink_stale_codex_lock_file(s, home_dir=home_dir)
            _diag.log_tui_diag(
                "release_external_lock",
                session_id=str(s.get("id", "")),
                tool=str(s.get("tool", "")),
                ext_procs=0,
                force_takeover=bool(force_takeover),
                unlinked_codex_lock=bool(unlinked),
            )
            return True, ""
        # Never terminate an actively working or wait_input external session or one with active subagents
        if (
            s.get("agentState") in ("working", "wait_input", "wait")
            or int(s.get("activeSubagents") or 0) > 0
            or s.get("notFullyIdle")
            or not force_takeover
        ):
            _diag.log_tui_diag(
                "release_external_lock",
                session_id=str(s.get("id", "")),
                tool=str(s.get("tool", "")),
                ext_procs=len(ext_procs),
                force_takeover=bool(force_takeover),
                action="focus_external",
            )
            try:
                self.hub.action(s["tool"], s["id"], "focus", {})
                return False, f"Focused external terminal tab ({s.get('tty','')})"
            except Exception:
                return False, "Session is running in an external terminal tab"
        # Explicit takeover of an idle external process: the one place MixHub stops an agent it did not start
        try:
            res = self.hub.hibernate(s, ext_procs, dry=False, keep_working_set=True)
            # The marker is for MixHub's own on-exit, which this outside process doesn't run.
            _clear_hibernating(s["id"])
            pending = set((res or {}).get("pendingPids") or [])
            deadline = time.monotonic() + 5.0
            while pending:
                pending &= {p.get("pid") for p in self.hub.processes()}
                if not pending:
                    break
                if time.monotonic() >= deadline:
                    # Resuming now would put two processes on one transcript
                    return False, "The other terminal's session is still exiting; open it again in a moment"
                time.sleep(0.2)
            time.sleep(0.25)
        except Exception:
            pass
        unlinked = _unlink_stale_codex_lock_file(s, home_dir=home_dir)
        _diag.log_tui_diag(
            "release_external_lock",
            session_id=str(s.get("id", "")),
            tool=str(s.get("tool", "")),
            ext_procs=len(ext_procs),
            force_takeover=bool(force_takeover),
            action="hibernate_and_takeover",
            unlinked_codex_lock=bool(unlinked),
        )
        return True, "Released external lock and resumed in right pane"

    _release_external_session_lock = _release_external_locks_before_takeover


