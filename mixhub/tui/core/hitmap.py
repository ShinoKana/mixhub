#!/usr/bin/env python3
"""
`mixhub.tui.core.hitmap`: Render-time mouse hit registration (`Hit` + `HitMap`).

Replaces ad-hoc coordinate recalculation in mouse handlers: widgets register clickable `Rect`s during
`render()`, and mouse dispatch queries `HitMap.at(x, y)` (highest `z` wins; later registration breaks ties).
"""
from __future__ import annotations

from dataclasses import dataclass

from mixhub.tui.core.geometry import Rect


@dataclass(frozen=True)
class Hit:
    rect: Rect
    action: str
    payload: object = None
    z: int = 0


class HitMap:
    """Z-ordered collection of clickable regions registered during a frame."""

    def __init__(self) -> None:
        self._hits: list[Hit] = []

    def clear(self) -> None:
        self._hits.clear()

    def add(self, rect: Rect, action: str, payload: object = None, z: int = 0) -> None:
        if not rect.is_empty:
            self._hits.append(Hit(rect=rect, action=action, payload=payload, z=int(z)))

    def at(self, x: int, y: int) -> Hit | None:
        """Return the matching `Hit` at `(x, y)` with highest `z` (latest registered wins ties)."""
        best: Hit | None = None
        for hit in reversed(self._hits):
            if hit.rect.contains(x, y):
                if best is None or hit.z > best.z:
                    best = hit
        return best

    def __len__(self) -> int:
        return len(self._hits)

    def hits(self) -> list[Hit]:
        return list(self._hits)


__all__ = ["Hit", "HitMap"]
