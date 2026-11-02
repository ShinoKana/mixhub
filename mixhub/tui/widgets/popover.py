#!/usr/bin/env python3
"""
`mixhub.tui.widgets.popover`: Unified `PopoverItem`, `PopoverSpec`, and `PopoverManager`
for all four floating sidebar popovers (`row`, `relay`, `new_session`, `provider`).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

try:
    import curses
except ImportError:  # pragma: no cover
    curses = None  # type: ignore[assignment]

from mixhub.core.textwidth import disp_pad, disp_width
from mixhub.tui.core.canvas import Canvas
from mixhub.tui.core.events import Event, FocusOut, Key, Mouse
from mixhub.tui.core.geometry import Rect
from mixhub.tui.core.hitmap import HitMap
from mixhub.tui.core.style import (
    P_AGENT_OPENCODE,
    P_AGENT_OPENCODE_SEL,
    P_AGENT_AGY,
    P_AGENT_AGY_SEL,
    P_AGENT_CLAUDE,
    P_AGENT_CLAUDE_SEL,
    P_AGENT_CODEX,
    P_AGENT_CODEX_SEL,
    P_DONE,
    P_DONE_SEL,
    P_META,
    P_UNSEL,
    P_USER_BOX,
    Style,
)
from mixhub.tui.core.widget import AppCtx, RenderCtx, Widget

POPOVER_W = 22
PROVIDER_CARD_Y = 1
PROVIDER_CARD_X = 4
NEW_SESSION_CARD_Y = 2

PROVIDER_FILTER_ITEMS: tuple[tuple[str | None, str, int, int], ...] = (
    (None, "All", P_UNSEL, P_USER_BOX),
    ("claude", "Claude Code", P_AGENT_CLAUDE, P_AGENT_CLAUDE_SEL),
    ("codex", "Codex", P_AGENT_CODEX, P_AGENT_CODEX_SEL),
    ("opencode", "OpenCode", P_AGENT_OPENCODE, P_AGENT_OPENCODE_SEL),
    ("gemini", "Gemini", P_AGENT_AGY, P_AGENT_AGY_SEL),
)

NEW_SESSION_ITEMS: tuple[tuple[str, str, int, int], ...] = (
    ("claude", "Claude Code", P_AGENT_CLAUDE, P_AGENT_CLAUDE_SEL),
    ("codex", "Codex", P_AGENT_CODEX, P_AGENT_CODEX_SEL),
    ("opencode", "OpenCode", P_AGENT_OPENCODE, P_AGENT_OPENCODE_SEL),
    ("agy", "Gemini", P_AGENT_AGY, P_AGENT_AGY_SEL),
)

RELAY_TO_ITEMS: tuple[tuple[str, str, int, int], ...] = (
    ("claude", "Claude Code", P_AGENT_CLAUDE, P_AGENT_CLAUDE_SEL),
    ("agy", "Gemini", P_AGENT_AGY, P_AGENT_AGY_SEL),
    ("codex", "Codex", P_AGENT_CODEX, P_AGENT_CODEX_SEL),
    ("deepseek", "DeepSeek", P_DONE, P_DONE_SEL),
)

PROVIDER_HOTKEYS: dict[int, str | None] = {
    ord("1"): None, ord("a"): None, ord("A"): None,
    ord("2"): "claude", ord("c"): "claude", ord("C"): "claude",
    ord("3"): "codex", ord("x"): "codex", ord("X"): "codex",
    ord("4"): "gemini", ord("g"): "gemini", ord("G"): "gemini",
    ord("5"): "deepseek", ord("d"): "deepseek", ord("D"): "deepseek",
}

NEW_SESSION_HOTKEYS: dict[int, str] = {
    ord("1"): "claude", ord("c"): "claude", ord("C"): "claude",
    ord("2"): "codex", ord("x"): "codex", ord("X"): "codex",
    ord("3"): "agy", ord("g"): "agy", ord("G"): "agy",
    ord("4"): "deepseek", ord("d"): "deepseek", ord("D"): "deepseek",
    10: "claude", 13: "claude",
}

RELAY_HOTKEYS: dict[int, str] = {
    ord("1"): "claude", ord("c"): "claude", ord("C"): "claude",
    ord("2"): "agy", ord("g"): "agy", ord("G"): "agy",
    ord("3"): "codex", ord("x"): "codex", ord("X"): "codex",
    ord("4"): "deepseek", ord("d"): "deepseek", ord("D"): "deepseek",
}


def popover_card_x(left_w: int, width: int = POPOVER_W) -> int:
    return max(2, min(int(left_w) - int(width) - 1, 4))


def relay_to_card_y(list_top: int, list_bottom: int, n_items: int = len(RELAY_TO_ITEMS)) -> int:
    return max(list_top, min(list_top + 2, list_bottom - n_items - 2))


@dataclass(frozen=True)
class PopoverItem:
    key: str | None
    label: str
    role: str = "menu.item"
    enabled: bool = True
    checked: bool = False
    rest_pair: int = P_UNSEL
    hov_pair: int = P_USER_BOX
    right_badge: str = ""


@dataclass(frozen=True)
class PopoverSpec:
    kind: str  # "row" | "relay" | "new_session" | "provider"
    items: tuple[PopoverItem, ...]
    width: int = POPOVER_W
    anchor: Callable[[RenderCtx], tuple[int, int]] = field(
        default=lambda ctx: (1, popover_card_x(ctx.left_w, POPOVER_W))
    )
    title: str | None = None
    hotkeys: Mapping[int, str | None] = field(default_factory=dict)
    close_on_focus_out: bool = True
    keep_tmux_focus: bool = True


def relay_menu_spec() -> PopoverSpec:
    items = tuple(
        PopoverItem(key=k, label=lbl, rest_pair=rp, hov_pair=hp)
        for k, lbl, rp, hp in RELAY_TO_ITEMS
    )
    return PopoverSpec(
        kind="relay",
        items=items,
        width=POPOVER_W,
        anchor=lambda ctx: (
            relay_to_card_y(ctx.list_top, ctx.list_bottom, len(items)),
            popover_card_x(ctx.left_w, POPOVER_W),
        ),
        title="⇄ Relay To",
        hotkeys=RELAY_HOTKEYS,
    )


def new_session_menu_spec() -> PopoverSpec:
    items = tuple(
        PopoverItem(key=k, label=lbl, rest_pair=rp, hov_pair=hp)
        for k, lbl, rp, hp in NEW_SESSION_ITEMS
    )
    return PopoverSpec(
        kind="new_session",
        items=items,
        width=POPOVER_W,
        anchor=lambda ctx: (NEW_SESSION_CARD_Y, popover_card_x(ctx.left_w, POPOVER_W)),
        title="New Session",
        hotkeys=NEW_SESSION_HOTKEYS,
    )


def provider_menu_spec(active_key: str | None = None) -> PopoverSpec:
    items = tuple(
        PopoverItem(
            key=k,
            label=lbl,
            rest_pair=rp,
            hov_pair=hp,
            checked=(k == active_key),
            right_badge="✓" if k == active_key else "",
        )
        for k, lbl, rp, hp in PROVIDER_FILTER_ITEMS
    )
    return PopoverSpec(
        kind="provider",
        items=items,
        width=POPOVER_W,
        anchor=lambda ctx: (PROVIDER_CARD_Y, popover_card_x(ctx.left_w, POPOVER_W)),
        title="Filter",
        hotkeys=PROVIDER_HOTKEYS,
    )


def draw_popover_card_to_stdscr(
    stdscr: Any,
    card_y: int,
    card_x: int,
    card_w: int,
    title: str,
    items: Sequence[tuple[str | None, str, int, int, str]],
    hx: int,
    hy: int,
    left_w: int,
) -> None:
    """
    Shared 1-path curses drawer for floating sidebar popovers (`Filter` / `New Session` / `⋯` / `⇄ Relay To`).
    Blanks covered rows across `[0, left_w - 1)` first so 2-column CJK characters underneath are never split.
    """
    if stdscr is None or curses is None:
        return
    blank = " " * max(1, left_w - 1)
    try:
        for ry in range(card_y, card_y + len(items) + 2):
            stdscr.addstr(ry, 0, blank, curses.color_pair(P_UNSEL))
        if title:
            top = "╭─ " + title + " " + ("─" * max(1, card_w - disp_width(title) - 5)) + "╮"
        else:
            top = "╭" + ("─" * (card_w - 2)) + "╮"
        stdscr.addstr(card_y, card_x, top, curses.color_pair(P_META))
        for idx, (_, label, rest_pair, hov_sel_pair, right_badge) in enumerate(items):
            iy = card_y + 1 + idx
            is_hov = hy == iy and card_x <= hx < card_x + card_w
            attr = (curses.color_pair(hov_sel_pair) | curses.A_BOLD) if is_hov else curses.color_pair(rest_pair)
            stdscr.addstr(iy, card_x, "│", curses.color_pair(P_META))
            stdscr.addstr(iy, card_x + 1, f" {disp_pad(label, card_w - 6)} {right_badge or ' '} ", attr)
            stdscr.addstr(iy, card_x + card_w - 1, "│", curses.color_pair(P_META))
        stdscr.addstr(card_y + len(items) + 1, card_x, "╰" + ("─" * (card_w - 2)) + "╯", curses.color_pair(P_META))
    except Exception:
        pass


class PopoverManager(Widget):
    """
    Single state machine, renderer, HitMap registrar, hotkey handler, and `Esc`/`FocusOut` handler
    for floating sidebar popovers.
    """

    def __init__(self, on_focus_request: Callable[[], None] | None = None):
        self._spec: PopoverSpec | None = None
        self._on_pick: Callable[[Any, Any], None] | None = None
        self._payload: Any = None
        self._opened_mono: float = 0.0
        self._last_rect: Rect | None = None
        self._last_left_w: int = 32
        self._on_focus_request = on_focus_request

    def open(
        self,
        spec: PopoverSpec,
        on_pick: Callable[[Any, Any], None],
        payload: Any = None,
    ) -> None:
        self._spec = spec
        self._on_pick = on_pick
        self._payload = payload
        self._opened_mono = time.monotonic()
        if spec.keep_tmux_focus and self._on_focus_request is not None:
            self._on_focus_request()

    def close(self) -> bool:
        was_open = self._spec is not None
        self._spec = None
        self._on_pick = None
        self._payload = None
        self._last_rect = None
        return was_open

    def is_open(self, kind: str | None = None) -> bool:
        if self._spec is None:
            return False
        if kind is None:
            return True
        return self._spec.kind == kind

    def render(self, cv: Canvas, hm: HitMap, ctx: RenderCtx) -> None:
        spec = self._spec
        if spec is None:
            return
        card_y, card_x = spec.anchor(ctx)
        card_w = spec.width
        n_items = len(spec.items)
        card_h = n_items + 2
        self._last_rect = Rect(card_y, card_x, card_h, card_w)
        self._last_left_w = ctx.left_w

        # Full-screen backdrop at z=9 so outside clicks close the popover
        hm.add(Rect(0, 0, max(1, ctx.h), max(1, ctx.w)), "pop:__backdrop__", payload=spec.kind, z=9)

        # Blank covered sidebar rows across `[0, left_w - 1)` to repair any split CJK wide characters underneath
        blank_style = Style(P_UNSEL, bold=False)
        cv.fill(Rect(card_y, 0, card_h, max(1, ctx.left_w - 1)), " ", blank_style)

        # Card border & items
        border_style = ctx.theme.style("menu.border", "rest")
        hm.add(self._last_rect, "pop:__border__", payload=spec.kind, z=10)

        if spec.title:
            top = "╭─ " + spec.title + " " + ("─" * max(1, card_w - disp_width(spec.title) - 5)) + "╮"
        else:
            top = "╭" + ("─" * (card_w - 2)) + "╮"
        cv.put(card_y, card_x, top, border_style)

        for idx, item in enumerate(spec.items):
            iy = card_y + 1 + idx
            item_rect = Rect(iy, card_x, 1, card_w)
            is_hov = item_rect.contains(ctx.hx, ctx.hy)
            badge = item.right_badge or ("✓" if item.checked else " ")
            if not item.enabled:
                item_style = ctx.theme.style(item.role, "disabled")
            elif is_hov:
                item_style = Style(item.hov_pair, bold=True)
            else:
                item_style = Style(item.rest_pair, bold=False)

            cv.put(iy, card_x, "│", border_style)
            cv.put(iy, card_x + 1, f" {disp_pad(item.label, card_w - 6)} {badge} ", item_style)
            cv.put(iy, card_x + card_w - 1, "│", border_style)

            if item.enabled:
                hm.add(item_rect, f"pop:{item.key}", payload=item.key, z=11)

        cv.put(card_y + n_items + 1, card_x, "╰" + ("─" * (card_w - 2)) + "╯", border_style)

    def handle(self, ev: Event, ctx: AppCtx | None = None) -> bool:
        spec = self._spec
        if spec is None:
            return False

        if isinstance(ev, FocusOut):
            if spec.close_on_focus_out:
                self.close()
                return True
            return False

        if isinstance(ev, Key):
            if ev.code == 27:
                self.close()
                return True
            if ev.code in spec.hotkeys:
                picked_key = spec.hotkeys[ev.code]
                cb = self._on_pick
                payload = self._payload
                # Close BEFORE invoking callback so pane-switching callbacks cannot race with FocusOut
                self.close()
                if cb is not None:
                    cb(picked_key, payload)
                return True
            self.close()
            return True

        if isinstance(ev, Mouse):
            if ev.kind != "click":
                return False
            rect = self._last_rect
            if rect is None:
                r_ctx = RenderCtx(h=40, w=32, left_w=self._last_left_w)
                cy, cx = spec.anchor(r_ctx)
                rect = Rect(cy, cx, len(spec.items) + 2, spec.width)
            if rect.contains(ev.x, ev.y):
                rel_y = ev.y - rect.y
                if 1 <= rel_y <= len(spec.items):
                    item = spec.items[rel_y - 1]
                    if item.enabled:
                        cb = self._on_pick
                        payload = self._payload
                        self.close()
                        if cb is not None:
                            cb(item.key, payload)
                    return True
                # Border row click is swallowed without closing
                return True
            # Click outside closes the popover
            self.close()
            return True

        return False


__all__ = [
    "NEW_SESSION_CARD_Y",
    "NEW_SESSION_HOTKEYS",
    "NEW_SESSION_ITEMS",
    "POPOVER_W",
    "PROVIDER_CARD_X",
    "PROVIDER_CARD_Y",
    "PROVIDER_FILTER_ITEMS",
    "PROVIDER_HOTKEYS",
    "RELAY_HOTKEYS",
    "RELAY_TO_ITEMS",
    "PopoverItem",
    "PopoverManager",
    "PopoverSpec",
    "draw_popover_card_to_stdscr",
    "new_session_menu_spec",
    "popover_card_x",
    "provider_menu_spec",
    "relay_menu_spec",
    "relay_to_card_y",
]
