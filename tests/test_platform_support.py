"""Linux / WSL support and clear failures on native Windows (which MixHub does not support)."""
import builtins
import importlib
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from mixhub.cli import main as cli_main
from mixhub.hub import metrics

MEMINFO = """MemTotal:       16000000 kB
MemFree:          500000 kB
MemAvailable:    4000000 kB
Buffers:          100000 kB
Cached:          3000000 kB
"""


class LinuxMetricsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def read(self, meminfo):
        path = Path(self.tmp.name) / "meminfo"
        path.write_text(meminfo)
        with mock.patch.object(metrics.sys, "platform", "linux"), \
                mock.patch.object(metrics, "LINUX_MEMINFO", str(path)):
            return metrics.SystemMetricsMixin.get_system_resource_metrics(force=True)

    def test_memory_comes_from_proc_meminfo(self):
        got = self.read(MEMINFO)
        self.assertAlmostEqual(got["sysMemoryTotalMB"], 16000000 / 1024, delta=1)
        self.assertAlmostEqual(got["sysMemoryUsedMB"], 12000000 / 1024, delta=1)
        self.assertEqual(got["sysMemoryPercent"], 75)
        self.assertEqual(got["sysMemoryPressureLevel"], 1)

    def test_little_available_memory_raises_the_pressure_level(self):
        warn = self.read(MEMINFO.replace("4000000 kB", "2400000 kB"))  # 15% available
        critical = self.read(MEMINFO.replace("4000000 kB", "800000 kB"))  # 5% available
        self.assertEqual(warn["sysMemoryPressureLevel"], 2)
        self.assertEqual(critical["sysMemoryPressureLevel"], 4)


class LinuxProcessesTest(unittest.TestCase):
    def test_agents_on_linux_ptys_are_found(self):
        from mixhub.hub.processes import parse_processes
        ps = ("  PID  PPID TT          RSS     ELAPSED                  STARTED COMMAND\n"
              "  100     1 pts/2      2048       04:00 Fri Oct 30 20:47:14 2026 claude --resume abc\n"
              "  101     1 tty1       2048       04:00 Fri Oct 30 20:47:14 2026 codex\n"
              "  102     1 ?          2048       04:00 Fri Oct 30 20:47:14 2026 claude\n")
        got = parse_processes(ps, boot_id="")
        self.assertEqual(sorted(p["pid"] for p in got), [100, 101])

    def test_boot_id_comes_from_proc_when_sysctl_has_no_boottime(self):
        from mixhub.proc import identity
        path = Path(tempfile.mkdtemp()) / "boot_id"
        path.write_text("223679ba-f47b-4ab6-b78c-5f35352a4447\n")
        failing = lambda *a, **k: mock.Mock(returncode=1, stdout="")
        with mock.patch.object(identity, "LINUX_BOOT_ID", str(path)):
            self.assertEqual(identity.get_boot_id(run_fn=failing, use_cache=False),
                             "linux:223679ba-f47b-4ab6-b78c-5f35352a4447")


