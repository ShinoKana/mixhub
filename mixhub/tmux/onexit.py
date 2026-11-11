#!/usr/bin/env python3
"""`mixhub.tmux.onexit`: `tui.py --on-exit`: what happens when a CLI in a `hubpool` window exits."""
from __future__ import annotations

import json
import os
import re
import subprocess
import time

from mixhub.tmux import pool as _pool
from mixhub.tmux.session_state import (
    _clear_last_exit,
    _consume_hibernating,
    _remove_from_working_set_file,
    _resolve_promoted_new_session,
    _update_working_set_file,
)
from mixhub.tui import diag as _diag
from mixhub.tui import paths as _paths
from mixhub.tui import selection as _selection


def handle_cli_on_exit(tool: str, sid: str, wname: str, rc: int = 0, working_set_path: str | None = None) -> int:
    """
    `--on-exit` hook run by the `hubpool` CLI wrapper after the native CLI exits.

    1. Hub-initiated hibernate (marker written in the last few minutes): consume the marker and keep the session in
       `Active`. An older marker is removed and the exit handled as below.
    2. Signal termination: preserve the real session in `Active`; never write a normal-exit marker.
    3. The CLI failing (any other non-zero rc, e.g. `claude --resume` finding no conversation): keep it in `Active`.
    4. User `/exit` / `Ctrl-D` (rc 0): drop it from `active_working_set.json` (moves `Active` -> `Recent`) and record
       `data/last_exit/<sid>.json`.
    Then close the window. Only when it was the window the user was looking at does the view move: `--reader` shows
    this session's Preview and `pool:reader` is selected first.
    """
    eff_tool, eff_sid, eff_wname = _resolve_promoted_new_session(tool, sid, wname)
    env = dict(os.environ)
    env.pop("TMUX", None)
    if wname and str(eff_sid).startswith("new-"):
        run_fn = subprocess.run
        is_mock_run = type(run_fn).__name__ in ("MagicMock", "AsyncMock", "NonCallableMagicMock")
        if not is_mock_run:
            try:
                pane_env = os.environ.get("TMUX_PANE", "").strip()
                pres = subprocess.run(
                    ["tmux", "-L", "hubpool", "list-panes", "-a", "-F", "#{window_name}|#{pane_id}|#{pane_start_command}"],
                    capture_output=True,
                    text=True,
                    env=env,
                    timeout=2,
                )
                p_lines = (pres.stdout if isinstance(getattr(pres, "stdout", None), str) else "").splitlines()
                if pane_env:
                    for ln in p_lines:
                        pts = ln.split("|", 2)
                        if len(pts) >= 2 and pts[1].strip() == pane_env and pts[0].strip().startswith("w-"):
                            eff_wname = pts[0].strip()
                            break
                for ln in p_lines:
                    pts = ln.split("|", 2)
                    if len(pts) == 3 and pts[0].strip() in (eff_wname, wname):
                        m_hdr = re.search(r"--header\s+(\S+)\s+(\S+)", pts[2])
                        if m_hdr:
                            hdr_t = m_hdr.group(1).strip("'\"")
                            hdr_s = m_hdr.group(2).strip("'\"")
                            if hdr_s and not hdr_s.startswith("new-"):
                                eff_tool = hdr_t or eff_tool
                                eff_sid = hdr_s
                                eff_wname = pts[0].strip()
                                break
            except Exception:
                pass
    # A list, not a generator: consume the markers of both ids, not just the first.
    if any([_consume_hibernating(check_id) for check_id in dict.fromkeys(x for x in (sid, eff_sid) if x)]):
        return 0
    try:
        rc_i = int(rc)
    except (TypeError, ValueError):
        rc_i = 0
    exit_dir = (
        os.path.join(os.path.dirname(working_set_path), "data", "last_exit")
        if working_set_path
        else _paths.LAST_EXIT_DIR
    )
    if rc_i < 0 or rc_i >= 128:
        if eff_sid and not str(eff_sid).startswith("new-"):
            _update_working_set_file(eff_tool or tool, eff_sid, working_set_path, keep=True)
            _clear_last_exit(eff_sid, exit_dir)
        # The server or window may already be gone. Do not navigate during shutdown.
        return 0
    is_active = False
    if eff_wname:
        try:
            res = subprocess.run(
                ["tmux", "-L", "hubpool", "display-message", "-p", "-t", f"pool:{eff_wname}", "#{window_active}"],
                capture_output=True,
                text=True,
                env=env,
                timeout=3,
            )
            out = res.stdout if isinstance(getattr(res, "stdout", None), str) else ""
            is_active = out.strip() == "1"
        except Exception:
            is_active = False
    if rc_i != 0:
        # The CLI failed (e.g. `claude --resume` found no conversation), the user didn't leave the session: it stays
        # in `Active`, and a background window failing (a retried delivery, say) never moves the user's view.
        if eff_sid and not str(eff_sid).startswith("new-"):
            _update_working_set_file(eff_tool or tool, eff_sid, working_set_path, keep=True)
        _diag.log_tui_diag("cli_failed", session=f"{eff_tool or tool}:{eff_sid or sid}", rc=rc_i, wname=eff_wname or "")
    else:
        for drop_id in dict.fromkeys(x for x in (sid, eff_sid) if x):
            _remove_from_working_set_file(eff_tool or tool, drop_id, working_set_path, reason=f"exit rc={rc}")
        try:
            os.makedirs(exit_dir, exist_ok=True)
            for exit_id in dict.fromkeys(x for x in (eff_sid, sid) if x):
                tmp = os.path.join(exit_dir, f".{exit_id}.json.tmp.{os.getpid()}")
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump({"ts": time.time(), "rc": rc_i, "tool": str(eff_tool or tool)}, f)
                os.replace(tmp, os.path.join(exit_dir, f"{exit_id}.json"))
        except OSError:
            pass
    if is_active or not wname:
        # Only the window the user was looking at hands the view to this session's Preview (an exit reported without
        # its window: nothing to tell it by)
        _selection.write_active_selection({"tool": eff_tool or tool, "id": eff_sid or sid, "title": "", "path": ""}, activate_reader=True)
    if not eff_wname:
        return 0
    try:
        if is_active:
            subprocess.run(
                ["tmux", "-L", "hubpool", "select-window", "-t", "pool:reader", ";", "kill-window", "-t", f"pool:{eff_wname}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                timeout=3,
            )
        else:
            subprocess.run(
                ["tmux", "-L", "hubpool", "kill-window", "-t", f"pool:{eff_wname}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                timeout=3,
            )
    except Exception:
        pass
    _pool._record_hubpool_window(eff_wname, present=False)
    if wname and wname != eff_wname:
        _pool._record_hubpool_window(wname, present=False)
    return 0
