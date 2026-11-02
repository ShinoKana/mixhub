#!/usr/bin/env python3
"""
`mixhub.rules`: MixHub SSoT bidirectional rules compiler (AGENTS.md <-> CLAUDE.md / .gemini/GEMINI.md) and
workspace trust pre-seeding for Claude Code, Antigravity / Gemini and Codex.

Modules:
  blocks  managed `BEGIN/END HUB SYNC` blocks, `hub-policy` extraction, sha256 digests
  trust   opt-in workspace trust pre-seeding (`ensure_workspace_trusted`) and its Preferences lookup
  sync    the CLAUDE.md read-only guard, `sync_rules` and the `hub sync-rules` command (`main`)

The repo-root `rules_sync.py` is a compatibility shim re-exporting these names.
"""
from __future__ import annotations
