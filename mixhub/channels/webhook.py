"""Generic webhook channel: POSTs each notification as JSON to `webhook.url` in channels.json."""
from __future__ import annotations

from mixhub.channels.base import RemoteChannel
from mixhub.channels.config import load_channel_section, resolve_channel_config_path
from mixhub.channels.text import sanitize_plain_text


class WebhookChannel(RemoteChannel):
    """Sends `{"text", "title", "session_id", "tool", "topic"}` with the configured extra `headers`."""

    name = "webhook"

    def __init__(self, config_path: str | None = None, sender=None):
        self.config_path = config_path or resolve_channel_config_path("webhook")
        self.sender = sender

    def load_config(self) -> dict:
        return load_channel_section(self.config_path, "webhook")

    def is_configured(self) -> bool:
        return bool(self.load_config().get("url"))

    def send(
        self,
        message: str,
        session: dict | None = None,
        topic: str | None = None,
        dry_run: bool = False,
        **kwargs,
    ) -> dict:
        session = session or {}
        payload = {
            "text": sanitize_plain_text(message),
            "title": sanitize_plain_text(session.get("title") or ""),
            "session_id": str(session.get("id") or ""),
            "tool": str(session.get("tool") or ""),
            "topic": topic or "",
        }
        cfg = self.load_config()
        url = str(cfg.get("url") or "")
        if dry_run or not url:
            return {"ok": True, "channel": "webhook", "dry_run": True, "payload": payload}
        headers = cfg.get("headers") if isinstance(cfg.get("headers"), dict) else {}
        send_fn = self.sender or self._http_post
        try:
            status = send_fn(url, payload, {str(k): str(v) for k, v in headers.items()})
        except OSError as exc:
            return {"ok": False, "channel": "webhook", "dry_run": False, "error": str(exc), "payload": payload}
        return {"ok": 200 <= status < 300, "channel": "webhook", "dry_run": False, "status": status, "payload": payload}
