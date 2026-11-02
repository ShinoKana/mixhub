#!/usr/bin/env python3
"""
`mixhub.tui.core.geometry`: Immutable half-open `Rect(y, x, h, w)` primitive.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    """
    2D half-open rectangle `[x, x + w) × [y, y + h)` in terminal cell coordinates.
    Note: Constructor follows curses `(y, x, h, w)` convention; `contains(x, y)` takes `(x, y)`.
    """

    y: int
    x: int
    h: int
    w: int

    @property
    def right(self) -> int:
        return self.x + max(0, self.w)

    @property
    def bottom(self) -> int:
        return self.y + max(0, self.h)

    @property
    def is_empty(self) -> bool:
        return self.h <= 0 or self.w <= 0

    def contains(self, x: int, y: int) -> bool:
        """Return True if point `(x, y)` lies strictly inside `[self.x, right) × [self.y, bottom)`."""
        if self.h <= 0 or self.w <= 0:
            return False
        return (self.x <= x < self.x + self.w) and (self.y <= y < self.y + self.h)

    def intersect(self, other: "Rect") -> "Rect":
        """Return intersection rectangle (returns an empty `Rect` with `h=0, w=0` when disjoint)."""
        ny = max(self.y, other.y)
        nx = max(self.x, other.x)
        nb = min(self.bottom, other.bottom)
        nr = min(self.right, other.right)
        if nb <= ny or nr <= nx:
            return Rect(ny, nx, 0, 0)
        return Rect(ny, nx, nb - ny, nr - nx)

    def clamp(self, bounds: "Rect") -> "Rect":
        """Clamp this rectangle's position and size to fit inside `bounds`."""
        w = max(0, min(self.w, max(0, bounds.w)))
        h = max(0, min(self.h, max(0, bounds.h)))
        if bounds.w <= 0 or bounds.h <= 0:
            return Rect(bounds.y, bounds.x, 0, 0)
        x = max(bounds.x, min(self.x, bounds.right - w))
        y = max(bounds.y, min(self.y, bounds.bottom - h))
        return Rect(y, x, h, w)


__all__ = ["Rect"]
