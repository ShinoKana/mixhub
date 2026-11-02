#!/usr/bin/env python3
"""
Compatibility shim: the terminal blocker probe lives in `mixhub.probe`.

`import probe` keeps working for tests and external tools (`probe.WORKING_RE`, `probe.probe_session_pane`, ...);
every name the old single-file module defined is re-exported from its owning module:

  mixhub.probe.patterns  tail sizes, `DEFAULT_PROBE_RULES_PATH`, built-in rule tables, screen regexes
  mixhub.probe.rules     rule compilation and `load_probe_rules`
  mixhub.probe.text      `strip_ansi`, `clean_terminal_line`, `redact_secrets`, raw excerpts
  mixhub.probe.capture   `tmux` runner, `resolve_pool_pane`, `capture_pane(s)`
  mixhub.probe.menus     numbered menus, header / command, fingerprints, hotkey approval boxes
  mixhub.probe.classify  `classify_pane_text` (`probe_pane_state`), `probe_session_pane`

Patch the owning module (e.g. `mixhub.probe.capture._run_cmd`), not this one.
"""
from __future__ import annotations

from mixhub.probe.capture import (  # noqa: F401
    capture_pane,
    capture_panes,
    _captured_pane_result,
    _default_runner,
    resolve_pool_pane,
    _run_cmd,
    _tmux_bin,
)
from mixhub.probe.classify import (  # noqa: F401
    classify_pane_text,
    probe_pane_state,
    probe_session_pane,
)
from mixhub.probe.menus import (  # noqa: F401
    _compute_fingerprint,
    _extract_header_and_command,
    _find_numbered_menu,
    _match_hotkey_approval_box,
)
from mixhub.probe.patterns import (  # noqa: F401
    AGY_HOTKEY_PAIR_RE,
    AGY_NEEDS_APPROVAL_RE,
    ANSI_CSI_RE,
    ANSI_OSC_RE,
    BARE_COMPOSER_PROMPT_RE,
    BOX_EDGE_RE,
    BUILTIN_PROBE_RULES,
    CLAUDE_HEAD_RE,
    CMD_DOLLAR_RE,
    COMPLETED_VERB_RE,
    CONTEXT_LINES,
    DEFAULT_PROBE_RULES_PATH,
    EXCERPT_LINES,
    FOOTER_HINT_RE,
    GEMINI_EXEC_RE,
    IDLE_STATUS_BAR_RE,
    INLINE_HOTKEY_RE,
    MARKDOWN_LABEL_RE,
    NESTED_QUANTIFIER_RE,
    OPT_DENY_RE,
    OPT_ONCE_RE,
    OPT_SESSION_RE,
    OPT_TRUST_DENY_RE,
    OPTION_RE,
    PERM_Q_RE,
    PROMPT_RE,
    QUESTION_HINT_RE,
    RULE_KEY_ALIASES,
    RULE_RE,
    SECRET_REDACT_RE,
    SPINNER_RE,
    STRUCTURAL_FOOTER_KEYBAR_RE,
    TAIL_LINES,
    TITLE_BUSY_RE,
    TOOL_OUTPUT_RE,
    TRANSCRIPT_RE,
    TRUST_RE,
    WORKING_RE,
    YN_RE,
)
from mixhub.probe.rules import (  # noqa: F401
    _BUILTIN_COMPILED_RULES,
    _compile_pattern_list,
    _compile_rules_dict,
    load_probe_rules,
    _match_any,
    _RULES_CACHE,
)
from mixhub.probe.text import (  # noqa: F401
    _build_raw_excerpt,
    clean_terminal_line,
    redact_secrets,
    strip_ansi,
)
