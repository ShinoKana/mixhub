"""Claude Code subprovider detection: which brain (Anthropic / DeepSeek / ...) a Claude session talks to."""
from __future__ import annotations

import json
import os
from typing import Iterable

from mixhub.providers.presets import ANTHROPIC_VARS, HARNESS_BRAIN_PRESETS


# ───────────────────────── (4) Subprovider Detection & Legacy Helpers ─────────────────────────


def _detect_claude_badge_from_text(text: str) -> str | None:
    low = text.lower()
    if "deepseek" in low or "cc·ds" in low or "cc-ds" in low:
        return "CC·DS"
    if "moonshot" in low or "kimi" in low or "cc·kimi" in low or "cc-kimi" in low:
        return "CC·Kimi"
    if "openrouter.ai" in low or "cc·or" in low or "cc-openrouter" in low:
        return "CC·OR"
    if "dashscope" in low or "qwen" in low or "cc·qwen" in low or "cc-qwen" in low:
        return "CC·Qwen"
    if "bigmodel.cn" in low or "api.z.ai" in low or "glm-" in low or "cc·glm" in low or "cc-glm" in low:
        return "CC·GLM"
    return None


def detect_claude_subprovider(
    session_meta_or_jsonl: dict | str | os.PathLike | Iterable | None = None,
    *,
    env: dict[str, str] | None = None,
) -> str:
    """
    Detect which brain is running inside a Claude Code session across all S/A-tier presets:
      - Returns `"CC·DS"`, `"CC·Kimi"`, `"CC·OR"`, `"CC·Qwen"`, `"CC·GLM"`,
        or `"Claude"` (native Anthropic default).
    """
    if env and isinstance(env, dict):
        preset_env = str(env.get("MIXHUB_PRESET") or "").strip().lower()
        if preset_env in HARNESS_BRAIN_PRESETS and HARNESS_BRAIN_PRESETS[preset_env]["harness"] == "claude":
            return HARNESS_BRAIN_PRESETS[preset_env]["badge"]
        for env_key in ANTHROPIC_VARS:
            badge = _detect_claude_badge_from_text(str(env.get(env_key) or ""))
            if badge:
                return badge

    def _row_badge(row: dict) -> str | None:
        for k in (
            "subprovider",
            "preset",
            "MIXHUB_PRESET",
            "ANTHROPIC_BASE_URL",
            "baseUrl",
            "base_url",
            "model",
            "provider",
            "ANTHROPIC_MODEL",
            "command",
            "cmdline",
        ):
            val = str(row.get(k) or "")
            if val.lower() in HARNESS_BRAIN_PRESETS:
                return HARNESS_BRAIN_PRESETS[val.lower()]["badge"]
            b = _detect_claude_badge_from_text(val)
            if b:
                return b
        msg = row.get("message")
        if isinstance(msg, dict):
            b = _detect_claude_badge_from_text(str(msg.get("model") or ""))
            if b:
                return b
        row_env = row.get("env") or row.get("environment")
        if isinstance(row_env, dict):
            for ev in row_env.values():
                b = _detect_claude_badge_from_text(str(ev or ""))
                if b:
                    return b
        for proc in row.get("processes") or []:
            if isinstance(proc, dict):
                b = _row_badge(proc)
                if b:
                    return b
        return None

    if isinstance(session_meta_or_jsonl, dict):
        b = _row_badge(session_meta_or_jsonl)
        if b:
            return b
        cand_path = session_meta_or_jsonl.get("path")
        if cand_path and isinstance(cand_path, (str, os.PathLike)):
            session_meta_or_jsonl = str(cand_path)
        else:
            return "Claude"

    if isinstance(session_meta_or_jsonl, (str, os.PathLike)):
        raw_str = os.fspath(session_meta_or_jsonl)
        expanded = os.path.expanduser(raw_str)
        if os.path.isfile(expanded):
            try:
                with open(expanded, encoding="utf-8", errors="replace") as f:
                    for idx, line in enumerate(f):
                        if idx > 400:
                            break
                        sline = line.strip()
                        if not sline:
                            continue
                        if _detect_claude_badge_from_text(sline) is None:
                            continue
                        try:
                            row = json.loads(sline)
                            if isinstance(row, dict):
                                b = _row_badge(row)
                                if b:
                                    return b
                        except ValueError:
                            b = _detect_claude_badge_from_text(sline)
                            if b:
                                return b
            except OSError:
                pass
            return "Claude"

        for line in raw_str.splitlines():
            sline = line.strip()
            if not sline:
                continue
            if sline.startswith("{"):
                try:
                    row = json.loads(sline)
                    if isinstance(row, dict):
                        b = _row_badge(row)
                        if b:
                            return b
                except ValueError:
                    b = _detect_claude_badge_from_text(sline)
                    if b:
                        return b
            else:
                b = _detect_claude_badge_from_text(sline)
                if b:
                    return b
        return "Claude"

    if session_meta_or_jsonl is not None and isinstance(session_meta_or_jsonl, Iterable):
        for item in session_meta_or_jsonl:
            res = detect_claude_subprovider(item)
            if res != "Claude":
                return res
        return "Claude"

    if env is None:
        b = _detect_claude_badge_from_text(os.environ.get("ANTHROPIC_BASE_URL", ""))
        if b:
            return b
    return "Claude"
