"""Harness x brain presets (S / A tier endpoints, badges, model slots) and per-harness env builders."""
from __future__ import annotations

import re
from typing import Callable, Literal, TypedDict


# ───────────────────────── (1) 2026 S/A-Tier Harness × Brain Matrix ─────────────────────────

Tier = Literal["S", "A"]
Harness = Literal["claude", "codex", "agy"]
EnvBuilder = Callable[["Preset", "str | None", str], dict[str, str]]


class Preset(TypedDict, total=False):
    key: str
    tier: Tier
    harness: Harness
    brain: str
    badge: str
    base_url: str | None
    alt_base_urls: dict[str, str]
    env_key_var: str | None
    default_model: str | None
    small_fast_model: str | None
    launch_env_builder: EnvBuilder
    wire_api: str | None  # codex custom providers only: "chat" | "responses"


ANTHROPIC_VARS = (
    "ANTHROPIC_BASE_URL",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_SMALL_FAST_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_DEFAULT_SONNET_MODEL",
    "ANTHROPIC_DEFAULT_HAIKU_MODEL",
    "CLAUDE_CODE_SUBAGENT_MODEL",
)
_MODEL_VARS = ANTHROPIC_VARS[3:]
_SECRET_VARS = frozenset({"ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY"})


def env_native(p: Preset, key: str | None, model: str) -> dict[str, str]:
    return {}


def env_cc(auth: Literal["token", "api_key"] = "token") -> EnvBuilder:
    """Claude Code harness -> Anthropic-compatible third-party brain (all 6 model slots mapped)."""
    def build(p: Preset, key: str | None, model: str) -> dict[str, str]:
        eff_model = model or p.get("default_model") or ""
        fast_model = p.get("small_fast_model") or eff_model
        env = {
            "ANTHROPIC_BASE_URL": p.get("base_url") or "",
            "ANTHROPIC_AUTH_TOKEN": (key or "") if auth == "token" else "",
            "ANTHROPIC_API_KEY": (key or "") if auth == "api_key" else "",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        }
        if eff_model:
            for mv in _MODEL_VARS:
                env[mv] = fast_model if mv in ("ANTHROPIC_SMALL_FAST_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL") else eff_model
        if p.get("brain") == "deepseek":
            env["API_TIMEOUT_MS"] = "600000"
        return env
    return build


def env_codex(p: Preset, key: str | None, model: str) -> dict[str, str]:
    kv = p.get("env_key_var")
    return {kv: key} if kv and key else {}


def _p(
    key_id: str,
    tier: Tier,
    harness: Harness,
    brain: str,
    badge: str,
    base_url: str | None = None,
    key: str | None = None,
    model: str | None = None,
    builder: EnvBuilder = env_native,
    wire: str | None = None,
    alt_base_urls: dict[str, str] | None = None,
    small_fast_model: str | None = None,
) -> Preset:
    return Preset(
        key=key_id,
        tier=tier,
        harness=harness,
        brain=brain,
        badge=badge,
        base_url=base_url,
        alt_base_urls=alt_base_urls or {},
        env_key_var=key,
        default_model=model,
        small_fast_model=small_fast_model or model,
        launch_env_builder=builder,
        wire_api=wire,
    )


