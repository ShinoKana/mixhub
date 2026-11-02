#!/usr/bin/env python3
"""
Compatibility shim: the SSoT rules compiler and workspace trust live in `mixhub.rules`.

`import rules_sync` keeps working for tests and external tools (`rules_sync.ensure_workspace_trusted`,
`rules_sync.sync_rules`, ...); every name the old single-file module defined is re-exported from its owning module:

  mixhub.rules.blocks  managed `HUB SYNC` blocks, `hub-policy` extraction, sha256 digests
  mixhub.rules.trust   `ensure_workspace_trusted` (Preferences come from `mixhub.core.prefs`)
  mixhub.rules.sync    CLAUDE.md read-only guard, `sync_rules`, `main` (`hub sync-rules`)

Patch the owning module (e.g. `mixhub.rules.sync.ensure_workspace_trusted` to affect `sync_rules`), not this one.
"""
from __future__ import annotations

import sys

from mixhub.rules.blocks import (  # noqa: F401
    BEGIN_RE,
    build_managed_block,
    END_RE,
    extract_hub_policy,
    inspect_managed_block,
    merge_managed_block,
    POLICY_BLOCK_RE,
    sha256_text,
    strip_managed_wrappers,
)
from mixhub.rules.sync import (  # noqa: F401
    _assert_never_write_claude_md,
    _atomic_write,
    _claude_md_to_agents_text,
    _is_claude_md_path,
    _normalize_targets,
    _read_text,
    _TARGET_ALIASES,
    CLAUDE_MD_PROTECTION_NOTICE,
    CLAUDE_MD_READONLY_MSG,
    main,
    sync_rules,
)
from mixhub.rules.trust import (  # noqa: F401
    _atomic_write_json,
    _collect_existing_cli_trusted_roots,
    _is_temp_location,
    _is_within_root,
    _load_trust_preferences,
    ensure_workspace_trusted,
)

if __name__ == "__main__":
    sys.exit(main())
