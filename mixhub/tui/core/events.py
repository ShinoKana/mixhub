#!/usr/bin/env python3
"""
`mixhub.tui.core.events`: Unified TUI event hierarchy (`Mouse`, `Key`, `Resize`, `FocusIn`, `FocusOut`,
`FileChanged`, `Tick`, `DraggedPath`).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Union

MouseKind = Literal["click", "double", "release", "motion", "drag", "wheel_up", "wheel_down"]


@dataclass(frozen=True)
class Mouse:
    kind: MouseKind
    x: int
    y: int
    button: int = 0


@dataclass(frozen=True)
class Key:
    code: int


@dataclass(frozen=True)
class Resize:
    pass


@dataclass(frozen=True)
class FocusIn:
    pass


@dataclass(frozen=True)
class FocusOut:
    """Emitted on `?1004h` CSI `\\x1b[O` or outer tmux `pane_active` focus loss."""


@dataclass(frozen=True)
class FileChanged:
    path: str


@dataclass(frozen=True)
class Tick:
    now_mono: float


@dataclass(frozen=True)
class DraggedPath:
    path: str


Event = Union[Mouse, Key, Resize, FocusIn, FocusOut, FileChanged, Tick, DraggedPath]

__all__ = [
    "DraggedPath",
    "Event",
    "FileChanged",
    "FocusIn",
    "FocusOut",
    "Key",
    "Mouse",
    "MouseKind",
    "Resize",
    "Tick",
]
