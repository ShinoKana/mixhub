#!/usr/bin/env python3
"""
`mixhub.tui.core.widget`: `Widget` protocol and render/application contexts (`RenderCtx`, `AppCtx`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from mixhub.tui.core.canvas import Canvas
from mixhub.tui.core.events import Event
from mixhub.tui.core.hitmap import HitMap
from mixhub.tui.core.style import DEFAULT_THEME, Theme


@dataclass
class RenderCtx:
    h: int
    w: int
    left_w: int = 32
    list_top: int = 3
    list_bottom: int = 37
    hx: int = -1
    hy: int = -1
    now_mono: float = 0.0
    sidebar_only: bool = False
    reader_only: bool = False
    header_only: bool = False
    theme: Theme = field(default_factory=lambda: DEFAULT_THEME)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class AppCtx:
    app: Any = None
    now_mono: float = 0.0
    theme: Theme = field(default_factory=lambda: DEFAULT_THEME)
    extra: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class Widget(Protocol):
    def render(self, cv: Canvas, hm: HitMap, ctx: RenderCtx) -> None:
        ...

    def handle(self, ev: Event, ctx: AppCtx) -> bool:
        ...


__all__ = ["AppCtx", "RenderCtx", "Widget"]
