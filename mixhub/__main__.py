#!/usr/bin/env python3
"""Entry point for `python3 -m mixhub` (same as the `mixhub` / `hub` console scripts)."""
from __future__ import annotations

import sys

from mixhub.cli.main import entry
from mixhub.core.launch import release_pythonpath

if __name__ == "__main__":
    release_pythonpath()  # the checkout root injected for this process must not reach its children
    sys.exit(entry())
