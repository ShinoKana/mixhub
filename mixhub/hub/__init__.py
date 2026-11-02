"""
`mixhub.hub`: session discovery, transcript parsing, process binding, lifecycle and the `Hub` object.

Modules (each imports only modules listed above it, plus `mixhub.core`, `mixhub.proc` and stdlib):
  constants        repo root (`constants.ROOT`), read sizes, UUID patterns, tool codes, cache schema version
  schema           provider storage schema adapters (every upstream path / DB / marker literal)
  util             hostname label, tolerant JSON, timestamps, file / SQLite signatures
  text             prompt cleaning, snippets, printable filtering
  titles           Claude title records and reverse scan, agy sender, placeholder titles
  transcript       JSONL head / tail sampling, per-tool entries, tail-state classification
  session_filters  automated / subagent / temp rules, local rules and runtime Preferences caches
  shell            `run`, AppleScript / Terminal helpers, resume commands
  opencode         OpenCode SQLite reader
  index            `SessionRecord`, `SessionIndex`
  send             typing into a session's tmux pane, headless resume

`Hub` (in `hub`) composes one mixin per concern:
  working_set      disk cache, `hub_state.json`, Active working set     (WorkingSetMixin)
  summary          session summaries, transcript windows                (TranscriptMixin)
  processes        `ps` parsing, background process snapshot, liveness   (ProcessMixin)
  pane_probe       cached `mixhub.probe` pane classification                 (PaneProbeMixin)
  discovery        discovery across provider stores and merge            (DiscoveryMixin)
  metrics          system CPU / memory metrics                           (SystemMetricsMixin)
  listing          listing, lookup and title resolution                  (ListingMixin)
  actions          focus / rename / send / action dispatcher             (ActionsMixin)
  lifecycle        hibernate / close / idle GC / storage slimming        (LifecycleMixin)
  sleep_blockers   what keeps a session from auto-hibernating            (SleepBlockersMixin)
  integrations     handoff / rules / channels / doctor     (IntegrationsMixin)

The Web daemon runs as `python -m mixhub.web`; the root `server.py` of a source checkout is a compatibility shim.
"""
from __future__ import annotations

from mixhub.hub.constants import (  # noqa: F401
    HEAD_BYTES,
    INDEX_CACHE_SCHEMA_VERSION,
    TAIL_BYTES,
    TAIL_SCAN_MAX_BYTES,
    TAIL_SCAN_STEP_BYTES,
    TOOLS,
    UUID,
)
from mixhub.hub.discovery import (  # noqa: F401
    discover,
)
from mixhub.hub.hub import (  # noqa: F401
    Hub,
)
from mixhub.hub.index import (  # noqa: F401
    SessionIndex,
    SessionRecord,
)
from mixhub.hub.processes import (  # noqa: F401
    parse_processes,
)
from mixhub.hub.schema import (  # noqa: F401
    claude_is_headless_marker,
    claude_is_synthetic_user,
    codex_state_db_candidates,
    PROVIDER_SCHEMA_ADAPTERS,
)
from mixhub.hub.session_filters import (  # noqa: F401
    is_automated_or_subagent_text,
    is_codex_automated,
    is_temp_session,
)
from mixhub.hub.shell import (  # noqa: F401
    apple_string,
    focus_script,
    resume_command,
    run,
)
from mixhub.hub.text import (  # noqa: F401
    clean_prompt,
    printable,
    snippet,
)
from mixhub.hub.titles import (  # noqa: F401
    _is_generic_placeholder_title,
    agy_initial,
    agy_initial_sender,
    claude_reverse_title_scan,
    claude_title_chunk_fields,
    title_fields,
)
from mixhub.hub.transcript import (  # noqa: F401
    _is_structured_wait_entry,
    classify_tail_entries,
    entries,
    json_rows,
    sample_rows,
)
from mixhub.hub.util import (  # noqa: F401
    _db_file_sig,
    _file_sig,
    clean_hostname,
    read_json,
    stamp,
)
from mixhub.proc.classify import AGENT_CLI_NAMES, dispatch_script_names  # noqa: F401
from mixhub.proc.guard import safe_kill_process  # noqa: F401
from mixhub.proc.identity import ProcessIdentity, ProcNode  # noqa: F401
from mixhub.proc.tree import build_proc_tree, has_agent_ancestor  # noqa: F401

__all__ = [
    "AGENT_CLI_NAMES",
    "HEAD_BYTES",
    "Hub",
    "INDEX_CACHE_SCHEMA_VERSION",
    "PROVIDER_SCHEMA_ADAPTERS",
    "ProcNode",
    "ProcessIdentity",
    "SessionIndex",
    "SessionRecord",
    "TAIL_BYTES",
    "TAIL_SCAN_MAX_BYTES",
    "TAIL_SCAN_STEP_BYTES",
    "TOOLS",
    "UUID",
    "_db_file_sig",
    "_file_sig",
    "_is_generic_placeholder_title",
    "_is_structured_wait_entry",
    "agy_initial",
    "agy_initial_sender",
    "apple_string",
    "build_proc_tree",
    "classify_tail_entries",
    "claude_is_headless_marker",
    "claude_is_synthetic_user",
    "claude_reverse_title_scan",
    "claude_title_chunk_fields",
    "clean_hostname",
    "clean_prompt",
    "codex_state_db_candidates",
    "discover",
    "dispatch_script_names",
    "entries",
    "focus_script",
    "has_agent_ancestor",
    "is_automated_or_subagent_text",
    "is_codex_automated",
    "is_temp_session",
    "json_rows",
    "parse_processes",
    "printable",
    "read_json",
    "resume_command",
    "run",
    "safe_kill_process",
    "sample_rows",
    "snippet",
    "stamp",
    "title_fields",
]
