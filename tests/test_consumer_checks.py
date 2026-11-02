"""Local scripts that import MixHub internals still import them.

Scripts outside this repo may reach into MixHub (private names included). List each one's self-check in
`data/consumer_checks.txt`, one command per line, `{python}` standing for MixHub's interpreter; the file is local
and gitignored, so a clone without it skips this test. A command must exit 0, so renaming or moving a name such a
script uses fails here first instead of breaking the script silently.
"""
import shlex
import subprocess
import sys
import unittest
from pathlib import Path

CHECKS = Path(__file__).resolve().parents[1] / "data" / "consumer_checks.txt"


class ConsumerChecksTest(unittest.TestCase):
    def test_every_listed_self_check_passes(self):
        if not CHECKS.is_file():
            self.skipTest(f"no {CHECKS.name}")
        lines = [ln.strip() for ln in CHECKS.read_text(encoding="utf-8").splitlines()]
        commands = [ln for ln in lines if ln and not ln.startswith("#")]
        for command in commands:
            with self.subTest(command=command):
                argv = [sys.executable if part == "{python}" else part for part in shlex.split(command)]
                res = subprocess.run(argv, capture_output=True, text=True, timeout=120)
                self.assertEqual(res.returncode, 0, f"{command}\n{res.stdout}{res.stderr}")


if __name__ == "__main__":
    unittest.main()
