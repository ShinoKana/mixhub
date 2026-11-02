#!/usr/bin/env python3
"""`mixhub.tui.caffeinate`: macOS `caffeinate -i` bound to the hub or to working agents."""
from __future__ import annotations

import os
import subprocess
import time


class CaffeinateController:
    """Manages macOS `caffeinate -i` bound to Hub or active sessions to prevent idle sleep."""
    def __init__(self):
        self.mode = "auto"  # "auto" (while an agent is working, on AC), "on" (always), "off" (disabled)
        self.proc = None

    _battery_cache: tuple[float, bool] = (0.0, False)

    @classmethod
    def _on_battery(cls) -> bool:
        """`pmset -g batt` at most once a minute; unknown counts as AC."""
        ts, value = cls._battery_cache
        if time.monotonic() - ts < 60.0:
            return value
        try:
            out = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=1).stdout
            value = "Battery Power" in out
        except (OSError, subprocess.SubprocessError):
            value = False
        cls._battery_cache = (time.monotonic(), value)
        return value

    def sync(self, working_count: int):
        # auto: hold idle-sleep off only while an agent is working, and never on battery
        should_run = (self.mode == "on") or (
            self.mode == "auto" and working_count > 0 and not self._on_battery()
        )
        is_running = self.proc is not None and self.proc.poll() is None
        if should_run and not is_running:
            try:
                self.proc = subprocess.Popen(
                    ["caffeinate", "-i", "-w", str(os.getpid())],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            except OSError:
                self.proc = None
        elif not should_run and is_running:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=0.5)
            except Exception:
                pass
            self.proc = None

    def toggle(self, live_count: int) -> str:
        order = ["auto", "on", "off"]
        self.mode = order[(order.index(self.mode) + 1) % len(order)]
        self.sync(live_count)
        labels = {
            "auto": "awake: auto (prevent idle sleep while an agent is working, on AC)",
            "on": "awake: on (caffeinate -i active)",
            "off": "awake: off (system sleep allowed)",
        }
        return labels[self.mode]

    def badge(self, live_count: int) -> str:
        active = self.proc is not None and self.proc.poll() is None
        if self.mode == "on":
            return "awake:on"
        if self.mode == "auto":
            return "awake" if (active or live_count > 0) else "sleep-ok"
        return "sleep-ok"

    def close(self):
        if self.proc is not None and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:
                pass
            self.proc = None
