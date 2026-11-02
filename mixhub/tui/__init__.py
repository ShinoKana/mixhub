#!/usr/bin/env python3
"""
`mixhub.tui`: Modular TUI core primitives (`core`), widgets (`widgets`), the `HubTUI` app and its helpers.

`HubTUI` lives in `app` and is composed from the per-concern mixins in `hubtui`; the module-level helpers it uses
live in `paths`, `diag`, `settings`, `selection`, `drafts`, `quota`, `badges`, `sidebar_rows`, `web` and `caffeinate`; import those modules directly (`from mixhub.tui.app import HubTUI`). The repo-root `tui.py`
is the entry script and a compatibility re-export of these.
"""
from __future__ import annotations

from mixhub.tui.core import (
    AGE_ROLES,
    App,
    AppCtx,
    Canvas,
    Cell,
    DEFAULT_STYLE,
    DEFAULT_THEME,
    DraggedPath,
    Event,
    EventSource,
    FileChanged,
    FocusIn,
    FocusOut,
    Hit,
    HitMap,
    InputDecoder,
    Key,
    Mouse,
    MouseKind,
    Rect,
    RenderCtx,
    Resize,
    Style,
    Theme,
    Tick,
    Widget,
    classify_sgr_mouse,
)
from mixhub.tui.widgets import (
    Chip,
    PopoverItem,
    PopoverManager,
    PopoverSpec,
    SidebarGrid,
    SidebarRow,
    SplitDivider,
    Wordmark,
    new_session_menu_spec,
    provider_menu_spec,
    relay_menu_spec,
)


__all__ = [
    "AGE_ROLES",
    "App",
    "AppCtx",
    "Canvas",
    "Cell",
    "Chip",
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
    "PopoverItem",
    "PopoverManager",
    "PopoverSpec",
    "Rect",
    "RenderCtx",
    "Resize",
    "SidebarGrid",
    "SidebarRow",
    "SplitDivider",
    "Style",
    "Theme",
    "Tick",
    "Widget",
    "Wordmark",
    "classify_sgr_mouse",
    "new_session_menu_spec",
    "provider_menu_spec",
    "relay_menu_spec",
]
