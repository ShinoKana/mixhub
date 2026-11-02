"""MixHub runs its outer workspace on its own tmux server (`tmux -L mixhub`), never on the user's default server: no
session, global option, key binding or hook of MixHub lands where the user's own tmux sessions live."""
import os
import subprocess
import unittest
from unittest import mock

from mixhub.tmux import outer, workspace
from mixhub.tmux.gateway import TmuxGateway


def _default_server(argv):
    argv = [str(a) for a in argv]
    if not argv or os.path.basename(argv[0]) != "tmux":
        return False
    return "-L" not in argv[1:3] and "-S" not in argv[1:3]


class OwnTmuxServerTest(unittest.TestCase):
    def setUp(self):
        self.calls = []

        def fake_run(argv, *a, **k):
            self.calls.append(list(argv))
            out = "%1|0|python3\n%2|33|tmux\n" if "list-panes" in argv else ""
            return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")

        def fake_popen(argv, *a, **k):
            self.calls.append(list(argv))
            return mock.MagicMock(returncode=0)

        for target, fn in (("subprocess.run", fake_run), ("subprocess.Popen", fake_popen)):
            p = mock.patch(target, fn)
            p.start()
            self.addCleanup(p.stop)

    def assert_nothing_on_default_server(self):
        stray = [c for c in self.calls if _default_server(c)]
        self.assertEqual(stray, [], "tmux commands sent to the user's default server")
        self.assertTrue(any("mixhub" in c for c in self.calls if c and os.path.basename(c[0]) == "tmux"))

    def test_building_the_workspace_stays_off_the_default_server(self):
        with mock.patch.dict(os.environ, {"TMUX": ""}):
            workspace.ensure_outer_dual_pane_layout("hub", "pool")
            workspace.allocate_workspace_session_names()
        self.assert_nothing_on_default_server()

    def test_outer_pane_helpers_stay_off_the_default_server(self):
        outer._OUTER_PANE_SNAPSHOT.clear()
        outer.get_outer_hub_pane_ids("hub")
        outer.focus_outer_left_pane("hub")
        outer.focus_outer_right_pane("hub")
        outer.is_outer_hub_zoomed("hub")
        outer._list_outer_client_ttys("hub")
        outer._is_outer_client_focused("hub")
        outer.set_outer_client_autowrap("hub", enabled=True)
        outer.toggle_outer_sidebar_zoom("hub")
        outer._sync_outer_window_title({"title": "x", "tool": "claude"}, "hub")
        self.assert_nothing_on_default_server()

    def test_gateway_outer_server_is_mixhubs_own(self):
        self.assertEqual(TmuxGateway().build_argv(["list-panes"], server="outer")[1:3], ["-L", "mixhub"])

    def test_attach_inside_the_users_tmux_nests_instead_of_switching_their_client(self):
        with mock.patch.dict(os.environ, {"TMUX": "/tmp/tmux-501/default,1,0"}), \
                mock.patch.object(workspace, "ensure_outer_dual_pane_layout"), \
                mock.patch.object(workspace, "allocate_workspace_session_names", return_value=("hub", "pool")), \
                mock.patch("os.execvpe") as execvpe, mock.patch("os.execvp") as execvp:
            workspace.launch_tmux_workspace()
        execvp.assert_not_called()
        argv, env = execvpe.call_args.args[1], execvpe.call_args.args[2]
        self.assertEqual(argv[:3], ["tmux", "-L", "mixhub"])
        self.assertIn("attach-session", argv)
        self.assertNotIn("TMUX", env)

    def test_mixhub_command_inside_the_users_tmux_nests_too(self):
        from mixhub.cli import workspace as cli_ws
        with mock.patch.dict(os.environ, {"TMUX": "/tmp/tmux-501/default,1,0"}), \
                mock.patch.object(cli_ws.shutil, "which", return_value="/usr/bin/tmux"), \
                mock.patch("mixhub.tmux.workspace.allocate_workspace_session_names", return_value=("hub", "pool")), \
                mock.patch("mixhub.tmux.workspace.ensure_outer_dual_pane_layout"), \
                mock.patch.object(cli_ws, "apply_window_title_and_hooks", return_value="mixhub"), \
                mock.patch.object(cli_ws, "retire_legacy_outer_sessions"), \
                mock.patch("os.execve") as execve:
            cli_ws.run_workspace()
        argv, env = execve.call_args.args[1], execve.call_args.args[2]
        self.assertEqual(argv[1:3], ["-L", "mixhub"])
        self.assertIn("attach-session", argv)
        self.assertNotIn("TMUX", env)

    def test_reattach_shell_hook_asks_mixhubs_server(self):
        self.assertNotIn("tmux has-session", outer.SHELL_HOOK)
        self.assertIn("tmux -L mixhub has-session", outer.SHELL_HOOK)


if __name__ == "__main__":
    unittest.main()


class LegacyOuterSessionTest(unittest.TestCase):
    """Before 0.2.1 the outer session lived on the user's default server. Starting MixHub retires those sessions when
    MixHub made them (a pane tagged `@hub_role=sidebar`) and nobody is looking at them; anything else stays."""

    def test_only_detached_sessions_mixhub_made_are_retired(self):
        from mixhub.cli import workspace as cli_ws
        calls = []
        listing = {
            "list-sessions": "hub\t0\nhub-2\t1\nhub-3\t0\nwork\t0\n",
            "hub": "sidebar\n\n", "hub-2": "sidebar\n\n", "hub-3": "\n\n", "work": "sidebar\n",
        }

        def fake_run(argv, *a, **k):
            calls.append(list(argv))
            if "list-sessions" in argv:
                out = listing["list-sessions"]
            elif "list-panes" in argv:
                out = listing[argv[argv.index("-t") + 1]]
            else:
                out = ""
            return subprocess.CompletedProcess(argv, 0, stdout=out, stderr="")

        with mock.patch.object(cli_ws.subprocess, "run", fake_run):
            cli_ws.retire_legacy_outer_sessions()
        killed = [c[c.index("-t") + 1] for c in calls if "kill-session" in c]
        self.assertEqual(killed, ["hub"])  # hub-2 has a client, hub-3 isn't ours, "work" isn't a MixHub name
        self.assertTrue(all(_default_server(c) for c in calls))
