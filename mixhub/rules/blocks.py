#!/usr/bin/env python3
"""`mixhub.rules.blocks`: managed `BEGIN/END HUB SYNC` blocks, `hub-policy` extraction and sha256 digests."""
from __future__ import annotations

import hashlib
import json
import re


BEGIN_RE = re.compile(
    r"^<!--\s*BEGIN HUB SYNC(?:\s+sha256:([0-9a-fA-F]{64}))?(?:\s+source:[^\s>-]+)?\s*-->\r?$",
    re.M,
)
END_RE = re.compile(r"^<!--\s*END HUB SYNC\s*-->\r?$", re.M)
POLICY_BLOCK_RE = re.compile(r"```json\s+hub-policy\s*\n(.*?)```", re.S | re.I)


def sha256_text(text: str) -> str:
    """Compute canonical SHA-256 hex digest of normalized UTF-8 text."""
    normalized = (text or "").replace("\r\n", "\n").strip() + "\n"
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def extract_hub_policy(agents_text: str) -> tuple[dict | None, str]:
    """
    Extract optional ```json hub-policy ... ``` block from AGENTS.md.
    Returns `(parsed_policy_dict_or_None, policy_sha256_or_empty)`.
    """
    match = POLICY_BLOCK_RE.search(agents_text or "")
    if not match:
        return None, ""
    raw_block = match.group(1).strip()
    digest = sha256_text(raw_block)
    try:
        parsed = json.loads(raw_block)
        if not isinstance(parsed, dict):
            parsed = {"raw": parsed}
    except ValueError:
        parsed = {"_parse_error": True, "raw": raw_block}
    return parsed, digest


def build_managed_block(agents_text: str, include_at_import: bool = False) -> tuple[str, str]:
    """
    Build the managed block string and return `(block_text, body_sha256)`.
    """
    canonical_body = (agents_text or "").replace("\r\n", "\n").strip()
    if include_at_import and "@AGENTS.md" not in canonical_body:
        inner = "@AGENTS.md\n\n" + canonical_body
    else:
        inner = canonical_body
    digest = sha256_text(inner)
    block = (
        f"<!-- BEGIN HUB SYNC sha256:{digest} source:AGENTS.md -->\n"
        f"{inner}\n"
        f"<!-- END HUB SYNC -->\n"
    )
    return block, digest


def inspect_managed_block(existing_text: str) -> dict:
    """
    Inspect an existing target file (CLAUDE.md or .gemini/GEMINI.md) for a managed block
    and verify whether its inner content matches the recorded SHA-256 hash.
    """
    if not existing_text:
        return {"has_block": False, "tampered": False, "recorded_sha256": "", "actual_sha256": ""}
    m_begin = BEGIN_RE.search(existing_text)
    m_end = END_RE.search(existing_text)
    if not m_begin or not m_end or m_end.start() < m_begin.end():
        return {"has_block": False, "tampered": False, "recorded_sha256": "", "actual_sha256": ""}
    recorded = (m_begin.group(1) or "").lower()
    inner = existing_text[m_begin.end() : m_end.start()].strip()
    actual = sha256_text(inner)
    tampered = bool(recorded and recorded != actual)
    return {
        "has_block": True,
        "tampered": tampered,
        "recorded_sha256": recorded,
        "actual_sha256": actual,
        "span": (m_begin.start(), m_end.end()),
        "inner": inner,
    }


def merge_managed_block(existing_text: str, new_block: str) -> str:
    """
    Insert or replace the `<!-- BEGIN HUB SYNC ... -->...<!-- END HUB SYNC -->` section
    while preserving any local user rules outside the managed block.
    """
    if not existing_text or not existing_text.strip():
        return new_block
    info = inspect_managed_block(existing_text)
    if info["has_block"]:
        start, end = info["span"]
        prefix = existing_text[:start]
        suffix = existing_text[end:].lstrip("\r\n")
        merged = prefix + new_block + ("\n" + suffix if suffix else "")
        return merged.rstrip() + "\n"
    return existing_text.rstrip() + "\n\n" + new_block


def strip_managed_wrappers(text: str) -> str:
    """Extract clean SSoT rules when bootstrapping AGENTS.md from an existing CLAUDE.md/GEMINI.md."""
    info = inspect_managed_block(text)
    body = info["inner"] if info["has_block"] else (text or "")
    lines = [ln for ln in body.splitlines() if ln.strip() != "@AGENTS.md"]
    return "\n".join(lines).strip() + "\n"
