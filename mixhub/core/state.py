#!/usr/bin/env python3
"""
`mixhub.core.state`: Canonical 5-State Session Machine (`WORKING / AWAITING / UNREAD / IDLE / HIBERNATED`).
"""
from __future__ import annotations

import enum


class SessionState(str, enum.Enum):
    """Canonical 5-state lifecycle model for MixHub sessions."""

    WORKING = "WORKING"
    AWAITING = "AWAITING"
    UNREAD = "UNREAD"
    IDLE = "IDLE"
    HIBERNATED = "HIBERNATED"


WORKING = SessionState.WORKING.value
AWAITING = SessionState.AWAITING.value
UNREAD = SessionState.UNREAD.value
IDLE = SessionState.IDLE.value
HIBERNATED = SessionState.HIBERNATED.value

ALL_SESSION_STATES: tuple[str, ...] = (
    WORKING,
    AWAITING,
    UNREAD,
    IDLE,
    HIBERNATED,
)


def classify_session_state(
    session: dict,
    seen_timestamps: dict[str, float] | None = None,
) -> SessionState:
    """
    Map a raw Hub session dictionary into the canonical 5-state machine:
      - `WORKING`    : Live process actively executing or active subagents running.
      - `AWAITING`   : Live session waiting for user input or permission confirmation.
      - `UNREAD`     : Idle live/working-set session whose `updatedAt` is newer than `seen_timestamps`.
      - `IDLE`       : Live or working-set session at rest with all updates seen.
      - `HIBERNATED` : Non-live session outside the active working set (or explicitly hibernated).
    """
    is_live = bool(session.get("isLive"))
    in_wset = bool(session.get("inWorkingSet"))
    agent_state = str(session.get("agentState") or "").lower()
    active_subs = int(session.get("activeSubagents") or 0)

    if not is_live and not in_wset:
        return SessionState.HIBERNATED

    if agent_state == "working" or active_subs > 0:
        return SessionState.WORKING

    if agent_state in ("wait_input", "awaiting", "waiting_human"):
        return SessionState.AWAITING

    if seen_timestamps is not None:
        key = f"{session.get('tool', '')}:{session.get('id', '')}"
        sid = str(session.get("id") or "")
        seen_ts = float(seen_timestamps.get(key) or seen_timestamps.get(sid) or 0.0)
        updated_at = float(session.get("updatedAt") or 0.0)
        if seen_ts > 0.0 and updated_at > seen_ts + 1.0:
            return SessionState.UNREAD

    if session.get("hibernatedAt") and not is_live:
        return SessionState.HIBERNATED

    return SessionState.IDLE


__all__ = [
    "ALL_SESSION_STATES",
    "AWAITING",
    "HIBERNATED",
    "IDLE",
    "SessionState",
    "UNREAD",
    "WORKING",
    "classify_session_state",
]
