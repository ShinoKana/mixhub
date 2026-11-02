#!/usr/bin/env python3
"""
Compatibility shim: the provider layer lives in `mixhub.providers`.

`import providers` keeps working for `tui.py`, `rules_sync.py`, tests and external tools; every name
the old single-file module defined is re-exported from its owning module:

  preferences      provider-side `Preferences` lookup
  title_overrides  MixHub title overrides and named sessions
  native_titles    read / write each CLI's native session title
  presets          harness x brain presets and env builders
  launch           launch specs (`build_launch`)
  resume           guarded resume / thinking-signature fork
  jsonl            tolerant JSONL iteration helpers
  deepseek         DeepSeek key resolution and the CC·DS launch command
  readiness        installed / authenticated / has-sessions state per provider
  claude           Claude Code subprovider (brain) detection
  quota            Claude 5h / weekly quota observations and reset inference

Patch the owning module (e.g. `mixhub.providers.deepseek.load_deepseek_api_key`) to affect callers
inside the package; patching an attribute here only affects code that looks it up on this module.
"""
from __future__ import annotations

from mixhub.providers.claude import (  # noqa: F401
    _detect_claude_badge_from_text,
    detect_claude_subprovider,
)
from mixhub.providers.deepseek import (  # noqa: F401
    _DEEPSEEK_ENV_LINE_RE,
    build_cc_ds_launch_cmd,
    is_deepseek_configured,
    load_deepseek_api_key,
)
from mixhub.providers.launch import (  # noqa: F401
    _claude_home,
    _codex_home,
    _settings_env_keys,
    _settings_layer,
    build_launch,
    LaunchSpec,
    MissingKeyError,
    ResumeSignatureError,
)
from mixhub.providers.native_titles import (  # noqa: F401
    _resolve_agy_locations,
    _resolve_claude_jsonl,
    _resolve_codex_jsonl,
    read_native_title,
    write_native_title,
)
from mixhub.providers.preferences import (  # noqa: F401
    load_provider_preferences,
    ROOT,
)
from mixhub.providers.presets import (  # noqa: F401
    _BRAIN_RX,
    _MODEL_VARS,
    _p,
    _SECRET_VARS,
    ANTHROPIC_VARS,
    brain_of_model,
    env_cc,
    env_codex,
    env_native,
    EnvBuilder,
    get_harness_preset,
    Harness,
    HARNESS_BRAIN_PRESETS,
    Preset,
    target_brain,
    Tier,
)
from mixhub.providers.quota import (  # noqa: F401
    _claude_file_sig,
    _CLAUDE_LIMIT_HIT_CACHE,
    _CLAUDE_LIMIT_KIND_RE,
    _claude_quota_sources,
    _CLAUDE_USAGE_TAIL_CACHE,
    _quota_epoch,
    _rem_pct_from_fraction,
    _resolve_claude_quota,
    CLAUDE_5H_ALIGN,
    CLAUDE_5H_SECONDS,
    CLAUDE_SAME_WIN_TOL,
    CLAUDE_STATUSLINE_JSON,
    CLAUDE_WK_SECONDS,
    collect_recent_claude_usage_timestamps,
    infer_claude_5h_reset,
    infer_claude_wk_reset,
    latest_claude_limit_hits,
    merge_quota_obs,
    QuotaObs,
    scan_claude_usage_lines,
)
from mixhub.providers.readiness import (  # noqa: F401
    _agy_real_session_ids,
    _borrowed_session_ids,
    _has_session_text,
    _jsonl_has_real_message,
    _native_session_ids,
    _readiness_preset,
    _row_has_real_message,
    get_provider_readiness_state,
    is_provider_active_and_authenticated,
)
from mixhub.providers.resume import (  # noqa: F401
    _is_signed_block,
    check_resume,
    find_session_file,
    fork_without_thinking,
    guarded_resume,
    ResumeCheck,
)
from mixhub.providers.title_overrides import (  # noqa: F401
    _clean_title_text,
    _ENVELOPE_TAGS,
    _infer_temp_anchor_dir,
    _is_temp_path,
    _lookup_override,
    _RUNTIME_TITLE_OVERRIDES,
    load_session_names,
    load_title_overrides,
    normalize_agent_name,
    resolve_overrides_path,
    resolve_session_names_path,
    save_title_override,
)
from rules_sync import (  # noqa: F401
    ensure_workspace_trusted,
)


__all__ = [
    "ANTHROPIC_VARS",
    "HARNESS_BRAIN_PRESETS",
    "LaunchSpec",
    "MissingKeyError",
    "Preset",
    "QuotaObs",
    "ResumeCheck",
    "ResumeSignatureError",
    "_claude_quota_sources",
    "_resolve_claude_quota",
    "brain_of_model",
    "build_cc_ds_launch_cmd",
    "build_launch",
    "check_resume",
    "collect_recent_claude_usage_timestamps",
    "detect_claude_subprovider",
    "ensure_workspace_trusted",
    "env_cc",
    "env_codex",
    "env_native",
    "find_session_file",
    "fork_without_thinking",
    "get_harness_preset",
    "get_provider_readiness_state",
    "guarded_resume",
    "infer_claude_5h_reset",
    "infer_claude_wk_reset",
    "is_provider_active_and_authenticated",
    "load_provider_preferences",
    "load_session_names",
    "load_title_overrides",
    "merge_quota_obs",
    "normalize_agent_name",
    "read_native_title",
    "resolve_overrides_path",
    "resolve_session_names_path",
    "save_title_override",
    "scan_claude_usage_lines",
    "target_brain",
    "write_native_title",
]
