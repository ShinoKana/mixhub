"""What a stranger sees on first run: an old tmux, no agent CLI, Linux without a clipboard tool."""
import unittest
from unittest import mock

from mixhub.cli import workspace as cli_ws
from mixhub.ops import doctor
from mixhub.tmux.gateway import tmux_version_tuple


class TmuxVersionTest(unittest.TestCase):
    def test_parses_release_and_dev_builds(self):
        self.assertEqual(tmux_version_tuple("tmux 3.6a"), (3, 6))
        self.assertEqual(tmux_version_tuple("tmux 2.9"), (2, 9))
        self.assertEqual(tmux_version_tuple("tmux next-3.5"), (3, 5))
        self.assertIsNone(tmux_version_tuple(""))

    def test_workspace_refuses_a_tmux_older_than_3_0_with_the_reason(self):
        with mock.patch.object(cli_ws.shutil, "which", return_value="/usr/bin/tmux"), \
                mock.patch.object(cli_ws, "_tmux_version_text", return_value="tmux 2.9"):
            reason = cli_ws._workspace_unavailable()
        self.assertIn("2.9", reason)
        self.assertIn("3.0", reason)
        with mock.patch.object(cli_ws.shutil, "which", return_value="/usr/bin/tmux"), \
                mock.patch.object(cli_ws, "_tmux_version_text", return_value="tmux 3.4"):
            self.assertEqual(cli_ws._workspace_unavailable(), "")


class DoctorFirstRunTest(unittest.TestCase):
    def report(self, which, version="tmux 3.4", platform="darwin", env=None):
        with mock.patch.object(doctor, "_which", side_effect=which), \
                mock.patch.object(doctor, "_tmux_version", return_value=version), \
                mock.patch.object(doctor.sys, "platform", platform):
            return doctor.run_doctor(None, env=env or {})["summary_text"]

    def test_no_agent_cli_says_what_to_install(self):
        text = self.report(lambda c: "/usr/bin/tmux" if c == "tmux" else "")
        self.assertIn("No agent CLI found", text)

    def test_old_tmux_is_a_warning(self):
        text = self.report(lambda c: "/usr/bin/" + c, version="tmux 2.9")
        self.assertIn("[WARN] tmux 2.9", text)
        self.assertIn("3.0", text)

    def test_linux_without_a_clipboard_tool_is_told(self):
        text = self.report(lambda c: "" if c in ("xclip", "xsel", "wl-copy") else "/usr/bin/" + c, platform="linux")
        self.assertIn("xclip", text)
        text = self.report(lambda c: "/usr/bin/" + c, platform="linux")
        self.assertNotIn("Clipboard", text)


if __name__ == "__main__":
    unittest.main()
