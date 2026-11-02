#!/usr/bin/env python3
"""
`mixhub.probe`: declarative bottom-15-lines terminal blocker probe.

Inspects live CLI panes in `tmux -L hubpool` (`claude`, `codex`, `agy`) to detect interactive
permission prompts, folder trust dialogs, multiple-choice questions, and active spinner states
before they are flushed to `.jsonl` transcripts.

Modules:
  patterns  tail sizes, the rules file path, the built-in rule tables and every screen regex
  rules     rule compilation and the hot-reloadable `data/probe_rules.json` loader
  text      ANSI stripping, box-border cleanup, secret redaction, raw excerpts
  capture   `tmux` runner, pool pane resolution and pane capture
  menus     numbered-menu, header / command, fingerprint and hotkey-box detection
  classify  `classify_pane_text` (`probe_pane_state`) and `probe_session_pane`

The repo-root `probe.py` is a compatibility shim re-exporting these names.
"""
from __future__ import annotations
