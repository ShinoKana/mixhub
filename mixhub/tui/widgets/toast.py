#!/usr/bin/env python3
"""`mixhub.tui.widgets.toast`: Ephemeral handoff toast formatter."""
from __future__ import annotations


def format_ephemeral_handoff_toast(before_tokens: int, after_tokens: int, saved_usd: float) -> str:
    before_t = max(0, int(before_tokens))
    after_t = max(0, int(after_tokens))
    saved_t = max(0, before_t - after_t)
    return (
        f"✦ Handoff {before_t / 1000.0:.1f}k → {after_t / 1000.0:.1f}k ctx "
        f"(-{saved_t / 1000.0:.1f}k tokens · ~${float(saved_usd):.2f} saved) "
        f"· How was this handoff? [1] Great  [2] Fine  [3] Dismiss"
    )


__all__ = ["format_ephemeral_handoff_toast"]
