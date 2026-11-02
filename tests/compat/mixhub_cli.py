#!/usr/bin/env python3
"""Clone convenience entry point (`python3 mixhub_cli.py ...`); not shipped in the wheel, whose `mixhub` / `hub`
console scripts call `mixhub.cli.main:entry`. The dispatcher lives in `mixhub.cli.main`."""
from __future__ import annotations

import sys

from mixhub.cli.main import main as _cli_main
from mixhub.core.paths import ROOT  # noqa: F401


def main() -> int:
    return int(_cli_main() or 0)


if __name__ == "__main__":
    sys.exit(main())
