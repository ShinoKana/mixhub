"""The Pinned section orders its sessions by recent activity, the same way Active does."""
from types import SimpleNamespace


def _sidebar(sessions, mode="status"):
    from mixhub.tui.hubtui.refresh import RefreshMixin
    from mixhub.tui.hubtui.sidebar import SidebarListMixin
    app = SimpleNamespace(sessions=sessions, sidebar_group_mode=mode, _collapsed_sections=set(), sidebar_only=True,
                          _matches_provider_filter=lambda s: True, _session_bucket=RefreshMixin._session_bucket)
    return [r["session"]["id"] for r in SidebarListMixin._sidebar_display_rows(app) if r["kind"] == "session"]


def test_pinned_sessions_are_ordered_by_recent_activity_like_active():
    sessions = [
        {"id": "p-old", "tool": "claude", "pinned": True, "updatedAt": 100.0},
        {"id": "p-new", "tool": "claude", "pinned": True, "updatedAt": 300.0},
        {"id": "p-mid", "tool": "codex", "pinned": True, "updatedAt": 200.0},
        {"id": "a-old", "tool": "claude", "isLive": True, "updatedAt": 50.0},
        {"id": "a-new", "tool": "claude", "isLive": True, "updatedAt": 400.0},
    ]
    assert _sidebar([dict(s) for s in sessions]) == ["p-new", "p-mid", "p-old", "a-new", "a-old"]
    assert _sidebar([dict(s) for s in sessions], mode="workspace")[:3] == ["p-new", "p-mid", "p-old"]
