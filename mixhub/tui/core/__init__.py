#!/usr/bin/env python3
"""
`mixhub.tui.core`: Core TUI Primitives (`Rect`, `Style`, `Theme`, `Canvas`, `Cell`, `Hit`, `HitMap`,
`Event`, `Widget`, `InputDecoder`, `App`).
"""
from __future__ import annotations

from mixhub.tui.core.canvas import DEFAULT_STYLE, Canvas, Cell
from mixhub.tui.core.events import (
    DraggedPath,
    Event,
    FileChanged,
    FocusIn,
    FocusOut,
    Key,
    Mouse,
    MouseKind,
    Resize,
    Tick,
)
from mixhub.tui.core.geometry import Rect
from mixhub.tui.core.hitmap import Hit, HitMap
from mixhub.tui.core.input import InputDecoder, classify_sgr_mouse
from mixhub.tui.core.loop import App, EventSource
from mixhub.tui.core.style import AGE_ROLES, DEFAULT_THEME, Style, Theme
from mixhub.tui.core.widget import AppCtx, RenderCtx, Widget

__all__ = [
    "AGE_ROLES",
    "App",
    "AppCtx",
    "Canvas",
    "Cell",
    "DEFAULT_STYLE",
    "DEFAULT_THEME",
    "DraggedPath",
    "Event",
    "EventSource",
    "FileChanged",
    "FocusIn",
    "FocusOut",
    "Hit",
    "HitMap",
    "InputDecoder",
    "Key",
    "Mouse",
    "MouseKind",
    "Rect",
    "RenderCtx",
    "Resize",
    "Style",
    "Theme",
    "Tick",
    "Widget",
    "classify_sgr_mouse",
]
