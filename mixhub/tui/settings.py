#!/usr/bin/env python3
"""`mixhub.tui.settings`: Platform flag and key-binding labels."""
from __future__ import annotations

import sys

IS_MAC = sys.platform == "darwin"
KEY_BACK_LEFT = "Ctrl+\\/Ctrl+g"
KEY_TOGGLE_SIDEBAR = "Ctrl+]/⌥b" if IS_MAC else "Ctrl+]/Alt+b"
