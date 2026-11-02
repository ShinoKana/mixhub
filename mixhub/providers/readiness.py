"""Provider readiness: installed / authenticated / has-real-sessions state per harness, and native PTY passthrough."""
from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
from typing import Any

from mixhub.providers.deepseek import load_deepseek_api_key
from mixhub.providers.jsonl import _d, _iter_jsonl
from mixhub.providers.preferences import load_provider_preferences
from mixhub.providers.presets import get_harness_preset, Preset
from mixhub.providers.title_overrides import normalize_agent_name


def _has_session_text(value: Any) -> bool:
    """Ignore empty JSON containers and metadata when checking session content."""
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return False
        if value[0] in "[{":
            try:
                return _has_session_text(json.loads(value))
            except ValueError:
                pass
        return True
    if isinstance(value, list):
        return any(_has_session_text(item) for item in value)
    if isinstance(value, dict):
        return any(
            _has_session_text(value.get(key))
            for key in ("title", "summary", "name", "preview", "text", "content", "message", "messages", "parts")
        )
    return False


def _row_has_real_message(row: dict[str, Any], harness: str) -> bool:
    kind = str(row.get("type") or "").lower()
    if harness == "claude" and kind in ("user", "assistant"):
        return _has_session_text(_d(row.get("message")).get("content"))
    if harness == "codex" and kind == "response_item":
        payload = _d(row.get("payload"))
        return (payload.get("type") == "message" and payload.get("role") in ("user", "assistant")
                and _has_session_text(payload.get("content")))
    if harness == "agy" and kind in ("user_input", "planner_response"):
        return _has_session_text(row.get("content"))
    return False


def _jsonl_has_real_message(path: Path, harness: str) -> bool:
    try:
        for row in _iter_jsonl(path):
            if _row_has_real_message(row, harness):
                return True
    except OSError:
        pass
    return False


def _native_session_ids(root: Path, harness: str) -> set[str]:
    if not root.is_dir():
        return set()
    ids: set[str] = set()
    try:
        for path in root.rglob("*.jsonl"):
            if "subagents" in path.parts or not path.is_file():
                continue
            if harness == "agy" and path.name != "transcript.jsonl":
                continue
            if _jsonl_has_real_message(path, harness):
                ids.add(path.relative_to(root).parts[0] if harness == "agy" else path.stem)
    except OSError:
        pass
    return ids


def _borrowed_session_ids(root: Path, preset: Preset) -> set[str]:
    """Count only history whose recorded routing identifies this borrowed preset."""
    if not root.is_dir():
        return set()
    ids: set[str] = set()
    canonical = preset["key"]
    harness = preset["harness"]
    model = preset.get("default_model") if preset["brain"] != "openrouter" else None
    endpoint = preset.get("base_url")
    try:
        for path in root.rglob("*.jsonl"):
            if "subagents" in path.parts or not path.is_file():
                continue
            has_message = routed_here = False
            for row in _iter_jsonl(path):
                has_message |= _row_has_real_message(row, harness)
                for part in (row, _d(row.get("payload")), _d(row.get("message")), _d(row.get("env"))):
                    if any(str(part.get(key) or "").lower() == canonical for key in (
                        "preset", "preset_id", "MIXHUB_PRESET"
                    )):
                        routed_here = True
                    if endpoint and any(str(part.get(key) or "").rstrip("/") == endpoint.rstrip("/") for key in (
                        "base_url", "ANTHROPIC_BASE_URL"
                    )):
                        routed_here = True
                    if model and str(part.get("model") or "") == model:
                        routed_here = True
                if has_message and routed_here:
                    ids.add(path.stem)
                    break
    except OSError:
        pass
    return ids


def _ro_connect(db: Path) -> sqlite3.Connection | None:
    for q in ("mode=ro", "immutable=1"):
        try:
            con = sqlite3.connect(f"{db.as_uri()}?{q}", uri=True, timeout=1.0)
            con.execute("SELECT 1 FROM sqlite_master LIMIT 1")
            return con
        except sqlite3.Error:
            continue
    return None


def _agy_real_session_ids(root: Path) -> set[str]:
    ids: set[str] = set()
    db = root / "conversation_summaries.db"
    if db.is_file() and (con := _ro_connect(db)):
        with closing(con):
            try:
                cols = {r[1] for r in con.execute("PRAGMA table_info(conversation_summaries)")}
                data_cols = [c for c in ("title", "preview") if c in cols]
                if "conversation_id" in cols and data_cols:
                    for row in con.execute(
                        "SELECT conversation_id, " + ", ".join(data_cols) + " FROM conversation_summaries"
                    ):
                        if row[0] and any(_has_session_text(v) for v in row[1:]):
                            ids.add(str(row[0]))
            except sqlite3.Error:
                pass
    return ids | _native_session_ids(root / "brain", "agy")