class CliPlatformMessagesTest(unittest.TestCase):
    def test_help_and_version(self):
        import mixhub

        for flag in ("--help", "-h", "help"):
            out = io.StringIO()
            with mock.patch.object(sys, "argv", ["mixhub", flag]), mock.patch("sys.stdout", out):
                self.assertEqual(cli_main.entry(), 0)
            self.assertIn("mixhub web", out.getvalue())
            self.assertIn("mixhub doctor", out.getvalue())
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["mixhub", "--version"]), mock.patch("sys.stdout", out):
            self.assertEqual(cli_main.entry(), 0)
        self.assertEqual(out.getvalue().strip(), "mixhub " + mixhub.__version__)

    def run_cli(self, *argv):
        err = io.StringIO()
        with mock.patch.object(sys, "argv", ["mixhub", *argv]), redirect_stderr(err):
            code = cli_main.entry()
        return code, err.getvalue()

    def test_workspace_without_tmux_says_what_to_install(self):
        with mock.patch("shutil.which", return_value=None), mock.patch("subprocess.run") as run:
            code, err = self.run_cli("workspace")
        self.assertEqual(code, 1)
        self.assertIn("tmux", err)
        self.assertIn("mixhub web", err)
        self.assertNotIn("Errno", err)
        run.assert_not_called()

    def test_missing_browser_opener_prints_the_url_instead_of_an_errno(self):
        with mock.patch.object(cli_main.sys, "platform", "linux"), \
                mock.patch.object(cli_main, "start"), \
                mock.patch.object(cli_main, "ensure_state_root", return_value=tempfile.gettempdir()), \
                mock.patch.dict(os.environ, {"HUB_NO_OPEN": ""}), \
                mock.patch("shutil.which", return_value=None), \
                mock.patch("subprocess.run", side_effect=FileNotFoundError(2, "No such file", "xdg-open")):
            out = io.StringIO()
            with mock.patch("sys.stdout", out):
                code, err = self.run_cli("web")
        self.assertEqual(code, 0)
        self.assertIn(cli_main.URL, out.getvalue())
        self.assertNotIn("Errno", err)


class TerminalAppOnlyActionsTest(unittest.TestCase):
    """Resume / focus from the web UI drive Terminal.app; elsewhere they explain instead of failing on `osascript`."""

    def setUp(self):
        from mixhub.hub.hub import Hub

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        home = Path(self.tmp.name)
        sid = "11111111-2222-4333-8444-555555555555"
        tx = home / ".claude/projects/p" / f"{sid}.jsonl"
        tx.parent.mkdir(parents=True)
        tx.write_text('{"type": "user", "cwd": "/work/app", "message": {"role": "user", "content": "hi"}}\n')
        (home / "hub").mkdir()
        self.hub = Hub(str(home), str(home / "hub"))
        self.hub.processes = lambda: []
        self.hub.open_files = lambda _: {}
        self.sid = sid

    def test_resume_off_macos_names_the_command_to_run(self):
        with mock.patch("mixhub.hub.actions.sys.platform", "linux"), mock.patch("mixhub.hub.actions.run") as run:
            with self.assertRaises(ValueError) as cm:
                self.hub.action("claude", self.sid, "resume", {})
        run.assert_not_called()
        self.assertIn("claude --resume " + self.sid, str(cm.exception))
        self.assertIn("Terminal.app", str(cm.exception))

    def test_resume_dry_run_still_reports_the_command_off_macos(self):
        with mock.patch("mixhub.hub.actions.sys.platform", "linux"):
            result = self.hub.action("claude", self.sid, "resume", {"dry_run": True})
        self.assertEqual(result["command"], "cd /work/app && claude --resume " + self.sid)


if __name__ == "__main__":
    unittest.main()


