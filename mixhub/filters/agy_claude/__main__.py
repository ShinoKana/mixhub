#!/usr/bin/env python3
"""`python -m mixhub.filters.agy_claude [agy args]`: the `agy` PTY filter (what `hub agy` and hubpool windows run)."""
from __future__ import annotations

import sys

from mixhub.core.launch import release_pythonpath
from mixhub.filters.agy_claude.main import main

if __name__ == "__main__":
    release_pythonpath()  # the checkout root injected for this process must not reach agy or its tools
    sys.exit(main())
