"""`scripts/demo_seed.py` writes the same demo sessions however often it runs."""
import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "demo_seed.py"


def load():
    spec = importlib.util.spec_from_file_location("demo_seed", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DemoSeedTest(unittest.TestCase):
    def test_running_twice_does_not_duplicate_sessions(self):
        seed = load()
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            seed.main(tmp)
            first = sorted(p.name for p in home.rglob("*.jsonl"))
            seed.main(tmp)
            self.assertEqual(sorted(p.name for p in home.rglob("*.jsonl")), first)
            with sqlite3.connect(home / ".codex/state_5.sqlite") as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM threads").fetchone()[0], 3)
            with sqlite3.connect(home / ".local/share/opencode/opencode.db") as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM session_v2").fetchone()[0], 1)

    def test_file_names_do_not_move_when_the_clock_ticks_between_runs(self):
        import time
        seed = load()
        real_strftime = time.strftime
        later = lambda fmt, t=None: real_strftime(fmt, time.localtime(time.time() + 5) if t is None else t)
        with tempfile.TemporaryDirectory() as tmp:
            seed.main(tmp)
            first = sorted(p.name for p in Path(tmp).rglob("*.jsonl"))
            with mock.patch("time.strftime", later):
                seed.main(tmp)
            self.assertEqual(sorted(p.name for p in Path(tmp).rglob("*.jsonl")), first)

    def test_quota_is_the_demo_s_own(self):
        seed = load()
        with tempfile.TemporaryDirectory() as tmp:
            home = seed.main(tmp)
            env = seed.demo_env(home)
            with mock.patch.dict(os.environ, env), mock.patch("mixhub.core.paths.STATE_ROOT", env["MIXHUB_HOME"]):
                from mixhub.tui.quota import get_cli_quota_metrics

                items = {i["key"]: i for i in get_cli_quota_metrics()}
        self.assertEqual((items["cc"]["rem_5h"], items["cc"]["rem_wk"]), (64, 71))
        self.assertEqual((items["agy"]["rem_5h"], items["agy"]["rem_wk"]), (88, 57))
        self.assertEqual((items["cx"]["rem_5h"], items["cx"]["rem_wk"]), (69, 78))

    def test_some_sessions_are_active_and_the_shell_prompt_names_no_one(self):
        seed = load()
        with tempfile.TemporaryDirectory() as tmp:
            home = seed.main(tmp)
            active = json.loads((home / "state/active_working_set.json").read_text())["sessions"]
            rc = (home / ".zshrc").read_text()
        self.assertEqual(sorted(k.split(":")[0] for k in active), ["agy", "claude", "codex", "opencode"])
        self.assertIn("PROMPT=", rc)
        self.assertNotIn("%n", rc)  # no user name
        self.assertNotIn("%m", rc)  # no host name

    def test_every_agent_has_sessions_and_one_conversation_is_long(self):
        seed = load()
        with tempfile.TemporaryDirectory() as tmp:
            home = seed.main(tmp)
            env = seed.demo_env(home)
            with mock.patch.dict(os.environ, env):
                from mixhub.hub.hub import Hub

                with mock.patch.object(Hub, "processes", lambda self: []), \
                        mock.patch.object(Hub, "open_files", lambda self, pids: {}):
                    hub = Hub(home=str(home), root=env["MIXHUB_HOME"])
                    rows = list(hub.listing({})["sessions"])
                    by_title = {r["title"]: r for r in rows}
                    long_one = list(hub.transcript(hub.get("claude", by_title["Terraform state lock timeout"]["id"])))
        tools = {r["tool"] for r in rows}
        self.assertEqual(tools, {"claude", "codex", "agy", "opencode"})
        self.assertEqual(by_title["Flaky login e2e test"]["tool"], "agy")
        self.assertTrue(by_title["Flaky login e2e test"]["inWorkingSet"])
        self.assertGreaterEqual(sum(r["tool"] == "codex" for r in rows), 3)
        prompts = [m for m in long_one if m.get("role") == "user" and m.get("kind") == "message"]
        self.assertEqual(len(prompts), 7)


if __name__ == "__main__":
    unittest.main()
