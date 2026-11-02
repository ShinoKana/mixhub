#!/usr/bin/env python3
"""
Compatibility shim and daemon entry point: the hub lives in `mixhub.hub`, the HTTP layer in `mixhub.web`.

`hub start` runs `python -m mixhub.web` (daemons started earlier run this file), and `import server` keeps working for `tui.py`,
`channels.py`, tests and external tools (`server.Hub()`, `server.Hub().listing(...)`, ...). Every name the old
single-file module defined is re-exported from its owning module:

  mixhub.hub.constants        repo root, read sizes, UUID patterns, tool codes, cache schema version
  mixhub.hub.schema           provider storage schema adapters
  mixhub.hub.util             hostname, tolerant JSON, timestamps, file signatures
  mixhub.hub.text             prompt cleaning and snippets
  mixhub.hub.titles           title records, reverse title scan, placeholder titles
  mixhub.hub.transcript       JSONL sampling, entry extraction, tail-state classification
  mixhub.hub.session_filters  automated / subagent / temp session rules
  mixhub.hub.shell            `run`, AppleScript / Terminal helpers, resume commands
  mixhub.hub.opencode         OpenCode SQLite reader
  mixhub.hub.index            `SessionRecord`, `SessionIndex`
  mixhub.hub.processes        `ps` parsing and `ProcessMixin`
  mixhub.hub.hub              `Hub`, composed from the mixins in working_set, summary, processes, pane_probe,
                              discovery, metrics, listing, actions, lifecycle, sleep_blockers, integrations
  mixhub.web.handler          `Handler` (loopback HTTP API)
  mixhub.web.server           `main`

Module-level state is owned by those modules: patch the owning module (e.g. `mixhub.hub.constants.ROOT`,
`mixhub.hub.actions.run`), not this one.
"""
from __future__ import annotations

# Standard-library modules reached through this module (`server.os.kill`, `server.http.server.ThreadingHTTPServer`);
# patching an attribute on them patches the standard-library module itself.
import http.server  # noqa: F401
import os  # noqa: F401
import sqlite3  # noqa: F401
import subprocess  # noqa: F401
import time  # noqa: F401
import zlib  # noqa: F401

from mixhub.hub.constants import (  # noqa: F401
    _OPEN_LOCK_PATTERNS,
    _OPEN_PATH_PATTERNS,
    _ROLLOUT_UUID_RE,
    _SUBAGENT_CREATED_RE,
    _SUBAGENT_INVOKE_RE,
    _UUID_RE,
    HEAD_BYTES,
    INDEX_CACHE_SCHEMA_VERSION,
    ROOT,
    TAIL_BYTES,
    TAIL_SCAN_MAX_BYTES,
    TAIL_SCAN_STEP_BYTES,
    TOOLS,
    UUID,
    WORKING_SET_IDLE_EXPIRE_SEC,
)
from mixhub.hub.hub import (  # noqa: F401
    get_system_resource_metrics,
    Hub,
)
from mixhub.hub.index import (  # noqa: F401
    SessionIndex,
    SessionRecord,
)
from mixhub.hub.opencode import (  # noqa: F401
    _opencode_connect,
    _OPENCODE_SESSIONS_SQL,
    opencode_entries,
    opencode_session_rows,
)
from mixhub.hub.processes import (  # noqa: F401
    _proc_start_epoch,
    parse_processes,
)
from mixhub.hub.schema import (  # noqa: F401
    _CODEX_STATE_DB_RE,
    claude_is_headless_marker,
    claude_is_synthetic_user,
    codex_state_db_candidates,
    PROVIDER_SCHEMA_ADAPTERS,
)
from mixhub.hub.session_filters import (  # noqa: F401
    _BUILTIN_AUTOMATED_PREFIXES,
    _BUILTIN_AUTOMATED_SUBSTRINGS,
    _load_local_rules,
    _load_ui_prefs,
    _LOCAL_RULES_CACHE,
    _UI_PREFS_CACHE,
    _ui_prefs_path,
    CODEX_AUTO_PREFIXES,
    is_automated_or_subagent_text,
    is_codex_automated,
    is_temp_session,
)
from mixhub.hub.shell import (  # noqa: F401
    _DEFAULT_RUN,
    apple_string,
    focus_script,
    resume_command,
    run,
)
from mixhub.hub.text import (  # noqa: F401
    _ENVELOPE_TAG_NAMES,
    _ENVELOPE_TAGS_RE,
    _PASTED_BOTH_TAGS_RE,
    _PASTED_CLOSE_RE,
    _PASTED_OPEN_RE,
    _PASTED_OUTER_RE,
    _SNIPPET_TOK_RE,
    _USER_REQUEST_RE,
    _USER_TAG_STRIP_RE,
    clean_prompt,
    printable,
    snippet,
)
from mixhub.hub.titles import (  # noqa: F401
    _GENERIC_PLACEHOLDER_TITLES,
    _is_generic_placeholder_title,
    _PLACEHOLDER_REPEAT_RE,
    agy_initial,
    agy_initial_sender,
    claude_reverse_title_scan,
    claude_title_chunk_fields,
    CLAUDE_TITLE_KEYS,
    CLAUDE_TITLE_MARKERS,
    CLAUDE_TITLE_RECORDS,
    FALLBACK_TITLE_RE,
    title_fields,
    TITLE_SCAN_CHUNK_BYTES,
    TITLE_SCAN_CONTEXT_BYTES,
    TITLE_SCAN_MAX_BYTES,
    TITLE_SCAN_OVERLAP_BYTES,
)
from mixhub.hub.transcript import (  # noqa: F401
    _is_structured_wait_entry,
    _parse_json_lines,
    classify_tail_entries,
    entries,
    json_rows,
    NO_DECISION_RE,
    sample_rows,
    WAIT_QUESTION_RE,
)
from mixhub.hub.util import (  # noqa: F401
    _db_file_sig,
    _file_sig,
    clean_hostname,
    read_json,
    stamp,
)
from mixhub.web.handler import (  # noqa: F401
    Handler,
)
from mixhub.web.server import (  # noqa: F401
    _port_owner_is_hub,
    main,
)


if __name__ == "__main__":
    main()
