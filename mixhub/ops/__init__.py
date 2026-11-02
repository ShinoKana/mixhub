#!/usr/bin/env python3
"""`mixhub.ops`: Operations modules (`doctor`); rules sync lives in `mixhub.rules`."""
from __future__ import annotations

from mixhub.ops import doctor
from mixhub.ops.doctor import run_doctor

__all__ = ["doctor", "run_doctor"]
