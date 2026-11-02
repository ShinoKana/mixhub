#!/usr/bin/env python3
"""`mixhub.cli.sessions`: Hub-backed session commands (`handoff`)."""
import argparse


def run_handoff(argv):
    parser = argparse.ArgumentParser(prog="mixhub handoff", description="Generate 10-section cross-agent handoff bundle + handoff.yaml")
    parser.add_argument("target", help="Source session ID or prefix")
    parser.add_argument("--to", required=True, choices=("claude", "agy", "codex", "deepseek"), help="Target agent engine")
    parser.add_argument("--mode", default="auto", choices=("auto", "escalate", "delegate", "review"), help="Directional handoff mode (auto|escalate|delegate|review)")
    parser.add_argument("--provider", default="", help="Optional provider override (e.g. deepseek)")
    parser.add_argument("--from-tool", default="", help="Optional source tool filter (claude|agy|codex)")
    parser.add_argument("--note", default="", help="Optional handoff note")
    parser.add_argument("--dry-run", action="store_true", help="Preview bundle without writing state")
    opts = parser.parse_args(argv)
    from mixhub.hub.hub import Hub
    hub = Hub()
    res = hub.handoff({
        "id": opts.target,
        "to": opts.to,
        "mode": opts.mode,
        "provider": opts.provider,
        "from_tool": opts.from_tool,
        "note": opts.note,
        "dry_run": opts.dry_run,
    })
    print(f"● Handoff [{res['from_tool']} -> {res['to_tool']}] · mode={res['mode']} · status={res['status']}")
    print(f"  - Bundle: {res['bundle_path']} (~{res['token_estimate']} tokens)")
    print(f"  - YAML  : {res['yaml_path']}")
    print(f"  - Chain : {' -> '.join(res['handoffChain'])}")
    print(f"  - Launch: {res['command']}")
    return 0 if res.get("ok") else 1


__all__ = ["run_handoff"]
