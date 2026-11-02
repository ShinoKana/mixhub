#!/usr/bin/env python3
"""
Compatibility shim: process tree ancestry filter (A2) and process identity triple guard (A5) live in `mixhub.proc`.

`import proc_guard` keeps working for tests and external tools; every name the old single-file module defined is
re-exported from its owning module:

  mixhub.proc.identity  `PS_ENV`, `ProcessIdentity`, `ProcNode`, boot id and `lstart` parsing
  mixhub.proc.classify  agent-family / dispatch-script / headless classification
  mixhub.proc.tree      `parse_ps_snapshot`, `build_proc_tree`, agent-ancestor detection
  mixhub.proc.guard     `inspect_live_process`, `capture_process_identity`, `safe_kill_process`

The boot id cache (`_CACHED_BOOT_ID`) is rebound at runtime; read it on `mixhub.proc.identity`. Patch the owning
module (e.g. `mixhub.proc.guard.get_boot_id` to affect `safe_kill_process`), not this one.
"""
from __future__ import annotations

from mixhub.proc.classify import (  # noqa: F401
    _AGENT_FAMILY_PATTERNS,
    _DISPATCH_CACHE,
    _INTERNAL_BEACON_RE,
    _INTERP_VALUE_FLAGS,
    _INTERPRETER_RE,
    _is_headless_invocation,
    _is_same_family_wrapper,
    _SHELL_NAMES,
    _split_argv_safe,
    AGENT_CLI_NAMES,
    classify_proc_args,
    dispatch_script_names,
    has_internal_beacon,
    is_recognized_agent_command,
)
from mixhub.proc.guard import (  # noqa: F401
    capture_process_identity,
    inspect_live_process,
    safe_kill_process,
)
from mixhub.proc.identity import (  # noqa: F401
    _BOOT_SEC_RE,
    _DOW_NAMES,
    _MONTHS,
    get_boot_id,
    normalize_boot_id,
    normalize_start_time,
    parse_lstart_epoch,
    ProcessIdentity,
    ProcNode,
    PS_ENV,
    reset_boot_id_cache,
)
from mixhub.proc.tree import (  # noqa: F401
    _extract_node_fields,
    build_proc_tree,
    find_agent_ancestor_detail,
    has_agent_ancestor,
    parse_ps_snapshot,
)
