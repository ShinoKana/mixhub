"""The package never deletes agent files: no web endpoint or command prunes transcripts or rollouts."""
import io
import json
import sys
import threading
import unittest
import urllib.request
from contextlib import redirect_stderr
from http.server import ThreadingHTTPServer
from unittest import mock

from mixhub.cli import main as cli_main
from mixhub.hub.hub import Hub
from mixhub.web.handler import Handler


class NoPruningTest(unittest.TestCase):
    def test_web_has_no_slim_endpoint(self):
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        srv.hub = mock.MagicMock()
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        port = srv.server_port
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/slim", data=b'{"dry_run": false}', method="POST",
                                     headers={"Host": f"127.0.0.1:{port}", "Content-Type": "application/json"})
        with self.assertRaises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(err.exception.code, 404)
        srv.hub.slim_storage.assert_not_called()

    def test_hub_and_cli_have_no_slim(self):
        self.assertFalse(hasattr(Hub, "slim_storage"))
        err = io.StringIO()
        with mock.patch.object(sys, "argv", ["mixhub", "slim", "--apply"]), redirect_stderr(err):
            self.assertEqual(cli_main.entry(), 2)
        self.assertIn("unknown command", err.getvalue())


if __name__ == "__main__":
    unittest.main()


class TakeoverTest(unittest.TestCase):
    """Clicking an idle session that runs in another terminal, with takeover asked for, is the one place MixHub stops an
    agent it did not start; sleep from the web refuses the same process."""

    def test_explicit_takeover_stops_the_outside_process(self):
        from types import SimpleNamespace
        from mixhub.tui.hubtui.actions import SessionActionsMixin as ActionsMixin
        outside = {"pid": 7, "tty": "ttys001", "resident": False, "boot_id": "bt:1", "start_time": "Sat Oct 31 00:00:00 2026"}
        s = {"id": "s1", "tool": "claude", "processes": [outside], "agentState": "idle"}
        app = SimpleNamespace(hub=mock.MagicMock(home="/nonexistent"))
        with mock.patch("mixhub.tui.hubtui.actions.time.sleep"):
            ActionsMixin._release_external_locks_before_takeover(app, s, force_takeover=True)
        app.hub.hibernate.assert_called_once_with(s, [outside], dry=False, keep_working_set=True)


class HookCommandTest(unittest.TestCase):
    """Hooks written into the agents' configs call `mixhub`: on many machines `hub` on PATH is GitHub's CLI."""

    def test_installed_package_hooks_call_mixhub_not_github_hub(self):
        import os
        import tempfile
        from mixhub.channels.hooks import generate_official_hook_config
        with tempfile.TemporaryDirectory() as tmp:
            bindir = os.path.join(tmp, "bin")
            os.mkdir(bindir)
            for name in ("hub", "mixhub"):
                path = os.path.join(bindir, name)
                open(path, "w").close()
                os.chmod(path, 0o755)
            # An installed package: no checkout `bin/hub`, interpreter elsewhere, GitHub's `hub` and `mixhub` on PATH.
            with mock.patch("mixhub.core.launch.CODE_ROOT", os.path.join(tmp, "site-packages")), \
                    mock.patch("mixhub.core.launch.sys.executable", os.path.join(tmp, "py", "python3")), \
                    mock.patch.dict(os.environ, {"PATH": bindir}):
                cfg = generate_official_hook_config()
        cmd = cfg["claude_settings_json"]["hooks"]["Stop"][0]["hooks"][0]["command"]
        self.assertEqual(cmd, os.path.join(bindir, "mixhub") + " notify --hook claude")
        self.assertIn('"' + os.path.join(bindir, "mixhub") + '"', cfg["codex_config_toml"])
