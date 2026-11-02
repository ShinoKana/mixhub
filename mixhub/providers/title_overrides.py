"""MixHub-side session title overrides (`data/title_overrides.json`) and named sessions (`session_names.json`)."""
from __future__ import annotations

import json
import os
import re

from mixhub.core import paths
from mixhub.core.env import env


# In-memory cache of title overrides keyed by `(normalized_agent, session_id)` so callers
# invoking `write_native_title` then `read_native_title` without explicit `path` stay consistent.
_RUNTIME_TITLE_OVERRIDES: dict[tuple[str, str], str] = {}

_ENVELOPE_TAGS = (
    r"environment_context|INSTRUCTIONS|ADDITIONAL_METADATA|SYSTEM_MESSAGE|system-reminder|"
    r"ide_opened_file|ide_selection|command-name|command-message|command-args|"
    r"local-command-caveat|local-command-stdout|user_instructions|permissions instructions|"
    r"collaboration_mode|cross-session-message|teammate-message|task-notification|turn_aborted"
)


def normalize_agent_name(agent: str) -> str:
    """Normalize CLI vendor aliases into canonical `'claude' | 'agy' | 'codex' | 'deepseek'`."""
    raw = str(agent or "").strip().lower()
    if raw in ("claude", "claude-code", "anthropic", "c"):
        return "claude"
    if raw in ("agy", "gemini", "antigravity", "google", "a"):
        return "agy"
    if raw in ("codex", "openai", "x"):
        return "codex"
    if raw in ("deepseek", "ds"):
        return "deepseek"
    return raw


def _clean_title_text(value: str, agent: str = "") -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if not text:
        return ""
    if text.startswith("Another Claude session sent a message:"):
        return ""
    if agent == "codex" and (
        text.startswith("# AGENTS.md instructions") or text.startswith("<INSTRUCTIONS>")
    ):
        return ""
    req = re.search(r"<USER_REQUEST>(.*?)</USER_REQUEST>", text, re.S | re.I)
    if req:
        text = req.group(1)
    text = re.sub(r"<(" + _ENVELOPE_TAGS + r")\b[^>]*>.*?</\1>", "", text, flags=re.S | re.I)
    text = re.sub(r"</?(?:USER_REQUEST|USER_INPUT)\b[^>]*>", "", text, flags=re.I)
    return " ".join(text.split())[:200]


def _is_temp_path(p: str) -> bool:
    if not p:
        return False
    ap = os.path.abspath(os.path.expanduser(p))
    rp = os.path.realpath(ap)
    prefixes = ("/tmp/", "/private/tmp/", "/var/folders/", "/private/var/folders/")
    return ap.startswith(prefixes) or rp.startswith(prefixes)


def _infer_temp_anchor_dir(path: str) -> str:
    """
    Given a file or directory `path` inside a temporary test directory, determine a stable
    directory for `title_overrides.json` so `write_native_title` and `read_native_title`
    resolve to the same file without polluting the MixHub state `data/` directory.
    """
    ap = os.path.abspath(os.path.expanduser(path))
    parts = ap.split(os.sep)
    if "brain" in parts:
        idx = parts.index("brain")
        if idx > 1:
            return os.sep.join(parts[:idx])
    if os.path.isdir(ap):
        return ap
    return os.path.dirname(ap)


def _data_dir_for_home(home: str, own_home: bool | None = None) -> str:
    """The state `data/` dir that goes with agent home `home`: the state root's for the user's own home, else the
    default state location inside that home (an isolated test home never writes into the real state)."""
    home = os.path.abspath(os.path.expanduser(home))
    if own_home is None:
        own_home = os.path.realpath(home) == os.path.realpath(os.path.expanduser("~"))
    if own_home:
        return os.path.join(paths.STATE_ROOT, "data")
    return os.path.join(home, ".local", "share", "mixhub", "data")


def resolve_overrides_path(
    overrides_path: str | None = None,
    *,
    path: str | None = None,
    home_dir: str | None = None,
    data_dir: str | None = None,
) -> str:
    """Resolve the path to `title_overrides.json` respecting isolated test directories."""
    if overrides_path:
        return os.path.abspath(os.path.expanduser(overrides_path))
    if data_dir:
        return os.path.join(os.path.abspath(os.path.expanduser(data_dir)), "title_overrides.json")
    env_data = env("DATA_DIR")
    if env_data:
        return os.path.join(os.path.abspath(os.path.expanduser(env_data)), "title_overrides.json")
    for home in (home_dir, env("AGENT_HOME")):
        if home:
            return os.path.join(_data_dir_for_home(home), "title_overrides.json")
    if path and _is_temp_path(path):
        anchor = _infer_temp_anchor_dir(path)
        return os.path.join(anchor, "title_overrides.json")
    home_now = os.path.expanduser("~")
    if _is_temp_path(home_now) and not os.environ.get("MIXHUB_HOME"):
        return os.path.join(_data_dir_for_home(home_now, own_home=False), "title_overrides.json")
    return os.path.join(paths.STATE_ROOT, "data", "title_overrides.json")


