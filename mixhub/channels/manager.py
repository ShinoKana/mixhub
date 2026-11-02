"""ChannelManager: sends one-way notifications to the configured channels and reports their status."""
from __future__ import annotations

import os

from mixhub.channels.base import RemoteChannel
from mixhub.channels.config import resolve_channel_config_path
from mixhub.channels.slack import SlackChannel
from mixhub.channels.telegram import TelegramChannel
from mixhub.channels.webhook import WebhookChannel


def _display_path(path: str) -> str:
    home = os.path.expanduser("~")
    return "~" + path[len(home):] if path and path.startswith(home + os.sep) else path


class ChannelManager:
    """The notification channels (`telegram`, `slack`, `webhook`) behind `mixhub notify` and the web settings."""

    def __init__(
        self,
        tg_config_path: str | None = None,
        channels_config_path: str | None = None,
        tg_runner=None,
        http_sender=None,
        home: str | None = None,
    ):
        self.telegram = TelegramChannel(config_path=tg_config_path or resolve_channel_config_path("telegram", home),
                                        runner=tg_runner)
        self.slack = SlackChannel(config_path=channels_config_path or resolve_channel_config_path("slack", home),
                                  sender=http_sender)
        self.webhook = WebhookChannel(config_path=channels_config_path or resolve_channel_config_path("webhook", home),
                                      sender=http_sender)
        self.adapters: dict[str, RemoteChannel] = {
            "telegram": self.telegram,
            "slack": self.slack,
            "webhook": self.webhook,
        }

    def status(self) -> dict:
        return {
            "ok": True,
            "channels": {
                name: {
                    "configured": adapter.is_configured(),
                    "config_path": _display_path(getattr(adapter, "config_path", "")),
                    **({"topics": self.telegram.topics()} if name == "telegram" else {}),
                }
                for name, adapter in self.adapters.items()
            },
        }

    def notify(
        self,
        message: str,
        channel: str = "telegram",
        session: dict | None = None,
        topic: str | None = None,
        dry_run: bool = False,
        **kwargs,
    ) -> dict:
        """Send through one channel, `all` of them, or `auto`: every configured one (none configured: nothing sent)."""
        target = (channel or "telegram").lower()
        if target in ("all", "auto"):
            names = [n for n, a in self.adapters.items() if target == "all" or a.is_configured()]
            results = {}
            for name in names:
                results[name] = self.adapters[name].send(message, session=session, topic=topic, dry_run=dry_run, **kwargs)
            return {"ok": all(r.get("ok") for r in results.values()), "results": results, "sent": bool(results)}
        if target not in self.adapters:
            raise ValueError(f"Unknown channel: {channel}")
        return self.adapters[target].send(message, session=session, topic=topic, dry_run=dry_run, **kwargs)
