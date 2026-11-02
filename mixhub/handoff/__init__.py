"""
`mixhub.handoff`: cross-vendor stateful auditable relay engine.

Modules (in dependency order):
  text         marker regexes, secret redaction, token estimation, transcript cleanup
  resume       effective project-root inference and resume-state classification
  contract     handoff.yaml contract: section titles, targets, YAML dump, mode resolution
  extract      10-section artifact extraction from a session transcript
  bundle       Markdown/YAML bundle rendering and target CLI prompt/command builders
  relay        end-to-end `create_handoff`
  disposition  canonical 5-state session resolution

Root `handoff.py` is a compatibility shim re-exporting these names.
"""
from __future__ import annotations

from mixhub.handoff.text import SECRET_PATTERNS, estimate_tokens, redact_secrets
from mixhub.handoff.resume import (
    RESUME_STATES,
    RESUME_STATE_ACTIONS,
    classify_resume_state,
    infer_effective_root,
)
from mixhub.handoff.contract import (
    HANDOFF_MODES,
    SECTION_TITLES,
    VALID_TARGETS,
    dump_handoff_yaml,
    resolve_handoff_mode,
)
from mixhub.handoff.extract import extract_handoff_artifacts, hub_clean_prompt
from mixhub.handoff.bundle import (
    build_handoff_command,
    build_handoff_prompt,
    render_bundle_markdown,
)
from mixhub.handoff.relay import create_handoff
from mixhub.handoff.disposition import _resolve_session_5state

__all__ = [
    "HANDOFF_MODES",
    "RESUME_STATES",
    "RESUME_STATE_ACTIONS",
    "SECRET_PATTERNS",
    "SECTION_TITLES",
    "VALID_TARGETS",
    "_resolve_session_5state",
    "build_handoff_command",
    "build_handoff_prompt",
    "classify_resume_state",
    "create_handoff",
    "dump_handoff_yaml",
    "estimate_tokens",
    "extract_handoff_artifacts",
    "hub_clean_prompt",
    "infer_effective_root",
    "redact_secrets",
    "render_bundle_markdown",
    "resolve_handoff_mode",
]