def load_title_overrides(
    overrides_path: str | None = None,
    *,
    path: str | None = None,
    home_dir: str | None = None,
    data_dir: str | None = None,
) -> dict[str, str]:
    """Load `title_overrides.json` safely, returning `{}` if missing or malformed."""
    ov_file = resolve_overrides_path(
        overrides_path, path=path, home_dir=home_dir, data_dir=data_dir
    )
    if not os.path.isfile(ov_file):
        return {}
    try:
        with open(ov_file, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            titles = data.get("titles") if isinstance(data.get("titles"), dict) else data
            return {str(k): str(v) for k, v in titles.items() if isinstance(v, str)}
    except (OSError, ValueError):
        pass
    return {}


def save_title_override(
    agent: str,
    session_id: str,
    new_title: str,
    overrides_path: str | None = None,
    *,
    path: str | None = None,
    home_dir: str | None = None,
    data_dir: str | None = None,
    dry_run: bool = False,
) -> str:
    """Atomically persist `new_title` into `title_overrides.json` and return its file path."""
    norm_agent = normalize_agent_name(agent)
    sid = str(session_id or "").strip()
    clean_title = _clean_title_text(new_title, norm_agent)
    ov_file = resolve_overrides_path(
        overrides_path, path=path, home_dir=home_dir, data_dir=data_dir
    )
    if not sid or not clean_title:
        return ov_file

    _RUNTIME_TITLE_OVERRIDES[(norm_agent, sid)] = clean_title
    if dry_run:
        return ov_file

    existing: dict[str, str] = {}
    if os.path.isfile(ov_file):
        try:
            with open(ov_file, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                existing = loaded
        except (OSError, ValueError):
            existing = {}

    existing[f"{norm_agent}:{sid}"] = clean_title
    existing[sid] = clean_title

    try:
        os.makedirs(os.path.dirname(ov_file), exist_ok=True)
        tmp_path = f"{ov_file}.tmp.{os.getpid()}"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, ov_file)
    except OSError:
        pass
    return ov_file


def resolve_session_names_path(
    session_names_path: str | None = None,
    *,
    path: str | None = None,
    home_dir: str | None = None,
    data_dir: str | None = None,
) -> str:
    """Resolve the path to `session_names.json` (MixHub non-invasive overlay store)."""
    if session_names_path:
        return os.path.abspath(os.path.expanduser(session_names_path))
    ov_path = resolve_overrides_path(path=path, home_dir=home_dir, data_dir=data_dir)
    return os.path.join(os.path.dirname(ov_path), "session_names.json")


def load_session_names(
    session_names_path: str | None = None,
    *,
    path: str | None = None,
    home_dir: str | None = None,
    data_dir: str | None = None,
) -> dict[str, str]:
    """Load `session_names.json` safely, returning `{}` if missing or malformed."""
    sn_file = resolve_session_names_path(
        session_names_path, path=path, home_dir=home_dir, data_dir=data_dir
    )
    if not os.path.isfile(sn_file):
        return {}
    try:
        with open(sn_file, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            names = data.get("names") if isinstance(data.get("names"), dict) else data
            return {str(k): str(v) for k, v in names.items() if isinstance(v, str)}
    except (OSError, ValueError):
        pass
    return {}


def _lookup_override(
    agent: str,
    session_id: str,
    *,
    overrides_path: str | None = None,
    path: str | None = None,
    home_dir: str | None = None,
    data_dir: str | None = None,
) -> str:
    norm_agent = normalize_agent_name(agent)
    sid = str(session_id or "").strip()
    overrides = load_title_overrides(
        overrides_path, path=path, home_dir=home_dir, data_dir=data_dir
    )
    val = overrides.get(f"{norm_agent}:{sid}") or overrides.get(sid) or ""
    if val:
        return val
    snames = load_session_names(path=path, home_dir=home_dir, data_dir=data_dir)
    val = snames.get(f"{norm_agent}:{sid}") or snames.get(sid) or ""
    if val:
        return val
    # If `path` is a nested file inside a temp dir, also check immediate parent dir
    if path and _is_temp_path(path):
        local_ov = os.path.join(
            os.path.dirname(os.path.abspath(path)) if not os.path.isdir(path) else os.path.abspath(path),
            "title_overrides.json",
        )
        if os.path.isfile(local_ov):
            local_map = load_title_overrides(local_ov)
            val = local_map.get(f"{norm_agent}:{sid}") or local_map.get(sid) or ""
            if val:
                return val
        return _RUNTIME_TITLE_OVERRIDES.get((norm_agent, sid), "")
    return ""
