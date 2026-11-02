#!/usr/bin/env python3
"""`mixhub.probe.text`: secret redaction, raw approval-card excerpts, ANSI stripping and box-border cleanup."""
from __future__ import annotations

from mixhub.probe.patterns import (
    ANSI_CSI_RE,
    ANSI_OSC_RE,
    BOX_EDGE_RE,
    EXCERPT_LINES,
    FOOTER_HINT_RE,
    IDLE_STATUS_BAR_RE,
    RULE_RE,
    SECRET_REDACT_RE,
)


def redact_secrets(text: str) -> str:
    """Redact API keys, tokens, and passwords from terminal excerpts before Telegram display."""
    return SECRET_REDACT_RE.sub("[REDACTED]", str(text or ""))


def _build_raw_excerpt(
    context_lines: list[str],
    tail: list[str],
    menu: list[tuple[int, int, bool, str]] | None = None,
    question: str = "",
) -> list[str]:
    """
    Build up to 6 lines (`EXCERPT_LINES`) of raw terminal excerpt from the bottom of the pane
    (preserving cursor glyphs `❯` and redacting secrets) so Telegram approval cards show
    the exact CLI prompt even when an unfamiliar CLI version changes its wording.
    """
    filtered = [
        redact_secrets(ln[:80])
        for ln in tail
        if ln and not RULE_RE.match(ln) and not FOOTER_HINT_RE.match(ln) and not IDLE_STATUS_BAR_RE.search(ln)
    ]
    if not filtered:
        return []
    excerpt = filtered[-EXCERPT_LINES:]
    if question:
        q_clean = redact_secrets(question[:80])
        if q_clean and all(q_clean not in ln for ln in excerpt):
            if len(excerpt) >= EXCERPT_LINES:
                excerpt = [q_clean, "⋯", *excerpt[-(EXCERPT_LINES - 2):]]
            else:
                excerpt = [q_clean, *excerpt][-(EXCERPT_LINES):]
    return excerpt


def strip_ansi(text: str) -> str:
    """Remove ANSI CSI and OSC escape sequences from terminal text."""
    return ANSI_CSI_RE.sub("", ANSI_OSC_RE.sub("", str(text or "")))


def clean_terminal_line(line: str) -> str:
    """Strip ANSI escape sequences and outer Unicode box-drawing borders."""
    cleaned = strip_ansi(line).rstrip("\r\n")
    return BOX_EDGE_RE.sub("", cleaned).strip()
