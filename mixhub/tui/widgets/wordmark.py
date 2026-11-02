#!/usr/bin/env python3
"""`mixhub.tui.widgets.wordmark`: top-left `mixhub` wordmark Easter egg (pure stdlib, 0 token, never touches agent processes).

The wordmark is a *state* of the brand text, not a character: it only ever changes the six
cells of `mixhub` on row 0, only reacts to the mouse, and costs zero wakeups while static
(`next_frame_delay()` returns `None`).

A click on Home plays one effect (never the same one twice in a row); any click while an effect
runs stops it at once:
  - `decode`: the letters scramble, then settle left to right, each lighting up as it lands (one shot).
  - `sweep` : a highlight runs across the word and back (one shot).
  - `pulse` : the `x` turns `x * + *` while an agent works and goes warm while one waits for
              approval; stays on (persisted as `"wordmark"` in the `Preferences` JSON) until clicked off.
"""
from __future__ import annotations

import json
import os
import random
import string

WORD = "mixhub"
MODES = ("off", "pulse", "decode", "sweep")
EFFECTS = ("decode", "sweep", "pulse")

PULSE_PERIOD = 0.45
PULSE_FRAMES = ("x", "*", "+", "*")
PULSE_X_IDX = 2

FRAME = 0.05
DECODE_HOLD = 0.25   # every cell scrambles before the first one settles
DECODE_STEP = 0.09   # then one cell settles per step, left to right
DECODE_GLOW = 0.18   # a settled cell stays lit this long
SWEEP_STEP = 0.07    # the highlight moves one cell per step, there and back
EFFECT_SECS = {
    "decode": DECODE_HOLD + DECODE_STEP * (len(WORD) - 1) + DECODE_GLOW,
    "sweep": SWEEP_STEP * (2 * len(WORD) - 1),
}

# Cell styles returned by `render_cells()`; the TUI maps them to curses attributes.
ST_NORMAL = "normal"   # regular wordmark colour (P_UNSEL)
ST_ACCENT = "accent"   # warm sand-gold (an agent waits for approval)
ST_DIM = "dim"         # scrambled, not yet settled
ST_LOCKED = "locked"   # lit: just settled / the sweep's head (white bold)
ST_ACTIVE = "active"   # the sweep's trail
ST_FLASH = "flash"     # reverse video


def _read_prefs(path: str) -> dict:
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return dict(data)
    except (OSError, ValueError, TypeError):
        pass
    return {}


def _write_prefs(path: str, updates: dict) -> None:
    """Atomically merge `updates` into the Preferences JSON, preserving every other key."""
    data = _read_prefs(path)
    data.update(updates)
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = f"{path}.tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except OSError:
        pass


def normalize_mode(value) -> str:
    # Only `pulse` is ever persisted; older saved modes (the lock game, the shuffle) read as `off`.
    return "pulse" if value == "pulse" else "off"