class PollQueueTest(unittest.TestCase):
    """Stand-in for kqueue where the platform has none (Linux), used by the sidebar and reader wait loops."""

    def setUp(self):
        from mixhub.tui.hubtui import evq
        self.evq = evq
        self.q = evq.PollQueue()
        self.addCleanup(self.q.close)

    def test_readable_fd_wakes_the_wait(self):
        r, w = os.pipe()
        self.addCleanup(os.close, r)
        self.addCleanup(os.close, w)
        self.q.control([self.evq.kevent(r, filter=self.evq.KQ_FILTER_READ, flags=self.evq.KQ_EV_ADD)], 0, 0)
        self.assertEqual(self.q.control([], 4, 0.05), [])
        os.write(w, b"x")
        got = self.q.control([], 4, 2.0)
        self.assertEqual([(e.ident, e.filter) for e in got], [(r, self.evq.KQ_FILTER_READ)])

    def test_changed_file_reports_a_vnode_event(self):
        path = Path(tempfile.mkdtemp()) / "lock"
        path.write_text("a")
        fd = os.open(path, os.O_RDONLY)
        self.addCleanup(os.close, fd)
        self.q.control([self.evq.kevent(fd, filter=self.evq.KQ_FILTER_VNODE, flags=self.evq.KQ_EV_ADD)], 0, 0)
        self.assertEqual(self.q.control([], 4, 0.05), [])
        path.write_text("abc")
        got = self.q.control([], 4, 2.0)
        self.assertEqual([(e.ident, e.filter) for e in got], [(fd, self.evq.KQ_FILTER_VNODE)])
        self.assertEqual(self.q.control([], 4, 0.05), [])

    def test_resize_reports_a_signal_event(self):
        import signal
        sizes = iter([os.terminal_size((80, 24))] + [os.terminal_size((100, 30))] * 50)
        with mock.patch.object(self.evq.os, "get_terminal_size", lambda *a: next(sizes)):
            self.q.control([self.evq.kevent(signal.SIGWINCH, filter=self.evq.KQ_FILTER_SIGNAL, flags=self.evq.KQ_EV_ADD)], 0, 0)
            got = self.q.control([], 4, 2.0)
        self.assertEqual([(e.ident, e.filter) for e in got], [(signal.SIGWINCH, self.evq.KQ_FILTER_SIGNAL)])


class DoctorReportTest(unittest.TestCase):
    def report(self, which, hub=None, env=None):
        from mixhub.ops import doctor
        with mock.patch.object(doctor, "_which", which), mock.patch.object(doctor, "_tmux_version", lambda p: "tmux 3.4" if p else ""):
            return doctor.run_doctor(hub, env=env if env is not None else {})

    def test_reports_agents_sessions_tmux_and_terminal(self):
        hub = mock.Mock()
        hub.discover.return_value = {"a": {"tool": "claude"}, "b": {"tool": "claude"}, "c": {"tool": "codex"}}
        hub.channel_manager.return_value.status.return_value = {"channels": {"telegram": {"configured": True}, "slack": {"configured": False}}}
        doc = self.report(lambda c: "/bin/" + c if c in ("claude", "tmux") else "", hub,
                          {"TERM": "xterm-256color", "LANG": "en_US.UTF-8"})
        self.assertEqual(doc["agents"]["claude"]["session_count"], 2)
        self.assertTrue(doc["agents"]["claude"]["installed"])
        self.assertFalse(doc["agents"]["agy"]["installed"])
        self.assertEqual(doc["tmux"]["version"], "tmux 3.4")
        self.assertEqual(doc["channels"], {"telegram": {"configured": True}, "slack": {"configured": False}})
        self.assertTrue(doc["terminal"]["utf8"])
        text = doc["summary_text"]
        self.assertIn("sessions=2", text)
        self.assertIn("telegram configured", text)
        self.assertIn("TERM=xterm-256color", text)

    def test_platform_modes_and_missing_tmux(self):
        doc = self.report(lambda c: "", env={"WSL_DISTRO_NAME": "Ubuntu-24.04"})
        self.assertEqual(doc["platform"]["mode"], "wsl2:Ubuntu-24.04")
        self.assertIn("not in PATH", doc["summary_text"])
        self.assertIn("no channel configured", doc["summary_text"])
        with mock.patch.object(sys, "platform", "win32"):
            self.assertEqual(self.report(lambda c: "")["platform"]["mode"], "windows-native")


class NativeWindowsTest(unittest.TestCase):
    """Native Windows is unsupported: every command but help/version says to use WSL, never a WinError."""

    def test_every_command_points_to_wsl(self):
        for argv in ([], ["web"], ["start"], ["stop"], ["status"], ["doctor"], ["workspace"], ["sidebar"]):
            err = io.StringIO()
            with self.subTest(argv=argv), mock.patch.object(sys, "platform", "win32"), \
                    mock.patch.object(sys, "argv", ["mixhub", *argv]), redirect_stderr(err), \
                    mock.patch("subprocess.run") as run, mock.patch("subprocess.check_output") as out:
                self.assertEqual(cli_main.entry(), 1)
                self.assertIn("WSL", err.getvalue())
                self.assertNotIn("mixhub web", err.getvalue())
                run.assert_not_called()
                out.assert_not_called()


