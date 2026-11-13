#!/usr/bin/env python3
"""
MixHub: one switchboard for Claude Code, Gemini (AGY), Codex and OpenCode sessions.

The README's Architecture section maps the subpackages (`hub`, `providers`, `tui`, `tmux`, `handoff`, `channels`,
`probe`, `proc`, `rules`, `filters`, `web`, `cli`, `core`). The concrete `Hub` class lives in `mixhub.hub.hub`.
"""
from __future__ import annotations

__version__ = "0.2.2"

__all__ = ["__version__"]
