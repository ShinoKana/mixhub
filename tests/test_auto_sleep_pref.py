"""Idle sessions are not put to sleep unless the user turns it on (Preferences `auto_sleep_hours`).

The old default slept a session after 12 h idle, shortened to 6 h under the kernel's "warn" memory pressure level.
A 16 GB Mac with swap in use sits at "warn" most of the day, so sessions were stopped 6 h after their last turn
and had to be resumed. Reading a session in the sidebar does not count as activity, which made it feel random.
"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server

A = "55555555-5555-4555-8555-555555555555"


class AutoSleepPreferenceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        root = self.home / "hub"
        root.mkdir()
        self.hub = server.Hub(str(self.home), str(root))
        self.prefs = self.home / "Preferences"
        env = patch.dict(os.environ, {"MIXHUB_PREFERENCES_PATH": str(self.prefs)})
        env.start()
        self.addCleanup(env.stop)

    def tearDown(self):
        self.temp.cleanup()

    def set_prefs(self, data):
        self.prefs.write_text(json.dumps(data))

    def run_gc(self, idle_hours, pressure=1):
        sess = {"tool": "claude", "id": A, "cwd": "/workspace", "resident": True, "agentState": "done_idle",
                "processes": [{"pid": 1, "tty": "ttys001", "resident": True}],
                "updatedAt": time.time() - idle_hours * 3600}
        with patch.object(self.hub, "discover", return_value={"claude:" + A: sess}), \
             patch.object(server.Hub, "get_system_resource_metrics", return_value={"sysMemoryPressureLevel": pressure}), \
             patch.object(self.hub, "sleep_blockers", return_value=[]), \
             patch.object(self.hub, "hibernate", return_value={"ok": True}) as hib:
            self.hub._last_proc_table = {}
            self.hub.processes = lambda: []
            self.hub.gc_idle_resident(dry_run=True)
            return hib.called

    def test_off_by_default_even_after_days_under_pressure(self):
        self.assertFalse(self.run_gc(72, pressure=4))

    def test_zero_or_false_keeps_it_off(self):
        for value in (0, False, None, "off", "nonsense", -3):
            self.set_prefs({"auto_sleep_hours": value})
            self.assertFalse(self.run_gc(72), value)

    def test_user_hours_are_used_as_is(self):
        self.set_prefs({"auto_sleep_hours": 8})
        self.assertFalse(self.run_gc(7))
        self.assertTrue(self.run_gc(9))

    def test_memory_pressure_no_longer_shortens_the_users_setting(self):
        self.set_prefs({"auto_sleep_hours": 8})
        self.assertFalse(self.run_gc(6.5, pressure=2))
        self.assertFalse(self.run_gc(5, pressure=4))

    def test_explicit_argument_still_wins(self):
        sess_hours = 2
        with patch.object(self.hub, "discover", return_value={"claude:" + A: {
                "tool": "claude", "id": A, "cwd": "/w", "resident": True, "agentState": "done_idle",
                "processes": [{"pid": 1, "tty": "ttys001", "resident": True}],
                "updatedAt": time.time() - sess_hours * 3600}}), \
             patch.object(self.hub, "sleep_blockers", return_value=[]), \
             patch.object(self.hub, "hibernate", return_value={"ok": True}) as hib:
            self.hub._last_proc_table = {}
            self.hub.processes = lambda: []
            self.hub.gc_idle_resident(max_idle_sec=3600, dry_run=True)
        self.assertTrue(hib.called)


if __name__ == "__main__":
    unittest.main()
