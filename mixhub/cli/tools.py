#!/usr/bin/env python3
"""`mixhub.cli.tools`: commands delegating to another module's `main` (`hub sync-rules`, `notify`, `doctor`)."""

def run_sync_rules(argv):
    from mixhub.rules.sync import main
    return main(argv)


def run_notify(argv):
    from mixhub.channels.cli import main
    return main(argv)


def run_doctor(argv):
    from mixhub.ops.doctor import main
    return main(argv)


__all__ = ["run_doctor", "run_notify", "run_sync_rules"]
