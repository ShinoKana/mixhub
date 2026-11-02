#!/usr/bin/env python3
"""Compatibility shim: the handoff engine lives in `mixhub.handoff`.

Cross-agent 10-section lossless handoff bundle and 3-mode directional handoff.
Every name below is re-exported so `import handoff` keeps working for the tests; patch the owning
`mixhub.handoff.<module>` attribute, not this module.
"""
# ruff: noqa: F401  (re-export shim)
from __future__ import annotations

from mixhub.handoff.text import (
    ACCEPTANCE_RE,
    CONSTRAINT_RE,
    FILE_LINE_RE,
    FILE_RANGE_RE,
    IRREV_RE,
    REJECTED_RE,
    SECRET_PATTERNS,
    UNVERIFIED_RE,
    _clean_verbatim_turn_text,
    _coalesce_markdown_blocks,
    _extract_codex_exec_cmd,
    _snippet,
    _summarize_error_output,
    estimate_tokens,
    redact_secrets,
)
from mixhub.handoff.resume import (
    AWAITING_USER_RE,
    PROJECT_ROOT_MARKERS,
    RESUME_STATES,
    RESUME_STATE_ACTIONS,
    classify_resume_state,
    infer_effective_root,
)
from mixhub.handoff.contract import (
    HANDOFF_MODES,
    SECTION_TITLES,
    VALID_TARGETS,
    _yaml_scalar,
    dump_handoff_yaml,
    resolve_handoff_mode,
)
from mixhub.handoff.extract import (
    _collect_git_metadata,
    extract_handoff_artifacts,
    hub_clean_prompt,
)
from mixhub.handoff.bundle import (
    build_handoff_command,
    build_handoff_prompt,
    render_bundle_markdown,
    render_markdown_bundle,
)
from mixhub.handoff.relay import create_handoff
from mixhub.handoff.disposition import _resolve_session_5state
