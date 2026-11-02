"""`mixhub update` upgrades the way MixHub was installed; the daily check only ever prints a hint."""
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from mixhub.cli import update


class InstallMethodTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        patcher = mock.patch.dict(os.environ, {k: v for k, v in os.environ.items()
                                               if k not in ("UV_TOOL_DIR", "PIPX_HOME")}, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def method(self, prefix, code_root=None):
        code_root = code_root or str(self.root / "site-packages")
        return update.install_method(prefix, code_root)

    def test_checkout(self):
        (self.root / "repo" / ".git").mkdir(parents=True)
        self.assertEqual(self.method("/usr", str(self.root / "repo")), "checkout")

    def test_uv_pipx_and_venv(self):
        self.assertEqual(self.method("/home/me/.local/share/uv/tools/mixhub"), "uv")
        self.assertEqual(self.method("/home/me/.local/pipx/venvs/mixhub"), "pipx")
        self.assertEqual(self.method("/home/me/.local/share/pipx/venvs/mixhub"), "pipx")
        self.assertEqual(self.method("/home/me/.local/share/mixhub/venv"), "venv")
        os.environ["UV_TOOL_DIR"] = "/opt/tools"
        self.assertEqual(self.method("/opt/tools/mixhub"), "uv")

    def test_system_python_is_plain_pip(self):
        self.assertEqual(self.method(sys.base_prefix), "pip")

    def test_commands(self):
        self.assertEqual(update.upgrade_command("checkout", "/r"), ["git", "-C", "/r", "pull", "--ff-only"])
        self.assertEqual(update.upgrade_command("uv"), ["uv", "tool", "upgrade", "mixhub-cli"])
        self.assertEqual(update.upgrade_command("pipx"), ["pipx", "upgrade", "mixhub-cli"])
        self.assertEqual(update.upgrade_command("venv")[1:], ["-m", "pip", "install", "--upgrade", "mixhub-cli"])
        self.assertIsNone(update.upgrade_command("pip"))


class VersionTest(unittest.TestCase):
    def test_ordering(self):
        self.assertTrue(update.is_newer("0.1.10", "0.1.9"))
        self.assertTrue(update.is_newer("0.2", "0.1.9"))
        self.assertTrue(update.is_newer("0.2.0", "0.2.0rc1"))
        self.assertFalse(update.is_newer("0.2.0rc1", "0.2.0"))
        self.assertFalse(update.is_newer("0.1.0", "0.1.0"))
        self.assertFalse(update.is_newer("", "0.1.0"))


class RunUpdateTest(unittest.TestCase):
    def run_update(self, method, installed_after, *, rc=0, workspace=False, web=False):
        calls = []

        def runner(argv, **kw):
            calls.append(list(argv))
            code = rc if argv[0] in ("uv", "pipx", "git") or "pip" in argv else 0
            if argv[:2] == ["tmux", "has-session"]:
                code = 0 if workspace else 1
            return mock.Mock(returncode=code)

        out = io.StringIO()
        with mock.patch.object(update, "install_method", return_value=method), \
                mock.patch.object(update.shutil, "which", return_value="/bin/x"), \
                mock.patch.object(update, "_installed_version", return_value=installed_after), \
                mock.patch.object(update.mixhub, "__version__", "0.1.0"), \
                mock.patch("mixhub.cli.daemon.health", return_value={"pid": 1} if web else None), \
                mock.patch.dict(os.environ, {"MIXHUB_LANGUAGE": "en"}), \
                mock.patch("sys.stdout", out):
            code = update.run_update([], runner=runner)
        return code, out.getvalue(), calls

    def test_upgrade_then_reload_and_restart_on_new_code(self):
        code, out, calls = self.run_update("uv", "0.2.0", workspace=True, web=True)
        self.assertEqual(code, 0)
        self.assertEqual(calls[0], ["uv", "tool", "upgrade", "mixhub-cli"])
        self.assertIn("0.1.0 → 0.2.0", out)
        tails = [c[-1] for c in calls[1:] if c[0] != "tmux"]
        self.assertEqual(tails, ["reload", "stop", "start"])  # each through a new `mixhub` process
        self.assertTrue(all("-m" in c for c in calls[1:] if c[0] != "tmux"))

    def test_nothing_restarted_when_already_current(self):
        code, out, calls = self.run_update("pipx", "0.1.0", workspace=True, web=True)
        self.assertEqual(code, 0)
        self.assertIn("up to date", out)
        self.assertEqual(calls, [["pipx", "upgrade", "mixhub-cli"]])

    def test_checkout_restarts_only_when_the_pull_brought_commits(self):
        heads = iter(["abc", "abc"])
        with mock.patch.object(update, "_checkout_head", side_effect=lambda root: next(heads)):
            code, out, calls = self.run_update("checkout", "0.1.0", workspace=True, web=True)
        self.assertEqual(code, 0)
        self.assertIn("up to date", out)
        self.assertEqual(len(calls), 1)
        heads = iter(["abc", "def"])
        with mock.patch.object(update, "_checkout_head", side_effect=lambda root: next(heads)):
            code, out, calls = self.run_update("checkout", "0.1.0", workspace=True, web=True)
        self.assertIn("Pulled the latest code", out)
        self.assertEqual([c[-1] for c in calls[1:] if c[0] != "tmux"], ["reload", "stop", "start"])

    def test_failed_upgrade_returns_its_status(self):
        code, _, calls = self.run_update("uv", "0.2.0", rc=3, workspace=True)
        self.assertEqual(code, 3)
        self.assertEqual(len(calls), 1)

    def test_system_pip_is_not_touched(self):
        err = io.StringIO()
        with mock.patch.object(update, "install_method", return_value="pip"), mock.patch("sys.stderr", err), \
                mock.patch.dict(os.environ, {"MIXHUB_LANGUAGE": "en"}):
            self.assertEqual(update.run_update([], runner=mock.Mock(side_effect=AssertionError)), 1)
        self.assertIn("pip install --upgrade mixhub-cli", err.getvalue())


class DailyCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.prefs = Path(self.tmp.name) / "Preferences"
        self.prefs.write_text("{}")
        patches = [
            mock.patch.object(update, "DATA_DIR", self.tmp.name),
            mock.patch.object(update.mixhub, "__version__", "0.1.0"),
            mock.patch.object(update, "install_method", return_value="uv"),
            mock.patch.dict(os.environ, {"MIXHUB_PREFERENCES_PATH": str(self.prefs), "MIXHUB_LANGUAGE": "en",
                                         "MIXHUB_NO_UPDATE_CHECK": ""}),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def write_cache(self, latest, age):
        (Path(self.tmp.name) / update.CACHE_NAME).write_text(json.dumps({"checked_at": 1000 - age, "latest": latest}))

    def test_hint_from_cache_and_no_check_within_a_day(self):
        self.write_cache("0.2.0", age=60)
        spawn = mock.Mock()
        self.assertIn("MixHub 0.2.0 is available (you have 0.1.0)", update.update_notice(now=1000, spawn=spawn))
        spawn.assert_not_called()
        self.write_cache("0.1.0", age=60)
        self.assertEqual(update.update_notice(now=1000, spawn=spawn), "")

    def test_stale_cache_starts_one_background_check(self):
        self.write_cache("0.1.0", age=update.CHECK_EVERY_SEC + 1)
        spawn = mock.Mock()
        update.update_notice(now=1000, spawn=spawn)
        spawn.assert_called_once()
        self.assertEqual(spawn.call_args.args[0][-2:], ["mixhub.cli.update", "--refresh"])
        self.assertTrue(spawn.call_args.kwargs["start_new_session"])

    def test_turned_off_by_preference_env_or_checkout(self):
        self.write_cache("9.0.0", age=update.CHECK_EVERY_SEC + 1)
        spawn = mock.Mock()
        self.prefs.write_text(json.dumps({"update_check": False}))
        self.assertEqual(update.update_notice(now=1000, spawn=spawn), "")
        self.prefs.write_text("{}")
        with mock.patch.dict(os.environ, {"MIXHUB_NO_UPDATE_CHECK": "1"}):
            self.assertEqual(update.update_notice(now=1000, spawn=spawn), "")
        with mock.patch.object(update, "install_method", return_value="checkout"):
            self.assertEqual(update.update_notice(now=1000, spawn=spawn), "")
        spawn.assert_not_called()

    def test_refresh_reads_pypi_and_keeps_the_old_answer_when_offline(self):
        resp = mock.MagicMock()
        resp.__enter__.return_value = io.BytesIO(json.dumps({"info": {"version": "0.3.1"}}).encode())
        self.assertEqual(update.refresh_cache(urlopen=lambda url, timeout: resp), "0.3.1")
        self.assertEqual(update.refresh_cache(urlopen=mock.Mock(side_effect=OSError("offline"))), "")
        cache = json.loads((Path(self.tmp.name) / update.CACHE_NAME).read_text())
        self.assertEqual(cache["latest"], "0.3.1")


if __name__ == "__main__":
    unittest.main()
