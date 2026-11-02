"""
`mixhub.channels`: optional one-way notifications to Telegram / Slack / a webhook.

MixHub does not relay chat back into sessions or take approvals from a chat app: agent CLIs have their own remote
control for that. These channels only tell you something happened.

Modules (in dependency order):
  config       channels.json lookup, Preferences, optional sender command, Telegram topic routing rules
  text         plain-text sanitizer, mobile cards
  base         RemoteChannel base class
  telegram     Telegram (Bot API sendMessage / sendDocument)
  slack        Slack (incoming webhook)
  webhook      any URL that takes a JSON POST
  manager      ChannelManager, what the hub and `mixhub notify` send through
  hooks        official Claude Code / Codex hook config snippets
  cli          `mixhub notify` entry point
"""
from __future__ import annotations

from mixhub.channels.config import (
    load_hub_preferences,
    resolve_channel_config_path,
    resolve_tg_topics,
    resolve_tgnotify_bin,
    resolve_topic_routing_rules,
)
from mixhub.channels.text import format_mobile_card, sanitize_plain_text
from mixhub.channels.base import RemoteChannel
from mixhub.channels.telegram import TelegramChannel
from mixhub.channels.slack import SlackChannel
from mixhub.channels.webhook import WebhookChannel
from mixhub.channels.hooks import generate_official_hook_config
from mixhub.channels.manager import ChannelManager
from mixhub.channels.cli import main

# Public alias kept from the package API.
run_channels_cli = main

__all__ = [
    "ChannelManager",
    "RemoteChannel",
    "SlackChannel",
    "TelegramChannel",
    "WebhookChannel",
    "format_mobile_card",
    "generate_official_hook_config",
    "load_hub_preferences",
    "main",
    "resolve_channel_config_path",
    "resolve_tg_topics",
    "resolve_tgnotify_bin",
    "resolve_topic_routing_rules",
    "run_channels_cli",
    "sanitize_plain_text",
]
