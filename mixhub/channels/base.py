"""RemoteChannel: shared base class for the notification channels."""
from __future__ import annotations

import json
import urllib.request


class RemoteChannel:
    """A one-way notification channel: `send` a message, report whether it `is_configured`."""

    name: str = "base"

    @staticmethod
    def _http_post(url: str, payload: dict, headers: dict | None = None) -> int:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "MixHub/2.0", **(headers or {})},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status

    def is_configured(self) -> bool:
        return False

    def send(
        self,
        message: str,
        session: dict | None = None,
        topic: str | None = None,
        dry_run: bool = False,
        **kwargs,
    ) -> dict:
        raise NotImplementedError
