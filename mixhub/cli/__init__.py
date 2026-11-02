#!/usr/bin/env python3
"""
`mixhub.cli`: the `hub` / `mixhub` command line (entry points: `bin/hub`, the `mixhub.cli.main:entry` console scripts, `python3 -m mixhub`).

Modules:
  main       the command dispatcher (`main`)
  daemon     Web daemon `start` / `stop` / `health` (`URL`, `PID_FILE`) and `reload_workspace_panes`
  sessions   Hub-backed commands: `handoff`
  tools      commands delegating to another module's `main`: `sync-rules`, `notify`, `doctor`
  workspace  outer tmux workspace, window title, attach / detach hooks, `reload`
"""
