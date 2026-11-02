#!/usr/bin/env python3
"""`mixhub.tui.web`: `↗ web` sidebar link: Web UI URL, port probe, and start-then-open worker."""
from __future__ import annotations

import os
import socket
import subprocess
import urllib.parse

from mixhub.core.browser import open_url
from mixhub.core.env import env
from mixhub.core.launch import CLI_MODULE, child_env, module_argv
from mixhub.tui import paths as _paths

WEB_LINK_LABEL = "↗ web"  # `--sidebar` bottom-right entry to the Web UI (Row h-1, right of `cpu` / `mem`)


def _web_port() -> int:
    try:
        return int(env("PORT", "8765"))
    except ValueError:
        return 8765


def web_archive_url(session: dict | None = None) -> str:
    """Web UI URL: `http://127.0.0.1:8765/#session=<id>` deep link for a session, the bare root on Home."""
    base = f"http://127.0.0.1:{_web_port()}"
    sid = str((session or {}).get("id") or "")
    return f"{base}/#session={urllib.parse.quote(sid, safe='')}" if sid else base


def _web_port_listening(port: int, timeout: float = 0.2) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _ensure_web_and_open(url: str) -> None:
    """
    Blocking worker, always run off the UI thread: if nothing listens on the Web port, run `hub start` (same
    `.hub.lock` / `.hub.pid` / `hub.log` path as the CLI, returns once `/api/health` answers), then open `url`.
    """
    if not _web_port_listening(_web_port()):
        try:
            os.makedirs(_paths.ROOT, exist_ok=True)
            subprocess.run(
                module_argv(CLI_MODULE, "start"),
                cwd=_paths.ROOT,
                env=child_env(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=8,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    open_url(url)
