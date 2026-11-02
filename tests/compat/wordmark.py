#!/usr/bin/env python3
"""
Compatibility shim: the top-left `mixhub` wordmark micro-interaction lives in `mixhub.tui.widgets.wordmark`.

`import wordmark` keeps working for tests and external tools; patch `mixhub.tui.widgets.wordmark`, not this module.
"""
from __future__ import annotations

from mixhub.tui.widgets.wordmark import (  # noqa: F401
    _read_prefs,
    _write_prefs,
    DECODE_GLOW,
    DECODE_HOLD,
    DECODE_STEP,
    EFFECTS,
    EFFECT_SECS,
    FRAME,
    MODES,
    normalize_mode,
    PULSE_FRAMES,
    PULSE_PERIOD,
    PULSE_X_IDX,
    ST_ACCENT,
    ST_ACTIVE,
    ST_DIM,
    ST_FLASH,
    ST_LOCKED,
    ST_NORMAL,
    SWEEP_STEP,
    WORD,
    Wordmark,
)