class Wordmark:
    """State machine for the `mixhub` wordmark. All methods take an explicit monotonic `now`."""

    def __init__(self, prefs_path: str | None = None, rng: random.Random | None = None, persist: bool = True):
        self.prefs_path = prefs_path
        self.persist = bool(persist and prefs_path)
        self.rng = rng or random.Random()
        prefs = _read_prefs(prefs_path) if prefs_path else {}
        self.mode = normalize_mode(prefs.get("wordmark"))
        self.effect_start = 0.0
        self.scramble_seed = 0
        # pulse started by an explicit `mixhub` click animates even while idle
        self.pulse_manual: bool = False
        self.last_effect: str | None = None

    def _save(self, **updates) -> None:
        if self.persist:
            _write_prefs(self.prefs_path, updates)

    def set_mode(self, mode: str, now: float | None = None) -> str:
        self.mode = mode if mode in MODES else "off"
        self.pulse_manual = False
        self._save(wordmark=normalize_mode(self.mode))
        return self.mode

    def _advance(self, now: float) -> None:
        """One-shot effects end on their own (no timers/threads: resolved lazily on the next query)."""
        if self.mode in EFFECT_SECS and now - self.effect_start >= EFFECT_SECS[self.mode]:
            self.mode = "off"

    def is_moving(self, now: float) -> bool:
        self._advance(now)
        return self.mode != "off"

    def cancel_game(self, now: float | None = None) -> None:
        """Kept for callers: clicks elsewhere leave the wordmark alone."""

    def on_wordmark_click(self, now: float) -> str:
        """Click on `mixhub` (Home only): stop whatever runs, else start an effect. Returns the new mode."""
        self._advance(now)
        if self.mode != "off":
            return self.set_mode("off", now)
        pool = [e for e in EFFECTS if e != self.last_effect]
        choice = self.rng.choice(pool)
        self.last_effect = choice
        self.mode = choice
        self.effect_start = now
        self.scramble_seed = self.rng.randrange(1 << 30)
        self.pulse_manual = choice == "pulse"
        self._save(wordmark=normalize_mode(choice))
        return choice

    def _scrambled(self, i: int, now: float) -> str:
        step = int(max(0.0, now - self.effect_start) / FRAME)
        return random.Random(self.scramble_seed * 1_000_003 + i * 7_919 + step).choice(string.ascii_lowercase)

    def render_cells(self, now: float, working: bool = False, wait_tool: bool = False) -> list[tuple[str, str]]:
        """Return six `(char, style)` cells for the current frame."""
        self._advance(now)
        cells = [(ch, ST_NORMAL) for ch in WORD]
        t = now - self.effect_start
        if self.mode == "pulse":
            if wait_tool:
                cells[PULSE_X_IDX] = ("x", ST_ACCENT)
            elif working or self.pulse_manual:
                cells[PULSE_X_IDX] = (PULSE_FRAMES[int(now / PULSE_PERIOD) % len(PULSE_FRAMES)], ST_NORMAL)
        elif self.mode == "decode":
            for i, ch in enumerate(WORD):
                landed = DECODE_HOLD + DECODE_STEP * i
                if t < landed:
                    cells[i] = (self._scrambled(i, now), ST_DIM)
                elif t < landed + DECODE_GLOW:
                    cells[i] = (ch, ST_LOCKED)
        elif self.mode == "sweep":
            step = int(t / SWEEP_STEP)
            head = step if step < len(WORD) else 2 * len(WORD) - 2 - step
            back = step >= len(WORD)
            if 0 <= head < len(WORD):
                cells[head] = (WORD[head], ST_LOCKED)
                trail = head + 1 if back else head - 1
                if 0 <= trail < len(WORD):
                    cells[trail] = (WORD[trail], ST_ACTIVE)
        return cells

    def render_text(self, now: float, working: bool = False, wait_tool: bool = False) -> str:
        return "".join(ch for ch, _ in self.render_cells(now, working=working, wait_tool=wait_tool))

    def render_suffix(self, now: float) -> tuple[str, bool]:
        """Text shown right of the wordmark on Home (none in the current effects)."""
        return "", False

    def next_frame_delay(self, now: float, working: bool = False) -> float | None:
        """Seconds until the next visual change, or `None` when static (0% idle CPU)."""
        self._advance(now)
        if self.mode in EFFECT_SECS:
            left = EFFECT_SECS[self.mode] - (now - self.effect_start)
            return max(0.02, round(min(FRAME, left), 4))
        if self.mode == "pulse" and (working or self.pulse_manual):
            return max(0.02, round(PULSE_PERIOD - (now % PULSE_PERIOD), 4))
        return None


__all__ = [
    "DECODE_GLOW",
    "DECODE_HOLD",
    "DECODE_STEP",
    "EFFECTS",
    "EFFECT_SECS",
    "FRAME",
    "MODES",
    "PULSE_FRAMES",
    "PULSE_PERIOD",
    "PULSE_X_IDX",
    "ST_ACCENT",
    "ST_ACTIVE",
    "ST_DIM",
    "ST_FLASH",
    "ST_LOCKED",
    "ST_NORMAL",
    "SWEEP_STEP",
    "WORD",
    "Wordmark",
    "normalize_mode",
]
