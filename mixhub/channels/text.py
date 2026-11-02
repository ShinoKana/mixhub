"""Message text helpers: plain-text sanitizer and mobile cards."""
from __future__ import annotations

import re


HTML_TAG_RE = re.compile(r"</?[A-Za-z!][^>]*>")
TABLE_LINE_RE = re.compile(r"^\s*\|.*\|\s*$")


def sanitize_plain_text(text: str) -> str:
    """
    Ensure a notification is strictly plain text:
      1. Strip all HTML tags (`<b>`, `<i>`, `<a ...>`, `<code>`, etc.).
      2. Replace any Markdown table separator lines (`|---|---|`).
      3. Never return a standalone `-` or a string starting with `-` that argparse
         could misinterpret as an option flag.
    """
    cleaned = HTML_TAG_RE.sub("", str(text or ""))
    lines = []
    for ln in cleaned.splitlines():
        if TABLE_LINE_RE.match(ln):
            ln = " · ".join(part.strip() for part in ln.strip("| ").split("|") if part.strip())
        lines.append(ln)
    out = "\n".join(lines).strip()
    if not out or out == "-":
        return "(empty notification)"
    if out.startswith("-"):
        out = "• " + out.lstrip("- ").strip()
    return out


def format_mobile_card(title: str, did_what: str, next_step: str = "") -> str:
    """A short plain-text card for a narrow screen: title, what happened, optionally what comes next."""
    lines = [f"◆ {sanitize_plain_text(title)[:48]}", f"● {sanitize_plain_text(did_what)[:200]}"]
    if next_step:
        lines.append(f"• Next: {sanitize_plain_text(next_step)[:60]}")
    return "\n".join(lines)
