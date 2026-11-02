#!/usr/bin/env python3
"""`mixhub.tui.widgets.header_bar`: `--header` Row 1 left-expanding Relay control states and layout."""
from __future__ import annotations

from mixhub.core.textwidth import disp_width

# Left-expanding 2-level Relay control (`⇄ Relay` closed / `◂ Relay` open, the `◂` pointing at the options on its left),
# always anchored at the right edge on `--header` Row 1
RELAY_TOGGLE_CLOSED = " ⇄ Relay "
RELAY_TOGGLE_OPEN = " ◂ Relay "
RELAY_STATES = ("CLOSED", "PICK_AGENT", "PICK_CLAUDE_BACKEND")


def relay_layout(
    w: int,
    state: str,
    min_x: int = 2,
    ds_ok: bool = True,
    rec_target: str = "",
) -> tuple[list[tuple[int, str, str]], list[tuple[int, int, str]]]:
    """
    Pure layout of the left-expanding 2-level Relay control anchored at the right edge (`--header` Row 1).

    - The toggle (`⇄ Relay` closed / `◂ Relay` open, 9 cols) always sits at `[w - 10, w - 1)`.
    - `PICK_AGENT` expands `Claude Gemini Codex` to its left; `PICK_CLAUDE_BACKEND` expands
      `< Back  Claude Code:  Anthropic DeepSeek`. Candidates are right-aligned to `w - 12` with a
      1-col separator and fall back to shorter labels when they would cross `min_x`. Labels carry no
      `[1]..[3]` prefixes and never append `★` (brand colors tell them apart; the `1`-`4` keys still work).

    Returns `(items, hits)`: `items = [(x0, label, act), ...]` left-to-right ending with the toggle, and
    `hits = [(x0, x1, act), ...]` for every clickable item (`act != "label"`).
    """
    tx1 = int(w) - 1
    tx0 = tx1 - 9
    if tx0 < min_x:
        return [], []
    if state not in ("PICK_AGENT", "PICK_CLAUDE_BACKEND"):
        return [(tx0, RELAY_TOGGLE_CLOSED, "relay_toggle")], [(tx0, tx1, "relay_toggle")]

    if state == "PICK_AGENT":
        options = [
            [
                ("Claude", "relay_pick_claude"),
                ("Gemini", "relay:agy"),
                ("Codex", "relay:codex"),
            ],
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

    bx1 = tx0 - 2
    sep = 1
    placed: list[tuple[int, str, str]] = []
    for cands in options:
        x = bx1
        trial: list[tuple[int, str, str]] = []
        for lbl, act in reversed(cands):
            lw = disp_width(lbl)
            x -= lw
            trial.append((x, lbl, act))
            x -= sep
        if trial and trial[-1][0] >= min_x:
            placed = list(reversed(trial))
            break
    items = placed + [(tx0, RELAY_TOGGLE_OPEN, "relay_toggle")]
    hits = [(x0, x0 + disp_width(lbl), act) for x0, lbl, act in placed if act != "label"]
    hits.append((tx0, tx1, "relay_toggle"))
    return items, hits
