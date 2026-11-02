#!/usr/bin/env python3
"""
`mixhub.tui.widgets.chip`: Reusable `Chip` widget registering its `Rect` in `HitMap` during `render()`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from mixhub.core.textwidth import disp_width
from mixhub.tui.core.canvas import Canvas
from mixhub.tui.core.events import Event, Mouse
from mixhub.tui.core.geometry import Rect
from mixhub.tui.core.hitmap import HitMap
from mixhub.tui.core.widget import AppCtx, RenderCtx, Widget


@dataclass
class Chip(Widget):
    y: int
    x: int
    label: str
    action: str
    role: str = "chip"
    enabled: bool = True
    active: bool = False
    z: int = 1
    payload: Any = None
    on_click: Callable[[str, Any], None] | None = None

    @property
    def rect(self) -> Rect:
        return Rect(self.y, self.x, 1, disp_width(self.label))

    def render(self, cv: Canvas, hm: HitMap, ctx: RenderCtx) -> None:
        r = self.rect
        if not self.enabled:
            state = "disabled"
        elif r.contains(ctx.hx, ctx.hy):
            state = "hover"
        elif self.active:
            state = "active"
        else:
            state = "rest"
        st = ctx.theme.style(self.role, state)
        cv.put(self.y, self.x, self.label, st)
        if self.enabled:
            hm.add(r, self.action, payload=self.payload, z=self.z)

    def handle(self, ev: Event, ctx: AppCtx) -> bool:
        if isinstance(ev, Mouse) and ev.kind == "click" and self.enabled and self.rect.contains(ev.x, ev.y):
            if self.on_click is not None:
                self.on_click(self.action, self.payload)
            return True
        return False


__all__ = ["Chip"]
