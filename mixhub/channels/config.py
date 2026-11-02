"""Channel configuration: where channels.json lives, Preferences lookup, the optional sender command and topic routing.

`~/.config/mixhub/channels.json` holds one section per channel:

    {
      "telegram": {"bot_token": "123:abc", "chat_id": -1001234567890,
                   "topics": {"builds": 42}, "default_topic": "builds",
                   "routing_rules": {"builds": ["deploy", "release"]}},
      "slack":    {"webhook_url": "https://hooks.slack.com/services/..."},
      "webhook":  {"url": "https://example.com/hook", "headers": {"Authorization": "Bearer ..."}}
    }

A section missing there is read from the older `~/.config/session-hub/channels.json`, and the Telegram section
from `~/.config/tg-notify/config.json` (that whole file is the Telegram section).
"""
from __future__ import annotations

import json
import os

from mixhub.core.prefs import load_preferences


USER_CHANNELS_CONFIG = os.path.join("~", ".config", "mixhub", "channels.json")
LEGACY_CHANNELS_CONFIG = os.path.join("~", ".config", "session-hub", "channels.json")
LEGACY_TG_CONFIG = os.path.join("~", ".config", "tg-notify", "config.json")
CHANNEL_SECTIONS: tuple[str, ...] = ("telegram", "slack", "webhook")
DEFAULT_TG_TOPICS: dict[str, int] = {"general": 1}  # Telegram's General topic: no message_thread_id


def load_hub_preferences(preferences_path: str | None = None) -> dict:
    """`Preferences` as a dict (`{}` if missing or malformed): `preferences_path` when given, else the one lookup in
    `mixhub.core.prefs`."""
    return load_preferences(preferences_path)


def _read_json(path: str) -> dict:
    try:
        with open(os.path.expanduser(path), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load_channel_section(path: str | None, section: str) -> dict:
    """The `section` of the config file at `path`; a file without any channel section is a whole Telegram section."""
    if not path:
        return {}
    data = _read_json(path)
    if isinstance(data.get(section), dict):
        return data[section]
    if section == "telegram" and data and not any(k in data for k in CHANNEL_SECTIONS):
        return data
    return {}


def channel_config_candidates(section: str, home: str | None = None) -> list[str]:
    """Config files that may hold `section`, first match wins."""
    base = os.path.expanduser(home) if home else os.path.expanduser("~")

    def under_home(rel: str) -> str:
        return os.path.join(base, os.path.relpath(os.path.expanduser(rel), os.path.expanduser("~")))

    paths = [under_home(USER_CHANNELS_CONFIG), under_home(LEGACY_CHANNELS_CONFIG)]
    if section == "telegram":
        paths.append(under_home(LEGACY_TG_CONFIG))
    return paths


def resolve_channel_config_path(section: str, home: str | None = None) -> str:
    """The config file `section` is read from: the first candidate that has it, else `~/.config/mixhub/channels.json`."""
    candidates = channel_config_candidates(section, home)
    for path in candidates:
        if os.path.isfile(path) and load_channel_section(path, section):
            return path
    return candidates[0]


def resolve_tgnotify_bin(preferences_path: str | None = None, telegram_config: dict | None = None) -> str:
    """
    Optional external command that sends Telegram messages (`<bin> -t <topic> [--alert] [--file F] <text>`), used
    only when the user configures one, in priority order:
      1. `MIXHUB_TGNOTIFY_BIN` environment variable
      2. `"tgnotify_bin"` in the Telegram section of channels.json
      3. `"tgnotify_bin"` in Preferences
    Empty when there is none: messages then go straight to the Bot API.
    """
    env_bin = os.environ.get("MIXHUB_TGNOTIFY_BIN", "").strip()
    if env_bin:
        return os.path.expanduser(env_bin)
    for source in (telegram_config or {}, load_hub_preferences(preferences_path)):
        pref_bin = str(source.get("tgnotify_bin") or "").strip()
        if pref_bin:
            return os.path.expanduser(pref_bin)
    return ""


def _coerce_topic_map(raw_topics) -> dict[str, int]:
    if isinstance(raw_topics, dict) and raw_topics:
        return {
            str(k): int(v)
            for k, v in raw_topics.items()
            if isinstance(v, (int, str)) and str(v).isdigit()
        }
    if isinstance(raw_topics, list) and raw_topics:
        return {
            str(item).strip(): idx
            for idx, item in enumerate(raw_topics, 1)
            if str(item).strip()
        }
    return {}


def resolve_tg_topics(
    config_path: str | None = None,
    preferences_path: str | None = None,
) -> dict[str, int]:
    """
    Resolve the Telegram topic map (name -> message_thread_id) from:
      1. `MIXHUB_TG_TOPICS` environment variable (JSON dict or list)
      2. `"topics"` in the Telegram section of `config_path` (default: the resolved channels config)
      3. `"tg_topics"` in Preferences
      4. `{"general": 1}`: the chat itself, no topic
    """
    env_topics = os.environ.get("MIXHUB_TG_TOPICS", "").strip()
    if env_topics:
        try:
            parsed_env = _coerce_topic_map(json.loads(env_topics))
            if parsed_env:
                return parsed_env
        except ValueError:
            pass

    path = config_path if config_path is not None else resolve_channel_config_path("telegram")
    parsed_cfg = _coerce_topic_map(load_channel_section(path, "telegram").get("topics"))
    if parsed_cfg:
        return parsed_cfg

    parsed_pref = _coerce_topic_map(load_hub_preferences(preferences_path).get("tg_topics"))
    if parsed_pref:
        return parsed_pref
    return dict(DEFAULT_TG_TOPICS)


def resolve_topic_routing_rules(
    config_path: str | None = None,
    preferences_path: str | None = None,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """The user's keyword rules (topic -> keywords) from the Telegram config (`routing_rules`) or Preferences
    (`tg_routing_rules`). None by default."""
    rules: list[tuple[str, tuple[str, ...]]] = []
    seen_topics: set[str] = set()
    path = config_path if config_path is not None else resolve_channel_config_path("telegram")
    for source_dict in (load_channel_section(path, "telegram"), load_hub_preferences(preferences_path)):
        raw_rules = source_dict.get("routing_rules") or source_dict.get("tg_routing_rules")
        if not isinstance(raw_rules, dict):
            continue
        for tname, kw_list in raw_rules.items():
            if tname not in seen_topics and isinstance(kw_list, (list, tuple)):
                clean_kws = tuple(str(k) for k in kw_list if str(k).strip())
                if clean_kws:
                    rules.append((str(tname), clean_kws))
                    seen_topics.add(str(tname))
    return tuple(rules)
