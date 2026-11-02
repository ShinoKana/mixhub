#!/usr/bin/env python3
"""
`mixhub.tui.widgets.sidebar_row`: `SidebarGrid` column constants and `SidebarRow` widget.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mixhub.tui.core.canvas import Canvas
from mixhub.tui.core.events import Event
from mixhub.tui.core.geometry import Rect
from mixhub.tui.core.hitmap import HitMap
from mixhub.tui.core.style import P_HOVER, P_META, P_UNSEL, P_USER_BOX, P_WAIT, P_WHITE, Style
from mixhub.tui.core.widget import AppCtx, RenderCtx, Widget
from mixhub.tui.sidebar_rows import layout_sidebar_row_32


@dataclass(frozen=True)
class SidebarGrid:
    """Single source of truth for the 32-column sidebar grid geometry."""

    LEFT_W: int = 32
    ICON_X: int = 2
    LABEL_X: int = 5
    TITLE_COLS: int = 21
    AGE_X: int = 27

    @classmethod
    def for_width(cls, left_w: int = 32) -> "SidebarGrid":
        title_cols = max(4, int(left_w) - 11)
        return cls(
            LEFT_W=int(left_w),
            ICON_X=2,
            LABEL_X=5,
            TITLE_COLS=title_cols,
            AGE_X=5 + title_cols + 1,
        )


class SidebarRow(Widget):
    """Widget wrapping `layout_sidebar_row_32` and encoding the 5-state zero-bold Morandi age role."""

    def __init__(
        self,
        y: int,
        idx: int,
        session: dict[str, Any],
        *,
        is_selected: bool = False,
        is_hovered: bool = False,
        is_unread: bool = False,
    ):
        self.y = y
        self.idx = idx
        self.session = session
        self.is_selected = is_selected
        self.is_hovered = is_hovered
        self.is_unread = is_unread

    def render(self, cv: Canvas, hm: HitMap, ctx: RenderCtx) -> None:
        s = self.session
        left_w = ctx.left_w
        grid = SidebarGrid.for_width(left_w)
        cells = layout_sidebar_row_32(s, left_w=left_w, is_unread=self.is_unread, now_mono=ctx.now_mono)
        s_state = s.get("agentState") or ("working" if s.get("isLive") else "dormant")
        is_working = s_state == "working"
        is_wait = (not is_working) and s_state in ("wait_input", "wait")
        is_failed_unread = bool(
            self.is_unread and (s.get("failed") or s.get("interrupted") or s_state in ("failed", "interrupted"))
        )
        highlight_age = False if is_working else (is_wait or self.is_unread)

        if self.is_selected or self.is_hovered:
            bg_pair = P_USER_BOX if self.is_selected else P_HOVER
            cv.put(self.y, 1, "   ", Style(bg_pair, bold=False))

        brand_pair = (
            cells["agent_sel_pair"]
            if self.is_selected
            else (cells["agent_hov_pair"] if self.is_hovered else cells["agent_pair"])
        )
        cv.put(
            self.y,
            grid.ICON_X,
            cells["dot_ch"],
            Style(brand_pair, bold=bool(self.is_selected or cells["is_bold"])),
        )

        title_style = (
            Style(P_WHITE, bold=True)
            if (self.is_selected or highlight_age)
            else Style(P_WHITE if self.is_hovered else P_UNSEL, bold=False)
        )
        cv.put(self.y, grid.LABEL_X, cells["t_cell"], title_style)

        age_x = grid.AGE_X
        show_actions = self.is_hovered or (self.is_selected and ctx.hy == self.y and 0 <= ctx.hx < left_w - 1)
        if show_actions:
            is_pinned = bool(s.get("pinned"))
            pin_sym = "★" if is_pinned else "☆"
            cv.put(self.y, age_x, f"⋯ {pin_sym}", Style(P_WAIT if (highlight_age or is_pinned) else P_META, bold=bool(highlight_age or is_pinned)))
            hm.add(Rect(self.y, age_x - 1, 1, 3), "row:more", payload=(self.idx, self.y), z=2)
            hm.add(Rect(self.y, age_x + 2, 1, max(1, left_w - 1 - (age_x + 2))), "row:pin", payload=self.idx, z=2)
        else:
            if is_working:
                age_role = "age.working"
            elif is_wait:
                age_role = "age.wait"
            elif is_failed_unread:
                age_role = "age.failed"
            elif self.is_unread:
                age_role = "age.unread"
            else:
                age_role = "age.seen"
            age_style = ctx.theme.style(age_role, "selected" if self.is_selected else "rest")
            cv.put(self.y, age_x, cells["age_col"], age_style)

        hm.add(Rect(self.y, 0, 1, max(1, left_w - 1)), "row:select", payload=self.idx, z=1)

    def handle(self, ev: Event, ctx: AppCtx) -> bool:
        return False


__all__ = ["SidebarGrid", "SidebarRow"]
