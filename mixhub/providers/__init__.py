"""
`mixhub.providers`: CLI vendor support (Claude / AGY / Codex / DeepSeek), harness presets, launch and
resume guards, provider readiness, Claude quota windows, and bidirectional native title sync.

Modules:
  preferences      provider-side `Preferences` lookup
  title_overrides  MixHub title overrides (`data/title_overrides.json`) and named sessions
  native_titles    read / write each CLI's native session title
  presets          harness x brain presets and env builders
  launch           launch specs (`build_launch`, `LaunchSpec`)
  resume           guarded resume / thinking-signature fork
  jsonl            tolerant JSONL iteration helpers
  deepseek         DeepSeek key resolution and the CC·DS launch command
  readiness        installed / authenticated / has-sessions state per provider
  claude           Claude Code subprovider (brain) detection
  quota            Claude 5h / weekly quota observations and reset inference

The root `providers.py` is a compatibility shim over these modules.
"""
from __future__ import annotations

from mixhub.providers.claude import (  # noqa: F401
    _detect_claude_badge_from_text,
    detect_claude_subprovider,
)
from mixhub.providers.deepseek import (  # noqa: F401
    build_cc_ds_launch_cmd,
    is_deepseek_configured,
    load_deepseek_api_key,
)
from mixhub.providers.launch import (  # noqa: F401
    _codex_home,
    build_launch,
    LaunchSpec,
    MissingKeyError,
    ResumeSignatureError,
)
from mixhub.providers.native_titles import (  # noqa: F401
    read_native_title,
    write_native_title,
)
from mixhub.providers.preferences import (  # noqa: F401
    load_provider_preferences,
)
from mixhub.providers.presets import (  # noqa: F401
    brain_of_model,
    env_cc,
    env_codex,
    env_native,
    get_harness_preset,
    Preset,
    target_brain,
)
from mixhub.providers.quota import (  # noqa: F401
    _claude_file_sig,
    _claude_quota_sources,
    _resolve_claude_quota,
    collect_recent_claude_usage_timestamps,
    infer_claude_5h_reset,
    infer_claude_wk_reset,
    merge_quota_obs,
    QuotaObs,
    scan_claude_usage_lines,
)
from mixhub.providers.readiness import (  # noqa: F401
    _agy_real_session_ids,
    _readiness_preset,
    get_provider_readiness_state,
    is_provider_active_and_authenticated,
)
from mixhub.providers.resume import (  # noqa: F401
    check_resume,
    find_session_file,
    fork_without_thinking,
    guarded_resume,
    ResumeCheck,
)
from mixhub.providers.title_overrides import (  # noqa: F401
    load_session_names,
    load_title_overrides,
    normalize_agent_name,
    resolve_overrides_path,
    resolve_session_names_path,
    save_title_override,
)
from mixhub.rules.blocks import (  # noqa: F401
    build_managed_block,
    extract_hub_policy,
    inspect_managed_block,
    merge_managed_block,
    sha256_text,
    strip_managed_wrappers,
)
from mixhub.rules.sync import sync_rules  # noqa: F401
from mixhub.rules.trust import ensure_workspace_trusted  # noqa: F401

__all__ = [
    "LaunchSpec",
    "MissingKeyError",
    "Preset",
    "QuotaObs",
    "ResumeCheck",
    "ResumeSignatureError",
    "_agy_real_session_ids",
    "_claude_file_sig",
    "_claude_quota_sources",
    "_codex_home",
    "_detect_claude_badge_from_text",
    "_readiness_preset",
    "_resolve_claude_quota",
    "brain_of_model",
    "build_cc_ds_launch_cmd",
    "build_launch",
    "build_managed_block",
    "check_resume",
    "collect_recent_claude_usage_timestamps",
    "detect_claude_subprovider",
    "ensure_workspace_trusted",
    "env_cc",
    "env_codex",
    "env_native",
    "extract_hub_policy",
    "find_session_file",
    "fork_without_thinking",
    "get_harness_preset",
    "get_provider_readiness_state",
    "guarded_resume",
    "infer_claude_5h_reset",
    "infer_claude_wk_reset",
    "inspect_managed_block",
    "is_deepseek_configured",
    "is_provider_active_and_authenticated",
    "load_deepseek_api_key",
    "load_provider_preferences",
    "load_session_names",
    "load_title_overrides",
    "merge_managed_block",
    "merge_quota_obs",
    "normalize_agent_name",
    "read_native_title",
    "resolve_overrides_path",
    "resolve_session_names_path",
    "save_title_override",
    "scan_claude_usage_lines",
    "sha256_text",
    "strip_managed_wrappers",
    "sync_rules",
    "target_brain",
    "write_native_title",
]
