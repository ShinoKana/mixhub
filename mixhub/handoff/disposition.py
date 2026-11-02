"""Canonical 5-state session resolution."""
from __future__ import annotations


def _resolve_session_5state(session: dict, seen_timestamps: dict[str, float] | None = None) -> str:
    """Resolve canonical 5-state (`WORKING`, `AWAITING`, `UNREAD`, `IDLE`, `HIBERNATED`)."""
    explicit = str(session.get("sessionState") or session.get("state") or "").strip().upper()
    if explicit in ("WORKING", "AWAITING", "UNREAD", "IDLE", "HIBERNATED"):
        return explicit
    try:
        from mixhub.core.state import classify_session_state

        return str(classify_session_state(session, seen_timestamps=seen_timestamps).value)
    except Exception:
        is_live = bool(session.get("isLive") or session.get("processes"))
        ag_st = str(session.get("agentState") or session.get("tailStatus") or "").lower()
        if ag_st in ("working", "running", "thinking"):
            return "WORKING"
        if ag_st in ("wait_input", "awaiting", "waiting"):
            return "AWAITING"
        if session.get("unread"):
            return "UNREAD"
        return "IDLE" if is_live else "HIBERNATED"
