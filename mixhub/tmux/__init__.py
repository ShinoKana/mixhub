#!/usr/bin/env python3
"""
`mixhub.tmux`: The tmux side of MixHub.

  - `gateway`      : Unified `TmuxGateway` (`outer` / `pool`) and `FakeTmux` for deterministic tests.
  - `outer`        : The outer `hub` session (pane ids, zoom, focus, client ttys, window title, reattach marker).
  - `workspace`    : Outer dual-pane layout, mouse / copy bindings, keep-alive hint.
  - `home_shell`   : The login shell under the reader on Home.
  - `commands`     : Resume argv, `hubpool` window names, `--header` / `--on-exit` command lines.
  - `headers`      : The pinned 2-row `--header` pane of every `hubpool` window.
"""
from __future__ import annotations

from mixhub.tmux.gateway import (
    DEFAULT_TMUX_GATEWAY,
    FakePopen,
    FakeTmux,
    TmuxGateway,
    _TMUX_BIN,
    clean_tmux_env,
    is_mock_object,
)

__all__ = [
    "DEFAULT_TMUX_GATEWAY",
    "FakePopen",
    "FakeTmux",
    "TmuxGateway",
    "_TMUX_BIN",
    "clean_tmux_env",
    "is_mock_object",
]
