"""Settings read from the environment are `MIXHUB_*`; the older `HUB_*` names keep working."""
import os
import re
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from mixhub.core.env import env

ROOT = Path(__file__).resolve().parents[1]
# Set by MixHub on the processes it starts and read back from them; not user settings.
INTERNAL = {"HUB_OUTER_SESSION", "HUB_POOL_SESSION", "HUB_MANAGED"}


class EnvNamesTest(unittest.TestCase):
    def test_mixhub_name_wins_and_the_old_name_still_works(self):
        with mock.patch.dict(os.environ, {"HUB_PORT": "9001"}, clear=False):
            os.environ.pop("MIXHUB_PORT", None)
            self.assertEqual(env("PORT"), "9001")
            with mock.patch.dict(os.environ, {"MIXHUB_PORT": "9002"}):
                self.assertEqual(env("PORT"), "9002")
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(env("PORT", "8765"), "8765")

    def test_agent_home_replaces_hub_home(self):
        with mock.patch.dict(os.environ, {"HUB_HOME": "/old"}, clear=True):
            self.assertEqual(env("AGENT_HOME"), "/old")
        with mock.patch.dict(os.environ, {"HUB_HOME": "/old", "MIXHUB_AGENT_HOME": "/new"}, clear=True):
            self.assertEqual(env("AGENT_HOME"), "/new")

    def test_the_package_reads_settings_only_through_env(self):
        files = subprocess.run(["git", "ls-files", "mixhub"], cwd=ROOT, capture_output=True, text=True).stdout.split()
        offenders = []
        for rel in files:
            if not rel.endswith(".py") or rel == "mixhub/core/env.py":
                continue
            text = (ROOT / rel).read_text(encoding="utf-8")
            for name in re.findall(r"""environ(?:\.get)?\(\s*["'](HUB_[A-Z_]+)["']""", text):
                if name not in INTERNAL:
                    offenders.append(f"{rel}: {name}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
