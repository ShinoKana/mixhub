#!/usr/bin/env python3
"""`python -m mixhub.tui [--sidebar | --reader | --header <tool> <sid> | --on-exit ... | --workspace]`."""
from __future__ import annotations

import locale

from mixhub.core.launch import release_pythonpath
from mixhub.tui.app import main

if __name__ == "__main__":
    release_pythonpath()  # the checkout root injected for this process must not reach its children
    locale.setlocale(locale.LC_ALL, "")
    main()