class WslClipboardTest(unittest.TestCase):
    def test_wsl_copies_through_utf16_so_cjk_survives(self):
        from mixhub.tmux import workspace
        tools = {"clip.exe": "/mnt/c/Windows/system32/clip.exe", "iconv": "/usr/bin/iconv"}
        with mock.patch.object(workspace, "IS_MAC", False), mock.patch.object(workspace.shutil, "which", tools.get):
            self.assertEqual(workspace.clipboard_copy_cmd(), "iconv -f utf-8 -t utf-16le | clip.exe")


class OpenUrlTest(unittest.TestCase):
    URL = "http://127.0.0.1:8765/"

    def opened(self, platform, tools, wsl=False, code=0):
        from mixhub.core import browser
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            return mock.Mock(returncode=code)

        with mock.patch.object(browser.sys, "platform", platform), \
                mock.patch.object(browser, "is_wsl", return_value=wsl):
            ok = browser.open_url(self.URL, run=run, which=lambda name: name if name in tools else None)
        return ok, calls

    def test_wsl_prefers_wslview_then_explorer(self):
        self.assertEqual(self.opened("linux", {"wslview", "xdg-open"}, wsl=True), (True, [["wslview", self.URL]]))
        # explorer.exe exits 1 even when it opened the page
        self.assertEqual(self.opened("linux", {"explorer.exe", "xdg-open"}, wsl=True, code=1),
                         (True, [["explorer.exe", self.URL]]))

    def test_linux_and_macos(self):
        self.assertEqual(self.opened("linux", {"xdg-open"}), (True, [["xdg-open", self.URL]]))
        self.assertEqual(self.opened("darwin", {"open"}), (True, [["open", self.URL]]))

    def test_no_opener_or_a_failing_one_reports_false(self):
        self.assertEqual(self.opened("linux", set()), (False, []))
        self.assertEqual(self.opened("linux", {"xdg-open"}, code=3)[0], False)

    def test_cli_prints_the_url_when_nothing_opened_it(self):
        out = io.StringIO()
        with mock.patch.object(sys, "argv", ["mixhub", "web"]), mock.patch("sys.stdout", out), \
                mock.patch.object(cli_main, "start"), mock.patch.object(cli_main, "open_url", return_value=False), \
                mock.patch.dict(os.environ, {"MIXHUB_NO_OPEN": "0"}):
            self.assertEqual(cli_main.entry(), 0)
        self.assertIn(cli_main.URL, out.getvalue())


class LinuxOpenFilesTest(unittest.TestCase):
    def test_cwd_and_files_come_from_proc_without_lsof(self):
        from mixhub.hub import processes
        root = Path(tempfile.mkdtemp())
        work = root / "work"
        work.mkdir()
        fd_dir = root / "proc" / "123" / "fd"
        fd_dir.mkdir(parents=True)
        os.symlink(work, root / "proc" / "123" / "cwd")
        os.symlink("/usr/bin/claude", root / "proc" / "123" / "exe")
        os.symlink(work / "s.jsonl", fd_dir / "10")
        os.symlink(work / "a.txt", fd_dir / "4")
        os.symlink("socket:[991]", fd_dir / "5")
        mixin = processes.ProcessMixin()
        mixin.warnings = []
        with mock.patch.object(processes.sys, "platform", "linux"), \
                mock.patch.object(processes, "PROC_ROOT", str(root / "proc")), \
                mock.patch.object(processes.subprocess, "run") as run:
            got = mixin.open_files([{"pid": 123}, {"pid": 456}])
        run.assert_not_called()
        self.assertEqual(got, {123: {"cwd": str(work), "paths": ["/usr/bin/claude", str(work / "a.txt"), str(work / "s.jsonl")]}})
