"""Launch specs: build the argv / env / settings layer that starts a harness with a given brain preset."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Sequence

from mixhub.providers.presets import _SECRET_VARS, ANTHROPIC_VARS, get_harness_preset, target_brain


# ───────────────── (2) Process-Level Isolated Launch + Cross-Brain Resume Guard ─────────────────


class MissingKeyError(RuntimeError):
    """Raised when a borrowed-brain preset requires an API key that is not present."""


class ResumeSignatureError(RuntimeError):
    """Raised when resuming across brains would replay foreign signed thinking blocks (HTTP 400)."""


@dataclass
class LaunchSpec:
    argv: list[str]
    env: dict[str, str]
    cwd: str | None = None
    temp_files: list[str] = field(default_factory=list)
    preset_id: str = ""
    brain: str = ""

    def cleanup(self) -> None:
        """Delete temporary `--settings` overlay files after child process exits."""
        for f in self.temp_files:
            Path(f).unlink(missing_ok=True)

    def __enter__(self) -> LaunchSpec:
        return self

    def __exit__(self, *_: Any) -> None:
        self.cleanup()


def _claude_home(home_dir: str | None = None) -> Path:
    if home_dir:
        return Path(os.path.expanduser(home_dir)) / ".claude"
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def _codex_home(home_dir: str | None = None) -> Path:
    if home_dir:
        return Path(os.path.expanduser(home_dir)) / ".codex"
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


def _settings_env_keys(cwd: str | None, home_dir: str | None = None) -> set[str]:
    """Read-only inspection of env keys defined in user/project settings that could shadow process env."""
    chome = _claude_home(home_dir)
    files = [chome / "settings.json", chome / "settings.local.json"]
    if cwd:
        files += [Path(cwd) / ".claude" / "settings.json", Path(cwd) / ".claude" / "settings.local.json"]
    keys: set[str] = set()
    for f in files:
        try:
            env = json.loads(f.read_text("utf-8")).get("env") or {}
            keys |= set(env) if isinstance(env, dict) else set()
        except (OSError, ValueError, AttributeError):
            continue
    return keys


def _settings_layer(overlay: dict[str, str], cwd: str | None, spec: LaunchSpec, home_dir: str | None = None) -> str:
    """
    Create a temporary `--settings` JSON file (0600).
    CLI `--settings` layer overrides local/project/user settings so `~/.claude/settings.json` is NEVER modified.
    Secrets are written to the temp file only when a user/project settings file defines a conflicting key;
    otherwise they remain strictly in process `env`.
    """
    conflicts = _settings_env_keys(cwd, home_dir=home_dir)
    layer = {k: v for k, v in overlay.items() if k not in _SECRET_VARS or not v or k in conflicts}
    fd, path = tempfile.mkstemp(prefix="mixhub-cc-", suffix=".json")
    os.chmod(path, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"env": layer}, f)
    spec.temp_files.append(path)
    return path


def build_launch(
    preset_id: str,
    *,
    model: str | None = None,
    cwd: str | None = None,
    resume: str | None = None,
    extra_args: Sequence[str] = (),
    api_key: str | None = None,
    base_env: dict[str, str] | None = None,
    region: str | None = None,
    require_api_key: bool = True,
    use_settings_file: bool = True,
    home_dir: str | None = None,
) -> LaunchSpec:
    """
    Build a process-isolated `LaunchSpec` for any S-tier or A-tier preset without modifying
    `~/.claude/settings.json` or `~/.codex/config.toml`.
    """
    p = dict(get_harness_preset(preset_id))
    canonical_id = p.get("key") or preset_id
    if region and p.get("alt_base_urls") and region in p["alt_base_urls"]:
        p["base_url"] = p["alt_base_urls"][region]

    env = dict(os.environ if base_env is None else base_env)
    eff_model = model or p.get("default_model")
    kv = p.get("env_key_var")
    is_borrowed = canonical_id not in ("claude", "codex", "agy")
    key = api_key or (env.get(kv) if kv else None)
    if kv and not key and require_api_key and is_borrowed:
        raise MissingKeyError(f"{canonical_id}: ${kv} is not set")

    # Never leak a previous brain's ANTHROPIC_* routing variables into the child process
    for v in ANTHROPIC_VARS:
        env.pop(v, None)

    overlay = p["launch_env_builder"](p, key, eff_model or "") if is_borrowed else {}
    for k, v in list(overlay.items()):
        if v:
            env[k] = v
    resolved_brain = target_brain(canonical_id, eff_model)
    env["MIXHUB_PRESET"] = canonical_id
    env["MIXHUB_BRAIN"] = resolved_brain
    spec = LaunchSpec([], env, cwd, preset_id=canonical_id, brain=resolved_brain)

    harness = p["harness"]
    if harness == "claude":
        argv = ["claude"]
        if overlay and use_settings_file:
            argv += ["--settings", _settings_layer(overlay, cwd, spec, home_dir=home_dir)]
        if eff_model and is_borrowed:
            argv += ["--model", eff_model]
        if resume:
            argv += ["--resume", resume]
    elif harness == "codex":
        argv = ["codex", *(["resume", resume] if resume else [])]
        if is_borrowed and p.get("base_url"):
            pid = "mixhub_" + canonical_id.replace("-", "_")
            for k, v in (
                ("name", p["badge"]),
                ("base_url", p["base_url"]),
                ("env_key", kv),
                ("wire_api", p.get("wire_api") or "chat"),
            ):
                if v is not None:
                    argv += ["-c", f"model_providers.{pid}.{k}={json.dumps(v)}"]
            argv += ["-c", f"model_provider={json.dumps(pid)}"]
        if eff_model and is_borrowed:
            argv += ["-m", eff_model]
    else:
        argv = ["agy", *(["--resume", resume] if resume else [])]

    spec.argv = argv + list(extra_args)
    return spec
