#!/usr/bin/env python3
"""
`mixhub.proc`: process tree ancestry filter (A2) and process identity triple guard (A5).

- A2 (`build_proc_tree`, `has_agent_ancestor`):
  Walks `ppid` upward from a candidate agent PID (up to `tmux` server or `pid <= 1`,
  `max_depth=64`) using a single `ps -axo pid=,ppid=,lstart=,args=` snapshot.
  Collapses contiguous same-tool launcher wrappers (`node .../codex`, `python3 agy_claude_filter.py`,
  `cc-ds`, etc.) and flags any process whose ancestor chain contains another coding agent
  (`claude`, `codex`, `agy`, `gemini`, `opencode`) or a dispatch script listed in Preferences
  `dispatch_scripts` (wrappers that start agents for you), or carries the `MIXHUB_INTERNAL=1` beacon, returning `"subagent_ppid"`.

- A5 (`ProcessIdentity`, `safe_kill_process`):
  Verifies `(boot_id, pid, start_time)` plus recognized agent CLI command before sending
  any termination signal (`SIGTERM` / `SIGINT`), guaranteeing 0 false kills on PID reuse
  or across host reboots.

Modules:
  identity  `ps` environment, `ProcessIdentity`, `ProcNode`, boot id and `lstart` parsing
  classify  agent-family / dispatch-script / headless classification of a command line
  tree      `ps` snapshot parsing, process tree, agent-ancestor detection
  guard     live-process inspection and the guarded `safe_kill_process`

The repo-root `proc_guard.py` is a compatibility shim re-exporting these names.
"""
from __future__ import annotations
