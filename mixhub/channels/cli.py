"""`mixhub notify` command-line entry point."""
from __future__ import annotations

import argparse
import json
import sys

from mixhub.channels.text import format_mobile_card
from mixhub.channels.hooks import generate_official_hook_config, parse_hook_event

from mixhub.core.paths import ROOT


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mixhub notify",
        description="Send a one-way notification to Telegram / Slack / a webhook",
    )
    parser.add_argument("--channel", default="auto", choices=("auto", "telegram", "slack", "webhook", "all"),
                        help="Where to send (default: every configured channel)")
    parser.add_argument("--session", default="", help="Optional session ID or prefix the message is about")
    parser.add_argument("-t", "--topic", default="", help="Explicit topic or channel override")
    parser.add_argument("--hook", choices=("claude", "codex"),
                        help="Called from an agent hook: read the event (stdin for Claude Code, last argument for Codex)")
    parser.add_argument("--print-hooks", action="store_true", help="Print Claude Code and Codex hook configurations")
    parser.add_argument("--dry-run", action="store_true", help="Preview routed payload without sending")
    parser.add_argument("--alert", action="store_true", help="Enable sound alert on Telegram")
    parser.add_argument("message", nargs="*", help="Plain-text notification message")
    opts = parser.parse_args(argv)

    if opts.print_hooks:
        print(json.dumps(generate_official_hook_config(ROOT), ensure_ascii=False, indent=2))
        return 0

    if opts.hook:
        return _run_hook(opts)

    from mixhub.hub.hub import Hub

    hub = Hub()
    session = None
    if opts.session:
        for s in hub.discover().values():
            if s["id"] == opts.session or s["id"].startswith(opts.session):
                session = s
                break

    msg = " ".join(opts.message).strip()
    if not msg and session:
        msg = format_mobile_card(
            title=session.get("title") or session["id"][:8],
            did_what=session.get("lastPrompt") or "Session ready for review",
        )
    elif not msg:
        parser.error("Provide a message, --session <id>, or --print-hooks")

    res = hub.channel_manager().notify(
        message=msg,
        channel=opts.channel,
        session=session,
        topic=opts.topic or None,
        dry_run=opts.dry_run,
        alert=opts.alert,
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0 if res.get("ok") else 1


def _run_hook(opts) -> int:
    """An agent hook: send what happened to every configured channel. Always exit 0 so the agent never stalls."""
    from mixhub.channels.manager import ChannelManager

    raw = " ".join(opts.message) if opts.hook == "codex" else sys.stdin.read()
    parsed = parse_hook_event(opts.hook, raw)
    if parsed is None:
        return 0
    message, session = parsed
    label = f"{session['title']} ({session['tool']})"
    try:
        res = ChannelManager().notify(
            message=f"{label}: {message}",
            channel=opts.channel,
            session=session,
            topic=opts.topic or None,
            dry_run=opts.dry_run,
            alert=opts.alert,
        )
    except Exception as exc:  # a broken channel config must not break the agent's hook
        res = {"ok": False, "error": str(exc)}
    if opts.dry_run or not res.get("ok"):
        print(json.dumps(res, ensure_ascii=False), file=sys.stderr)
    return 0
