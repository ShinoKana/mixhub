#!/usr/bin/env python3
"""`mixhub.tui.widgets.divider`: Draggable vertical split divider (`│` / `⋮`) widget."""
from __future__ import annotations

from mixhub.tui.core.canvas import Canvas
from mixhub.tui.core.events import Event
from mixhub.tui.core.geometry import Rect
from mixhub.tui.core.hitmap import HitMap
from mixhub.tui.core.widget import AppCtx, RenderCtx, Widget


class SplitDivider(Widget):
    def __init__(self, x: int, dragging: bool = False):
        self.x = x
        self.dragging = dragging

    def render(self, cv: Canvas, hm: HitMap, ctx: RenderCtx) -> None:
        is_hov = self.dragging or (ctx.hy >= 1 and abs(ctx.hx - self.x) <= 1)
        line_st = ctx.theme.style("divider", "hover" if is_hov else "rest")
        handle_st = ctx.theme.style("divider.handle", "hover" if is_hov else "rest")
        mid_y = ctx.h // 2
        handle_rows = (mid_y - 1, mid_y, mid_y + 1)
        for ry in range(1, max(1, ctx.h - 1)):
            cv.put(ry, self.x, "⋮" if ry in handle_rows else "│", handle_st if ry in handle_rows else line_st)
        hm.add(Rect(1, max(0, self.x - 1), max(0, ctx.h - 2), 3), "divider:drag", z=2)

    def handle(self, ev: Event, ctx: AppCtx) -> bool:
        return False


__all__ = ["SplitDivider"]
