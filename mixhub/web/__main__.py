#!/usr/bin/env python3
"""`python -m mixhub.web`: the loopback Web daemon started by `mixhub start` (`MIXHUB_PORT`, default 8765)."""
from __future__ import annotations

from mixhub.core.launch import release_pythonpath
from mixhub.web.server import main

if __name__ == "__main__":
    release_pythonpath()  # the checkout root injected for this process must not reach its children
    main()