HARNESS_BRAIN_PRESETS: dict[str, Preset] = {
    # ── S tier ──
    "claude": _p(
        "claude", "S", "claude", "anthropic", "Claude",
        "https://api.anthropic.com", "ANTHROPIC_API_KEY", "claude-sonnet-4-6", env_native,
    ),
    "codex": _p(
        "codex", "S", "codex", "openai", "Codex",
        "https://api.openai.com/v1", "OPENAI_API_KEY", "gpt-5-codex", env_native,
    ),
    "agy": _p(
        "agy", "S", "agy", "gemini", "AGY",
        "", "GEMINI_API_KEY", "gemini-2.5-pro", env_native,
    ),
    "cc-deepseek": _p(
        "cc-deepseek", "S", "claude", "deepseek", "CC·DS",
        "https://api.deepseek.com/anthropic", "DEEPSEEK_API_KEY", "deepseek-chat", env_cc(),
        small_fast_model="deepseek-chat",
    ),
    "cc-kimi": _p(
        "cc-kimi", "S", "claude", "kimi", "CC·Kimi",
        "https://api.moonshot.ai/anthropic", "MOONSHOT_API_KEY", "kimi-k2-turbo-preview", env_cc(),
        alt_base_urls={"intl": "https://api.moonshot.ai/anthropic", "cn": "https://api.moonshot.cn/anthropic"},
    ),
    "cc-openrouter": _p(
        "cc-openrouter", "S", "claude", "openrouter", "CC·OR",
        "https://openrouter.ai/api", "OPENROUTER_API_KEY", "anthropic/claude-sonnet-4.6", env_cc(),
    ),
    # ── A tier ──
    "cc-qwen": _p(
        "cc-qwen", "A", "claude", "qwen", "CC·Qwen",
        "https://dashscope-intl.aliyuncs.com/apps/anthropic", "DASHSCOPE_API_KEY", "qwen3-coder-plus", env_cc(),
        alt_base_urls={
            "intl": "https://dashscope-intl.aliyuncs.com/apps/anthropic",
            "cn": "https://dashscope.aliyuncs.com/apps/anthropic",
        },
    ),
    "cc-glm": _p(
        "cc-glm", "A", "claude", "glm", "CC·GLM",
        "https://api.z.ai/api/anthropic", "ZAI_API_KEY", "glm-4.6", env_cc(),
        alt_base_urls={
            "intl": "https://api.z.ai/api/anthropic",
            "cn": "https://open.bigmodel.cn/api/anthropic",
        },
    ),
    "cx-deepseek": _p(
        "cx-deepseek", "A", "codex", "deepseek", "CX·DS",
        "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY", "deepseek-chat", env_codex, "chat",
    ),
    "cx-openrouter": _p(
        "cx-openrouter", "A", "codex", "openrouter", "CX·OR",
        "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "anthropic/claude-sonnet-4.6", env_codex, "chat",
    ),
}

_BRAIN_RX: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (b, re.compile(rx, re.I))
    for b, rx in (
        ("anthropic", r"claude|anthropic/"),
        ("deepseek", r"deepseek"),
        ("kimi", r"kimi|moonshot"),
        ("qwen", r"qwen|dashscope"),
        ("glm", r"glm|z-ai/|zhipu|bigmodel"),
        ("gemini", r"gemini|google/"),
        ("openai", r"^(gpt|o\d|codex)|openai/"),
    )
)


def get_harness_preset(preset_id: str) -> Preset:
    """Lookup a preset by key or alias (e.g. `'cc-ds'` -> `'cc-deepseek'`)."""
    raw = str(preset_id or "").strip().lower()
    aliases = {
        "cc-ds": "cc-deepseek",
        "cc·ds": "cc-deepseek",
        "cc·kimi": "cc-kimi",
        "cc·or": "cc-openrouter",
        "cc·qwen": "cc-qwen",
        "cc·glm": "cc-glm",
        "cx-ds": "cx-deepseek",
        "cx·ds": "cx-deepseek",
        "cx·or": "cx-openrouter",
    }
    canonical = aliases.get(raw, raw)
    if canonical not in HARNESS_BRAIN_PRESETS:
        raise KeyError(f"Unknown harness preset: {preset_id}")
    return HARNESS_BRAIN_PRESETS[canonical]


def brain_of_model(model: str | None) -> str | None:
    if not model:
        return None
    return next((b for b, rx in _BRAIN_RX if rx.search(model)), None)


def target_brain(preset_id: str, model: str | None = None) -> str:
    """Resolve the upstream brain for `preset_id` (resolving OpenRouter from the model slug)."""
    p = get_harness_preset(preset_id)
    m = model or p.get("default_model")
    if p["brain"] == "openrouter":
        return brain_of_model(m) or "openrouter"
    return p["brain"]
