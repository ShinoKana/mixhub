"""Bridges to other MixHub modules: handoff, rules sync, notification channels, doctor."""
from __future__ import annotations

import os


class IntegrationsMixin:
    """Handoff / rules / channels / doctor entry points."""

    def handoff(self, body=None):
        from mixhub.handoff.relay import create_handoff
        body = body or {}
        dry = body.get("dry_run", False)
        if not isinstance(dry, bool):
            raise ValueError("dry_run must be boolean")
        sid = str(body.get("id") or body.get("session_id") or "").strip()
        to_tool = str(body.get("to") or body.get("to_tool") or "").strip()
        provider = str(body.get("provider") or "").strip()
        from_tool = str(body.get("from_tool") or body.get("tool") or "").strip()
        target_session_id = str(body.get("target_session_id") or body.get("to_session_id") or "").strip()
        mode = str(body.get("mode") or "auto").strip()
        note = str(body.get("note") or "").strip()
        if not sid or not to_tool:
            raise ValueError("Both id and to (target tool) are required")
        return create_handoff(
            self,
            session_id_or_prefix=sid,
            to_tool=to_tool,
            provider=provider,
            from_tool=from_tool,
            target_session_id=target_session_id,
            mode=mode,
            note=note,
            dry_run=dry,
        )

    def get_handoff(self, session_or_bundle_id):
        md_path = os.path.join(self.root, "data", "handoffs", f"{session_or_bundle_id}.md")
        if os.path.isfile(md_path):
            with open(md_path, encoding="utf-8") as f:
                md = f.read()
            return {"ok": True, "bundle": {"from_session_id": session_or_bundle_id, "bundle_path": md_path, "markdown": md}}
        raise KeyError("Handoff bundle not found")

    def sync_rules(self, body=None):
        from mixhub.rules import sync as rules_sync_mod
        body = body or {}
        dry = body.get("dry_run", False)
        if not isinstance(dry, bool):
            raise ValueError("dry_run must be boolean")
        target_dir = str(body.get("dir") or self.root)
        force = bool(body.get("force", False))
        approve_policy = bool(body.get("approve_policy", False))
        return rules_sync_mod.sync_rules(
            project_dir=target_dir,
            state_dir=os.path.join(self.root, "data"),
            dry_run=dry,
            force=force,
            approve_policy=approve_policy,
        )

    def channel_manager(self):
        """Channels configured under this hub's home (`~/.config/mixhub/channels.json` and its older locations)."""
        from mixhub.channels.manager import ChannelManager
        return ChannelManager(home=self.home)

    def channels_notify(self, body=None):
        body = body or {}
        dry = body.get("dry_run", False)
        if not isinstance(dry, bool):
            raise ValueError("dry_run must be boolean")
        msg = str(body.get("message") or "").strip()
        channel = str(body.get("channel") or "telegram").strip()
        topic = body.get("topic") or None
        sid = str(body.get("session_id") or body.get("id") or "").strip()
        session = self.resolve_session_id(sid) if sid else None
        if not msg and session:
            from mixhub.channels.text import format_mobile_card
            msg = format_mobile_card(
                title=session.get("title") or session["id"][:8],
                did_what=session.get("lastPrompt") or "Session ready for review",
            )
        if not msg:
            raise ValueError("message or session_id is required")
        return self.channel_manager().notify(
            message=msg,
            channel=channel,
            session=session,
            topic=topic,
            dry_run=dry,
            alert=bool(body.get("alert", False)),
        )

    def channels_hooks_config(self):
        from mixhub.channels.hooks import generate_official_hook_config
        return generate_official_hook_config(self.root)

    def doctor(self):
        from mixhub.ops.doctor import run_doctor
        return run_doctor(self)
