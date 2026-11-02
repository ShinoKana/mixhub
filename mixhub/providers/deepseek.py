"""DeepSeek API key resolution (env / `~/.config/deepseek/env`) and the CC·DS launch command."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex

from mixhub.providers.jsonl import _d


_DEEPSEEK_ENV_LINE_RE = re.compile(r"^\s*(?:export\s+)?DEEPSEEK_API_KEY\s*=\s*(.*?)\s*$")


def load_deepseek_api_key(home_dir: str | None = None, env: dict[str, str] | None = None) -> str:
    """
    Resolve `DEEPSEEK_API_KEY` from (1) `env` / `os.environ`, (2) `~/.config/deepseek/env`
    (`export DEEPSEEK_API_KEY=...` or `DEEPSEEK_API_KEY=...`, quotes stripped), (3) `~/.claude/settings.json` `env`.
    Returns `""` when no key is configured.
    """
    current_env = os.environ if env is None else env
    key = str(current_env.get("DEEPSEEK_API_KEY") or "").strip()
    if key:
        return key
    home = Path(home_dir).expanduser() if home_dir is not None else Path.home()
    try:
        for line in (home / ".config" / "deepseek" / "env").read_text("utf-8").splitlines():
            m = _DEEPSEEK_ENV_LINE_RE.match(line)
            if not m:
                continue
            val = m.group(1)
            if len(val) >= 2 and val[0] == val[-1] and val[0] in "'\"":
                val = val[1:-1]
            if val.strip():
                return val.strip()
    except (OSError, UnicodeDecodeError):
        pass
    try:
        settings_env = _d(json.loads((home / ".claude" / "settings.json").read_text("utf-8"))).get("env")
        return str(_d(settings_env).get("DEEPSEEK_API_KEY") or "").strip()
    except (OSError, ValueError):
        return ""


def is_deepseek_configured(home_dir: str | None = None, env: dict[str, str] | None = None) -> bool:
    """True when a DeepSeek API key is resolvable via `load_deepseek_api_key`."""
    return bool(load_deepseek_api_key(home_dir=home_dir, env=env))


def build_cc_ds_launch_cmd(
    session_id: str = "",
    *,
    cwd: str = "",
    model: str = "deepseek-chat",
    small_fast_model: str = "deepseek-chat",
    base_url: str = "https://api.deepseek.com/anthropic",
    fork_session: bool = False,
    prompt: str = "",
    claude_config_dir: str = "~/.claude",
    extra_args: list[str] | None = None,
) -> str:
    """
    Build a process-isolated launch command for `CC·DS` (DeepSeek running inside the Claude Code harness)
    while sharing `~/.claude` (`CLAUDE.md`, `skills`, `hooks`, `memory`, and session history
    for `--resume <id> --fork-session` lossless brain-switching).
    """
    eff_model = str(model or "deepseek-chat").strip()
    eff_fast = str(small_fast_model or "deepseek-chat").strip()
    eff_url = str(base_url or "https://api.deepseek.com/anthropic").strip()
    env_parts = [
        "-u",
        "ANTHROPIC_API_KEY",
        f"ANTHROPIC_BASE_URL={shlex.quote(eff_url)}",
        'ANTHROPIC_AUTH_TOKEN="${DEEPSEEK_API_KEY:-${ANTHROPIC_AUTH_TOKEN:-}}"',
        f"ANTHROPIC_MODEL={shlex.quote(eff_model)}",
        f"ANTHROPIC_SMALL_FAST_MODEL={shlex.quote(eff_fast)}",
        f"ANTHROPIC_DEFAULT_OPUS_MODEL={shlex.quote(eff_model)}",
        f"ANTHROPIC_DEFAULT_SONNET_MODEL={shlex.quote(eff_model)}",
        f"ANTHROPIC_DEFAULT_HAIKU_MODEL={shlex.quote(eff_fast)}",
        f"CLAUDE_CODE_SUBAGENT_MODEL={shlex.quote(eff_fast)}",
    ]
    if claude_config_dir and claude_config_dir not in ("~/.claude", os.path.expanduser("~/.claude")):
        env_parts.append(f"CLAUDE_CONFIG_DIR={shlex.quote(os.path.expanduser(claude_config_dir))}")

    cmd_parts = ["env", *env_parts, "claude"]
    sid = str(session_id or "").strip()
    if sid:
        cmd_parts.extend(["--resume", shlex.quote(sid)])
        if fork_session:
            cmd_parts.append("--fork-session")
    elif fork_session:
        cmd_parts.append("--fork-session")

    for arg in extra_args or []:
        if str(arg).strip():
            cmd_parts.append(shlex.quote(str(arg).strip()))

    if prompt:
        cmd_parts.extend(["--", shlex.quote(str(prompt))])

    cmd_str = " ".join(cmd_parts)
    if cwd:
        return f"cd {shlex.quote(os.path.expanduser(cwd))} && {cmd_str}"
    return cmd_str
