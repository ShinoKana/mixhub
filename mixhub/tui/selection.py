#!/usr/bin/env python3
"""`mixhub.tui.selection`: `active_selection.json`, the sidebar -> reader selection hand-off file."""
from __future__ import annotations

import json
import os
import time

from mixhub.tmux import outer as _outer
from mixhub.tui import paths as _paths


def write_active_selection(session: dict | None, *, activate_reader: bool = False, close_window: str = "",
                           close_windows: list[str] | None = None, path: str | None = None,
                           sync_title: bool = True):
    sel_path = path or _paths._effective_active_sel_path()
    try:
        os.makedirs(os.path.dirname(sel_path), exist_ok=True)
        tmp = f"{sel_path}.tmp.{os.getpid()}"
        payload = (
            {
                "tool": session["tool"],
                "id": session["id"],
                "title": session.get("title", ""),
                "path": session.get("path", ""),
                "empty": False,
                "ts": time.time(),
                **({"hubpoolWindow": session["hubpoolWindow"]} if session.get("hubpoolWindow") else {}),
            }
            if session
            else {
                "tool": "",
                "id": "",
                "title": "",
                "path": "",
                "empty": True,
                "ts": time.time(),
            }
        )
        if activate_reader:
            # The reader consumes this request only after drawing the requested frame.
            payload["activateReader"] = True
            if close_window:
                payload["closeWindow"] = close_window
            if close_windows:
                payload["closeWindows"] = close_windows
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, sel_path)
    except OSError:
        pass
    if sync_title:
        _outer._sync_outer_window_title(session)


def read_active_selection(path: str | None = None) -> dict | None:
    sel_path = path or _paths._effective_active_sel_path()
    try:
        with open(sel_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None
