"""`mixhub.web`: the loopback HTTP API (`handler.Handler`) and its daemon entry point (`server.main`)."""
from __future__ import annotations

from mixhub.hub.hub import Hub  # noqa: F401
from mixhub.web.handler import Handler  # noqa: F401
from mixhub.web.server import main  # noqa: F401

__all__ = [
    "Handler",
    "Hub",
    "main",
]
