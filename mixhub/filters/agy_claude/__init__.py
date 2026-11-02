#!/usr/bin/env python3
"""
`mixhub.filters.agy_claude`: Antigravity mouse-shim PTY filter wrapping the real `agy` binary.

Modules:
  prefs     real `agy` resolution, `Preferences` / env switches (`is_mouse_shim_enabled`)
  patterns  byte / text regexes shared by the input and output sides
  vscreen   display-width / cursor-column math and the replayed virtual screen (`VScreen`)
  debuglog  mouse diagnostics ring buffer (`MouseDebugLog`)
  cursor    prompt box cursor tracker (`PromptCursorTracker`)
  mouse     stdin mouse translation (`filter_stdin_mouse`), `sync_winsize`, `_SelClear`
  stream    output side: subagent badge, `transform_chunk`, exit restore bytes, coalescing, cursor guard
  main      the PTY filter loop (`main`)

MixHub runs the filter as `python -m mixhub.filters.agy_claude` (`__main__`); the repo-root `agy_claude_filter.py`
of a source checkout is a re-export shim that can still be run as a script (e.g. wrapped as `agy`).
This package must stay importable without the rest of MixHub (no `mixhub.hub` / `providers` / `channels` imports).
"""
