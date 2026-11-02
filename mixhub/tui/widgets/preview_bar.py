#!/usr/bin/env python3
"""`mixhub.tui.widgets.preview_bar`: Preview mode B/C action bar labels, captions, hints, and the bottom Relay layout."""
from __future__ import annotations

from mixhub.core.textwidth import disp_width

LBL_HISTORY = "▤ History"
LBL_RESUME = "Resume"
LBL_RESUME_CHIP = " Resume ↵ "
LBL_JUMP = "Go to Tab"
LBL_JUMP_CHIP = " Go to Tab ↵ "
LBL_GOTO = LBL_JUMP
LBL_GOTO_CHIP = LBL_JUMP_CHIP
LBL_MOVE = LBL_RESUME
LBL_MOVE_CHIP = LBL_RESUME_CHIP
LBL_RELAY = "Relay"
LBL_RELAY_CHIP = " Relay ↵ "
LBL_RELAY_BTN = "▐ ⇄ Relay ▾ ▌"
LBL_RELAY_WARN = "▐ ⇄ Relay ▾ wk {}% ▌"
LBL_RELAY_TO = "⇄ Relay to {}"
ST_DORMANT = "○ not running"
ST_EXT = "● open in another tab · {}"
CAP_RESUME = "resumes in this pane"
CAP_GOTO = "switches to that tab · keeps running"
CAP_MOVE = "stops it in that tab, resumes here"
CAP_MOVE_WORKING = "still working there · movable once idle"
CAP_MOVE_WAIT = "waiting for you there · answer it first"
CAP_QUOTA_LOW = "{} weekly {}% left"
CAP_RELAY_REC = "hands off to {} ({}% weekly left)"
MSG_MOVE_RACE = "it just got busy, not moved"
HINT_TYPE_B = "press ↵ to resume, then type"
HINT_TYPE_C = "press ↵ to resume here · or select Go to Tab"
RELAY_BOTTOM_OPEN = " Relay ▸ "


def preview_relay_layout(
    start_x: int,
    max_x: int,
    state: str,
    ds_ok: bool = True,
) -> tuple[list[tuple[int, str, str]], list[tuple[int, int, str]]]:
    """
    Pure layout of the right-expanding 2-level Relay candidates on the Preview bottom action bar (`Row h - 1`),
    placed immediately to the right of the `" Relay ▸ "` pill (`[start_x, max_x)`).
    Never renders `★` or `[1]..[3]` prefixes.
    """
    if state not in ("PICK_AGENT", "PICK_CLAUDE_BACKEND") or start_x >= max_x:
        return [], []
    if state == "PICK_AGENT":
        options = [
            [("Claude", "relay_pick_claude"), ("Gemini", "relay:agy"), ("Codex", "relay:codex")],
            [("Cl", "relay_pick_claude"), ("Gem", "relay:agy"), ("Cdx", "relay:codex")],
            [("C", "relay_pick_claude"), ("G", "relay:agy"), ("X", "relay:codex")],
        ]
    else:
        options = [
            [("< Back", "relay_back"), ("Claude Code:", "label"), ("Anthropic", "relay:claude"), ("DeepSeek", "relay:deepseek")],
            [("< Back", "relay_back"), ("Anthropic", "relay:claude"), ("DeepSeek", "relay:deepseek")],
            [("<", "relay_back"), ("Anth", "relay:claude"), ("DS", "relay:deepseek")],
        ]
        if not ds_ok:
            options = [[c for c in cands if c[1] != "relay:deepseek"] for cands in options]

    placed: list[tuple[int, str, str]] = []
    for cands in options:
        for sep in (2, 1):
            x = int(start_x)
            trial: list[tuple[int, str, str]] = []
            for lbl, act in cands:
                lw = disp_width(lbl)
                trial.append((x, lbl, act))
                x += lw + sep
            if trial and (trial[-1][0] + disp_width(trial[-1][1])) <= max_x:
                placed = trial
                break
        if placed:
            break
    hits = [(x0, x0 + disp_width(lbl), act) for x0, lbl, act in placed if act != "label"]
    return placed, hits