def _readiness_preset(provider_or_preset: str) -> Preset:
    try:
        return get_harness_preset(provider_or_preset)
    except KeyError:
        return get_harness_preset(normalize_agent_name(provider_or_preset))


def get_provider_readiness_state(
    provider_or_preset: str,
    *,
    home_dir: str | None = None,
    env: dict[str, str] | None = None,
    preferences_path: str | None = None,
) -> dict[str, Any]:
    """Inspect local CLI, credentials, and real history without launching a process."""
    preset = _readiness_preset(provider_or_preset)
    canonical = preset["key"]
    harness = preset["harness"]
    current_env = dict(os.environ if env is None else env)
    prefs = load_provider_preferences(preferences_path or current_env.get("MIXHUB_PREFERENCES_PATH"))
    home = Path(home_dir).expanduser() if home_dir is not None else Path.home()
    cli_root = {
        "claude": home / ".claude",
        "codex": home / ".codex",
        "agy": home / ".gemini" / "antigravity-cli",
    }[harness]
    binary = "agy" if harness == "agy" else harness
    path = current_env.get("PATH", "")
    installed = cli_root.is_dir() or bool(shutil.which(binary, path=path))

    def has_env_key(key: str | None) -> bool:
        return bool(key and str(current_env.get(key, "")).strip())

    if canonical.startswith(("cc-", "cx-")):
        key = preset.get("env_key_var")
        authenticated = has_env_key(key)
        if not authenticated and key:
            settings = home / ".claude" / "settings.json"
            try:
                settings_env = _d(json.loads(settings.read_text("utf-8"))).get("env")
                authenticated = bool(str(_d(settings_env).get(key) or "").strip())
            except (OSError, ValueError):
                pass
        if not authenticated and key == "DEEPSEEK_API_KEY":
            authenticated = bool(load_deepseek_api_key(home_dir=str(home), env=current_env))
        root = cli_root / ("projects" if harness == "claude" else "sessions")
        session_count = len(_borrowed_session_ids(root, preset))
    else:
        credential_files = {
            "claude": (home / ".claude" / ".credentials.json", home / ".claude.json"),
            "codex": (home / ".codex" / "auth.json",),
            "agy": (cli_root / "conversation_summaries.db",),
        }[harness]
        env_keys = {"claude": ("ANTHROPIC_API_KEY",), "codex": ("OPENAI_API_KEY",),
                    "agy": ("GEMINI_API_KEY",)}[harness]
        def non_empty_file(path: Path) -> bool:
            try:
                return path.is_file() and path.stat().st_size > 0
            except OSError:
                return False

        authenticated = any(has_env_key(key) for key in env_keys) or any(
            non_empty_file(path) for path in credential_files
        )
        if harness == "agy":
            session_count = len(_agy_real_session_ids(cli_root))
        else:
            session_count = len(_native_session_ids(cli_root / "sessions" if harness == "codex" else cli_root / "projects", harness))

    state = ("ready" if installed and authenticated else "history_only" if session_count
             else "installed_unready" if installed else "not_installed")
    def overridden(key: str) -> bool:
        names = prefs.get(key) or []
        return isinstance(names, (list, tuple, set)) and any(
            str(name).strip().lower() in (canonical, harness) for name in names
        )
    if overridden("always_hide_providers"):
        state = "installed_unready" if installed else "not_installed"
    elif overridden("always_show_providers"):
        state = "ready" if authenticated else "history_only"
    return {
        "provider": canonical,
        "state": state,
        "active": state in ("ready", "history_only"),
        "installed": installed,
        "authenticated": authenticated,
        "non_empty_session_count": session_count,
        "native_pty_login_passthrough": True,
    }


def is_provider_active_and_authenticated(
    provider_or_preset: str,
    *,
    home_dir: str | None = None,
    env: dict[str, str] | None = None,
    preferences_path: str | None = None,
) -> bool:
    """Return the provider's visibility after local readiness and manual overrides."""
    return bool(get_provider_readiness_state(
        provider_or_preset, home_dir=home_dir, env=env, preferences_path=preferences_path
    )["active"])


