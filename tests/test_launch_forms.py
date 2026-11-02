"""MixHub launches its own Python children one way (`python [-S] [-P] -m <package module>`) and recognizes both
that form and the legacy repo-root script form (`python3 <root>/tui.py --header ...`) that long-running panes and
`agy` wrappers of a live session were started with.
"""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mixhub.core import launch  # noqa: E402
from mixhub.core.launch import (  # noqa: E402
    AGY_FILTER_MODULE,
    CLI_MODULE,
    TUI_MODULE,
    WEB_MODULE,
)

PY = "/opt/homebrew/bin/python3"
SID = "44444444-4444-4444-8444-444444444444"
LEGACY = {
    "tui": f"{PY} /srv/mixhub-checkout/tui.py --header claude {SID}",
    "web": f"{PY} -S /srv/mixhub-checkout/server.py",
    "agy_filter": f"{PY} /srv/mixhub-checkout/agy_claude_filter.py --conversation {SID}",
}
NEW = {
    "tui": f"{PY} -P -m mixhub.tui --header claude {SID}",
    "web": f"{PY} -S -P -m mixhub.web",
    "agy_filter": f"{PY} -P -m mixhub.filters.agy_claude --conversation {SID}",
}


class LaunchCommandLineTests(unittest.TestCase):
    """The generated command lines (unit level: nothing is started)."""

    def test_checkout_children_get_pythonpath_and_safe_path_flag(self) -> None:
        self.assertTrue(launch.needs_pythonpath())  # the test suite runs from a source checkout
        argv = launch.module_argv(TUI_MODULE, "--header", "claude", SID)
        self.assertEqual(argv[0], sys.executable)
        self.assertEqual(argv[-5:], ["-m", "mixhub.tui", "--header", "claude", SID])
        self.assertEqual("-P" in argv, sys.version_info >= (3, 11))
        self.assertNotIn("-S", argv)
        self.assertEqual(launch.module_command(TUI_MODULE, "--reader")[:2], ["env", f"PYTHONPATH={REPO_ROOT}"])
        env = launch.child_env({"PYTHONPATH": "/x", "A": "1"})
        self.assertEqual(env["PYTHONPATH"], f"{REPO_ROOT}{os.pathsep}/x")
        self.assertEqual(env["A"], "1")
        self.assertIn("-S", launch.module_argv(WEB_MODULE, no_site=True))

    def test_installed_children_need_no_pythonpath_and_keep_site(self) -> None:
        launch.needs_pythonpath.cache_clear()
        try:
            with patch.object(launch, "_default_import_dirs", return_value={os.path.realpath(launch.CODE_ROOT)}):
                self.assertFalse(launch.needs_pythonpath())
                self.assertEqual(launch.module_command(TUI_MODULE, "--reader")[0], sys.executable)
                self.assertNotIn("-S", launch.module_argv(WEB_MODULE, no_site=True))
                self.assertNotIn("PYTHONPATH", launch.child_env({}))
                self.assertTrue(launch.module_shell(TUI_MODULE, "--sidebar").startswith(f"exec {shlex.quote(sys.executable)} "))
        finally:
            launch.needs_pythonpath.cache_clear()

    def test_pane_commands_keep_the_flag_tokens(self) -> None:
        from mixhub.tmux import commands

        header = commands._header_cmd("claude", SID)
        self.assertTrue(header.startswith("exec env PYTHONPATH="))
        self.assertTrue(header.endswith(f" -m mixhub.tui --header claude {SID}"))
        self.assertTrue(commands._reader_cmd().endswith(" -m mixhub.tui --reader"))
        sidebar = commands._sidebar_cmd({"HUB_OUTER_SESSION": "hub-2", "HUB_POOL_SESSION": "pool-view-2"})
        self.assertIn(" HUB_OUTER_SESSION=hub-2 HUB_POOL_SESSION=pool-view-2 ", sidebar)
        self.assertTrue(sidebar.endswith(" -m mixhub.tui --sidebar"))
        wrapped = commands._build_hubpool_cli_wrapper_cmd("claude", SID, "w-claude-x", "/tmp", "claude --resume x")
        self.assertTrue(wrapped.endswith(f' -m mixhub.tui --on-exit claude {SID} w-claude-x "$rc"'))
        agy = commands.interactive_resume_cmd({"tool": "agy", "id": SID, "cwd": "/tmp"})
        self.assertEqual(agy[-4:], ["-m", AGY_FILTER_MODULE, "--conversation", SID])
        for cmd in (header, sidebar, commands._reader_cmd(), shlex.join(agy)):
            self.assertNotIn("tui.py", cmd)
            self.assertNotIn("agy_claude_filter.py", cmd)
        # The new-window regex that recovers a header's target from `pane_start_command` still matches
        import re
        self.assertEqual(re.search(r"--header\s+(\S+)\s+(\S+)", header).groups(), ("claude", SID))

    def test_attach_hooks_and_web_start_use_the_cli_module(self) -> None:
        import mixhub.cli.workspace as ws

        calls = []
        with patch.object(ws.subprocess, "run", side_effect=lambda cmd, *a, **k: calls.append(cmd) or subprocess.CompletedProcess(cmd, 1, "", "")):
            ws.apply_window_title_and_hooks("hub-test")
        hooks = [c for c in calls if c[:4] == ["tmux", "-L", "mixhub", "set-hook"]]
        self.assertEqual(len(hooks), 2)
        for hook in hooks:
            inner = shlex.split(shlex.split(hook[-1])[-1])
            self.assertEqual(inner[-4:-1], ["-m", CLI_MODULE, inner[-2]])
            self.assertIn(inner[-2], ("on-detach", "on-attach"))
            self.assertEqual(inner[-1], "hub-test")

    def test_release_pythonpath_drops_only_the_injected_root(self) -> None:
        env = {"PYTHONPATH": f"{REPO_ROOT}{os.pathsep}/user/lib"}
        launch.release_pythonpath(env)
        self.assertEqual(env, {"PYTHONPATH": "/user/lib"})
        env = {"PYTHONPATH": str(REPO_ROOT)}
        launch.release_pythonpath(env)
        self.assertEqual(env, {})
        env = {"PYTHONPATH": f"/user/lib{os.pathsep}{REPO_ROOT}"}
        launch.release_pythonpath(env)
        self.assertEqual(env["PYTHONPATH"], f"/user/lib{os.pathsep}{REPO_ROOT}")

    def test_module_form_runs_from_a_hostile_working_directory(self) -> None:
        """`-m` from a project dir containing `mixhub/` and a broken `json.py` still runs this checkout's package."""
        with __import__("tempfile").TemporaryDirectory() as cwd:
            (Path(cwd) / "mixhub").mkdir()
            (Path(cwd) / "mixhub" / "__init__.py").write_text("raise SystemExit('shadowed mixhub')\n")
            (Path(cwd) / "json.py").write_text("raise SystemExit('shadowed json')\n")
            env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONSAFEPATH")}
            cmd = shlex.join(launch.module_command(CLI_MODULE, "no-such-command"))
            proc = subprocess.run(["/bin/sh", "-c", cmd], cwd=cwd, env=env, capture_output=True, text=True, timeout=60)
            if sys.version_info >= (3, 11):
                self.assertEqual(proc.returncode, 2, proc.stderr)
                self.assertIn("Usage: mixhub [command]", proc.stderr)
            code = "import json, os, mixhub; print(mixhub.__file__); print(os.environ.get('PYTHONPATH', ''))"
            out = subprocess.run(["/bin/sh", "-c", shlex.join(launch.code_command(code))], cwd=cwd, env=env,
                                 capture_output=True, text=True, timeout=60)
            if sys.version_info >= (3, 11):
                self.assertEqual(out.returncode, 0, out.stderr)
                self.assertEqual(out.stdout.splitlines()[0], str(REPO_ROOT / "mixhub" / "__init__.py"))

    def test_entry_modules_release_the_injected_pythonpath_for_their_children(self) -> None:
        code = "import os; print(repr(os.environ.get('PYTHONPATH')))"
        mod_dir = REPO_ROOT / "mixhub"
        for entry in (mod_dir / "__main__.py", mod_dir / "tui" / "__main__.py", mod_dir / "web" / "__main__.py",
                      mod_dir / "filters" / "agy_claude" / "__main__.py"):
            src = entry.read_text(encoding="utf-8")
            self.assertIn("release_pythonpath()", src.split('if __name__ == "__main__":', 1)[1], entry)
        env = launch.child_env({k: v for k, v in os.environ.items() if k != "PYTHONPATH"})
        released = subprocess.run(
            [sys.executable, "-c", "from mixhub.core.launch import release_pythonpath; release_pythonpath(); " + code],
            env=env, capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(released.stdout.strip(), "None", released.stderr)

    @unittest.skipUnless(shutil.which("tmux"), "needs tmux")
    def test_pane_command_runs_in_a_private_tmux_server(self) -> None:
        sock = f"mixhub-pkgtest-{uuid.uuid4().hex[:8]}"
        env = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE")}

        def tmux(*args):
            return subprocess.run(["tmux", "-L", sock, "-f", "/dev/null", *args], env=env, capture_output=True,
                                  text=True, timeout=15)

        # A side-effect-free CLI command; the `--header` flag rides along in a shell comment
        pane_cmd = launch.module_shell(CLI_MODULE, "shell-hook") + f" # --header claude {SID}"
        try:
            tmux("new-session", "-d", "-s", "t", "-x", "120", "-y", "40", "sleep 30")
            tmux("set-option", "-g", "remain-on-exit", "on")
            tmux("new-window", "-d", "-t", "t:", "-n", "w", pane_cmd)
            start_cmd = status = ""
            for _ in range(100):
                res = tmux("list-panes", "-t", "t:w", "-F", "#{pane_dead}|#{pane_dead_status}|#{pane_start_command}")
                dead, status, start_cmd = (res.stdout.strip().split("|", 2) + ["", ""])[:3]
                if dead == "1":
                    break
                __import__("time").sleep(0.1)
            self.assertEqual(dead, "1")
            panes = tmux("list-panes", "-a", "-F", "#{session_name}:#{window_name} dead=#{pane_dead} "
                                                   "status=#{pane_dead_status} pid=#{pane_pid} cmd=#{pane_current_command}")
            # Ubuntu's tmux leaves #{pane_dead_status} empty; macOS fills it in
            self.assertIn(status, ("0", ""), f"panes: {panes.stdout!r} {panes.stderr!r}; screen: "
                             f"{tmux('capture-pane', '-p', '-t', 't:w').stdout.strip()[-300:]!r}")
            self.assertIn("-m mixhub shell-hook", start_cmd)
            self.assertIn("--header", start_cmd)  # what `reload_workspace_panes` and external tools match on
            # Exit status comes from `pane_dead_status`: not every tmux build prints the "Pane is dead" line
            text = tmux("capture-pane", "-p", "-S", "-", "-t", "t:w").stdout
            self.assertIn("tmux -L mixhub has-session", text)
        finally:
            sock_path = tmux("display-message", "-p", "#{socket_path}").stdout.strip()
            tmux("kill-server")
            if sock_path.endswith(sock) and os.path.exists(sock_path):
                os.unlink(sock_path)  # tmux leaves the socket file of this private server behind


class ProcessRecognitionTests(unittest.TestCase):
    """Both the legacy script form and the new module form are recognized everywhere processes are matched."""

    def test_command_runs_accepts_both_forms(self) -> None:
        for role in LEGACY:
            self.assertTrue(launch.command_runs(LEGACY[role], role), role)
            self.assertTrue(launch.command_runs(NEW[role], role), role)
            self.assertTrue(launch.command_runs(f"env PYTHONPATH=/r {NEW[role]}", role), role)
            others = [r for r in LEGACY if r != role]
            for other in others:
                self.assertFalse(launch.command_runs(NEW[other], role), (role, other))
                self.assertFalse(launch.command_runs(LEGACY[other], role), (role, other))
        self.assertFalse(launch.command_runs("vim notes.txt", "tui"))
        self.assertFalse(launch.command_runs(f"{PY} -c 'import mixhub.tui'", "tui"))
        self.assertEqual(launch.python_module_invocation(f"{PY} -X utf8 -W ignore -m mixhub.web"), ("mixhub.web", []))
        self.assertEqual(launch.python_module_invocation(["python3.13", "-mmixhub.tui", "--reader"]), ("mixhub.tui", ["--reader"]))
        self.assertIsNone(launch.python_module_invocation(f"{PY} script.py -m mixhub.web"))

    def test_parse_processes_finds_agy_behind_either_filter_form(self) -> None:
        from mixhub.hub.processes import parse_processes

        for cmd in (LEGACY["agy_filter"], NEW["agy_filter"]):
            procs = parse_processes(f"100 1 ttys001 1024 00:01 {cmd}")
            self.assertEqual(len(procs), 1, cmd)
            self.assertEqual(procs[0]["tool"], "agy")
            self.assertEqual(procs[0]["args"], ["--conversation", SID])
        self.assertEqual(parse_processes(f"100 1 ttys001 1024 00:01 {NEW['tui']}"), [])

    def test_classify_treats_either_filter_form_as_the_agy_family(self) -> None:
        from mixhub.proc.classify import classify_proc_args

        for cmd in (LEGACY["agy_filter"], NEW["agy_filter"]):
            fam, kind, tail = classify_proc_args(cmd)
            self.assertEqual((fam, kind), ("agy", "agent"), cmd)
            self.assertEqual(tail, ["--conversation", SID])

    def test_sleep_blocker_aux_rules_cover_both_forms(self) -> None:
        from mixhub.hub.sleep_blockers import SleepBlockersMixin

        aux = SleepBlockersMixin._is_aux_single_process
        for cmd in (LEGACY["agy_filter"], NEW["agy_filter"], LEGACY["tui"], NEW["tui"],
                    f"{PY} -P -m mixhub.tui --sidebar", f"{PY} /srv/mixhub-checkout/tui.py --sidebar"):
            self.assertTrue(aux(cmd, False), cmd)
        self.assertFalse(aux(f"{PY} -P -m mixhub.tui --reader", False))

    def test_web_stop_signals_either_daemon_form_and_refuses_others(self) -> None:
        from mixhub.cli import daemon

        legacy = f"{PY} -S {os.path.join(daemon.ROOT, 'server.py')}"
        self.assertTrue(daemon.is_web_daemon_command(legacy))
        self.assertTrue(daemon.is_web_daemon_command(NEW["web"]))
        self.assertFalse(daemon.is_web_daemon_command(LEGACY["web"]))  # another checkout's server.py
        self.assertFalse(daemon.is_web_daemon_command(f"{PY} -m http.server 8765"))
        for cmd, ok in ((NEW["web"], True), (f"{PY} -m http.server 8765", False)):
            killed = []
            health = iter([{"pid": 4242}, None, None])
            with patch.object(daemon, "health", side_effect=lambda: next(health)), \
                    patch.object(daemon, "PID_FILE", "/nonexistent/.hub.pid"), \
                    patch.object(daemon.subprocess, "check_output", return_value=cmd + "\n"), \
                    patch.object(daemon.os, "kill", side_effect=lambda pid, sig: killed.append(pid)):
                if ok:
                    daemon.stop()
                    self.assertEqual(killed, [4242])
                else:
                    with self.assertRaises(RuntimeError):
                        daemon.stop()
                    self.assertEqual(killed, [])

    def test_reload_matches_old_and_new_pane_start_commands(self) -> None:
        from mixhub.cli.daemon import reload_workspace_panes

        class Gateway:
            def __init__(self):
                self.respawned = []

            def list_panes(self, sess, fmt, server, **kw):
                if server != "pool":
                    return []
                # tmux 3.6 reports `pane_start_command` wrapped in double quotes
                return [f'%1|"exec {LEGACY["tui"]}"', f"%2|exec env PYTHONPATH=/r {NEW['tui']}", "%3|exec claude --resume x"]

            def run(self, args, server, **kw):
                self.respawned.append(args[-1])

        gw = Gateway()
        self.assertEqual(reload_workspace_panes(gw), ["pool:%1", "pool:%2"])
        # Every pane comes back on the current launch form (a legacy `tui.py` pane too), with its own arguments
        from mixhub.core.launch import TUI_MODULE, module_shell

        self.assertEqual(gw.respawned, [module_shell(TUI_MODULE, "--header", "claude", SID)] * 2)

    def test_reload_keeps_the_sidebar_session_env(self) -> None:
        from mixhub.cli.daemon import reload_workspace_panes
        from mixhub.core.launch import TUI_MODULE, module_shell

        class Gateway:
            respawned = []

            def list_panes(self, sess, fmt, server, **kw):
                if server != "outer":
                    return []
                return [f"%7|exec env HUB_OUTER_SESSION=hub2 HUB_POOL_SESSION=pool2 {PY} /srv/x/tui.py --sidebar"]

            def run(self, args, server, **kw):
                self.respawned.append(args[-1])

        gw = Gateway()
        self.assertEqual(reload_workspace_panes(gw), ["outer:%7"])
        env = {"HUB_OUTER_SESSION": "hub2", "HUB_POOL_SESSION": "pool2"}
        self.assertEqual(gw.respawned, [module_shell(TUI_MODULE, "--sidebar", extra_env=env)])


if __name__ == "__main__":
    unittest.main()
