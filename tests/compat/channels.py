#!/usr/bin/env python3
"""Compatibility shim: the one-way notification channels live in `mixhub.channels`.

Every name below is re-exported so `import channels` keeps working for the tests; patch the owning
`mixhub.channels.<module>` attribute, not this module.
"""
# ruff: noqa: F401  (re-export shim)
from __future__ import annotations

from mixhub.channels.config import (
    DEFAULT_TG_TOPICS,
    _coerce_topic_map,
    channel_config_candidates,
    load_channel_section,
    load_hub_preferences,
    resolve_channel_config_path,
    resolve_tg_topics,
    resolve_tgnotify_bin,
    resolve_topic_routing_rules,
)
from mixhub.channels.text import HTML_TAG_RE, TABLE_LINE_RE, format_mobile_card, sanitize_plain_text
from mixhub.channels.base import RemoteChannel
from mixhub.channels.telegram import TelegramChannel
from mixhub.channels.slack import SlackChannel
from mixhub.channels.webhook import WebhookChannel
from mixhub.channels.hooks import generate_official_hook_config
from mixhub.channels.manager import ChannelManager
from mixhub.channels.cli import main
