"""Slack channel: one-way notifications through an incoming webhook (`slack.webhook_url` in channels.json)."""
from __future__ import annotations

from mixhub.channels.config import load_channel_section, resolve_channel_config_path
from mixhub.channels.text import sanitize_plain_text
from mixhub.channels.base import RemoteChannel


class SlackChannel(RemoteChannel):
    """Posts plain-text messages to the channel the incoming webhook belongs to."""

    name = "slack"

    def __init__(self, config_path: str | None = None, sender=None):
        self.config_path = config_path or resolve_channel_config_path("slack")
        self.sender = sender

    def load_config(self) -> dict:
        return load_channel_section(self.config_path, "slack")

    def is_configured(self) -> bool:
        return bool(self.load_config().get("webhook_url"))

    def build_payload(self, message: str, session: dict | None = None, file_path: str = "") -> dict:
        title = sanitize_plain_text((session or {}).get("title") or "")
        body = sanitize_plain_text(message)
        text = f"*{title}*\n{body}" if title and title not in body else body
        if file_path:
            text += f"\n(full text: {file_path})"
        return {"text": text[:3000]}

    def send(
        self,
        message: str,
        session: dict | None = None,
        topic: str | None = None,
        dry_run: bool = False,
        file_path: str = "",
        **kwargs,
    ) -> dict:
        payload = self.build_payload(message, session=session, file_path=str(file_path or ""))
        webhook_url = self.load_config().get("webhook_url", "")
        if dry_run or not webhook_url:
            return {"ok": True, "channel": "slack", "dry_run": True, "file_path": str(file_path or ""), "payload": payload}
        send_fn = self.sender or self._http_post
        try:
            status = send_fn(webhook_url, payload)
        except OSError as exc:
            return {"ok": False, "channel": "slack", "dry_run": False, "error": str(exc), "payload": payload}
        return {
            "ok": 200 <= status < 300,
            "channel": "slack",
            "dry_run": False,
            "status": status,
            "file_path": str(file_path or ""),
            "payload": payload,
        }
