#!/usr/bin/env python3
"""
`mixhub.tui.core.loop`: `EventSource` protocol and `App` widget dispatcher/render coordinator.
"""
from __future__ import annotations

from typing import Any, Callable, Protocol, Sequence

from mixhub.tui.core.canvas import Canvas
from mixhub.tui.core.events import Event
from mixhub.tui.core.hitmap import HitMap
from mixhub.tui.core.style import DEFAULT_THEME, Theme
from mixhub.tui.core.widget import AppCtx, RenderCtx, Widget


class EventSource(Protocol):
    def wait(self, timeout: float) -> list[Event]:
        ...


class App:
    """
    Lightweight widget dispatcher and Canvas+HitMap coordinator.
    Dispatch order:
      1. `popover_manager` (if open or handling focus/escape)
      2. `focused_widget` (if set)
      3. `widgets` in registration order
      4. `fallback_handler` (global keys)
    """

    def __init__(
        self,
        stdscr: Any = None,
        widgets: Sequence[Widget] = (),
        popover_manager: Widget | None = None,
        theme: Theme = DEFAULT_THEME,
        fallback_handler: Callable[[Event, AppCtx], bool] | None = None,
    ):
        self.stdscr = stdscr
        self.widgets: list[Widget] = list(widgets)
        self.popover_manager = popover_manager
        self.focused_widget: Widget | None = None
        self.theme = theme
        self.fallback_handler = fallback_handler
        self.hitmap = HitMap()
        self.canvas: Canvas | None = None

    def dispatch(self, ev: Event, ctx: AppCtx | None = None) -> bool:
        app_ctx = ctx or AppCtx(app=self, theme=self.theme)
        if self.popover_manager is not None and self.popover_manager.handle(ev, app_ctx):
            return True
        if self.focused_widget is not None and self.focused_widget.handle(ev, app_ctx):
            return True
        for w in self.widgets:
            if w is self.focused_widget:
                continue
            if w.handle(ev, app_ctx):
                return True
        if self.fallback_handler is not None:
            return bool(self.fallback_handler(ev, app_ctx))
        return False

    def render(self, ctx: RenderCtx) -> Canvas:
        cv = Canvas(ctx.h, ctx.w)
        hm = HitMap()
        for w in self.widgets:
            w.render(cv, hm, ctx)
        if self.popover_manager is not None:
            self.popover_manager.render(cv, hm, ctx)
        self.canvas = cv
        self.hitmap = hm
        if self.stdscr is not None:
            cv.flush(self.stdscr)
        return cv


__all__ = ["App", "EventSource"]
