import json
import os
import signal
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import proc_guard
import server


class ProcGuardAncestryTests(unittest.TestCase):
    def test_build_proc_tree_and_interactive_root_has_no_agent_ancestor(self):
        ps_snapshot = """
    1     0 Mon Oct 19 09:00:00 2026 /sbin/launchd
  500     1 Mon Oct 19 09:05:00 2026 /opt/homebrew/bin/tmux -L hubpool new-session -d
  600   500 Sun Oct 25 17:00:00 2026 -zsh
  700   600 Sun Oct 25 17:01:00 2026 claude --resume 11111111-1111-4111-8111-111111111111
  800   500 Sun Oct 25 17:02:00 2026 /Applications/Claude.app/Contents/MacOS/Claude
  801   800 Sun Oct 25 17:02:05 2026 claude --resume 22222222-2222-4222-8222-222222222222
"""
        tree = proc_guard.build_proc_tree(ps_output=ps_snapshot)
        self.assertIn(700, tree)
        self.assertIsNone(proc_guard.has_agent_ancestor(700, tree))
        # Desktop Electron app (`Claude` with capital C) must not be treated as CLI `claude`
        self.assertIsNone(proc_guard.has_agent_ancestor(801, tree))

    def test_same_family_launcher_wrappers_collapse_without_false_positive(self):
        ps_snapshot = """
  500     1 Mon Oct 19 09:05:00 2026 /opt/homebrew/bin/tmux -L hubpool
 1000   500 Sun Oct 25 17:10:00 2026 node /opt/homebrew/bin/codex resume 33333333-3333-4333-8333-333333333333
 1001  1000 Sun Oct 25 17:10:00 2026 /opt/homebrew/lib/node_modules/@openai/codex/vendor/aarch64-apple-darwin/codex/codex resume 33333333-3333-4333-8333-333333333333
 2000   500 Sun Oct 25 17:11:00 2026 /opt/homebrew/bin/python3 /Users/alice/.gemini/session-hub/agy_claude_filter.py --conversation 44444444-4444-4444-8444-444444444444
 2001  2000 Sun Oct 25 17:11:01 2026 /Users/alice/.gemini/antigravity-cli/bin/agy --conversation 44444444-4444-4444-8444-444444444444
 3000   500 Sun Oct 25 17:12:00 2026 /bin/zsh /Users/alice/.local/bin/cc-ds --resume 55555555-5555-4555-8555-555555555555
 3001  3000 Sun Oct 25 17:12:01 2026 claude --resume 55555555-5555-4555-8555-555555555555
"""
        tree = proc_guard.build_proc_tree(ps_output=ps_snapshot)
        self.assertIsNone(proc_guard.has_agent_ancestor(1001, tree))
        self.assertIsNone(proc_guard.has_agent_ancestor(2001, tree))
        self.assertIsNone(proc_guard.has_agent_ancestor(3001, tree))

    def test_detects_agent_and_dispatch_script_ancestors_across_shells(self):
        ps_snapshot = """
  500     1 Mon Oct 19 09:05:00 2026 /opt/homebrew/bin/tmux -L hubpool
 1100   500 Sun Oct 25 17:20:00 2026 claude --resume aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa
 1101  1100 Sun Oct 25 17:21:00 2026 /bin/zsh -c codex resume bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb
 1102  1101 Sun Oct 25 17:21:01 2026 node /opt/homebrew/bin/codex resume bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb
 1103  1102 Sun Oct 25 17:21:01 2026 /vendor/codex/codex resume bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb
 1200   500 Sun Oct 25 17:22:00 2026 /bin/bash /Users/alice/.gemini/bin/agent-ask --deep test
 1201  1200 Sun Oct 25 17:22:01 2026 /bin/bash /Users/alice/.claude/scripts/api-run.sh --model opus test
 1202  1201 Sun Oct 25 17:22:02 2026 claude --resume cccccccc-cccc-4ccc-8ccc-cccccccccccc
 1300   500 Sun Oct 25 17:23:00 2026 /bin/zsh /Users/alice/.gemini/bin/agent-run --level L2 task
 1301  1300 Sun Oct 25 17:23:01 2026 codex exec -s workspace-write task
 1400   500 Sun Oct 25 17:24:00 2026 agy --conversation dddddddd-dddd-4ddd-8ddd-dddddddddddd
 1401  1400 Sun Oct 25 17:24:10 2026 agy --conversation eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee
"""
        prefs = Path(tempfile.mkdtemp()) / "prefs.json"  # the wrapper scripts are the user's own, declared here
        prefs.write_text(json.dumps({"dispatch_scripts": ["agent-ask", "agent-run", "api-run.sh"]}))
        env_patch = patch.dict(os.environ, {"MIXHUB_PREFERENCES_PATH": str(prefs)})
        env_patch.start()
        self.addCleanup(env_patch.stop)
        tree = proc_guard.build_proc_tree(ps_output=ps_snapshot)
        self.assertEqual(proc_guard.has_agent_ancestor(1103, tree), "subagent_ppid")
        self.assertEqual(proc_guard.has_agent_ancestor(1202, tree), "subagent_ppid")
        self.assertEqual(proc_guard.has_agent_ancestor(1301, tree), "subagent_ppid")
        self.assertEqual(proc_guard.has_agent_ancestor(1401, tree), "subagent_ppid")

    def test_detects_mixhub_internal_beacon_on_self_or_ancestor(self):
        ps_snapshot = """
  500     1 Mon Oct 19 09:05:00 2026 /opt/homebrew/bin/tmux -L hubpool
 1500   500 Sun Oct 25 17:25:00 2026 env MIXHUB_INTERNAL=1 /bin/zsh -c claude
 1501  1500 Sun Oct 25 17:25:01 2026 claude --resume ffffffff-ffff-4fff-8fff-ffffffffffff
 1600   500 Sun Oct 25 17:26:00 2026 MIXHUB_INTERNAL=1 codex resume 99999999-9999-4999-8999-999999999999
"""
        tree = proc_guard.build_proc_tree(ps_output=ps_snapshot)
        self.assertEqual(proc_guard.has_agent_ancestor(1501, tree), "subagent_ppid")
        self.assertEqual(proc_guard.has_agent_ancestor(1600, tree), "subagent_ppid")

    def test_recycled_parent_start_time_and_cycle_safety(self):
        # Parent 2000 started AFTER child 2001 -> recycled PID, must stop walk and not flag false positive
        ps_snapshot = """
 2000     1 Sun Oct 25 18:00:00 2026 claude --resume 11111111-1111-4111-8111-111111111111
 2001  2000 Sun Oct 25 17:00:00 2026 codex resume 22222222-2222-4222-8222-222222222222
"""
        tree = proc_guard.build_proc_tree(ps_output=ps_snapshot)
        self.assertIsNone(proc_guard.has_agent_ancestor(2001, tree))

        # Cycle safety (3000 -> 3001 -> 3000)
        cycle_tree = {
            3000: proc_guard.ProcNode(pid=3000, ppid=3001, start_time="", args="-zsh"),
            3001: proc_guard.ProcNode(pid=3001, ppid=3000, start_time="", args="-zsh"),
        }
        self.assertIsNone(proc_guard.has_agent_ancestor(3000, cycle_tree, max_depth=8))

    def test_server_parse_processes_filters_subagent_ppid_and_captures_identity(self):
        ps_data = """
  500     1 ??      4096 10:00 Mon Oct 19 09:05:00 2026 /opt/homebrew/bin/tmux -L hubpool
  700   500 ttys001 102400 05:00 Sun Oct 25 17:01:00 2026 claude --resume 11111111-1111-4111-8111-111111111111
  701   700 ttys002 51200 01:00 Sun Oct 25 17:02:00 2026 codex resume 22222222-2222-4222-8222-222222222222
  702   500 ttys003 20480 02:00 Sun Oct 25 17:03:00 2026 /bin/bash /Users/alice/.gemini/bin/agent-ask brief
  703   702 ttys003 81920 01:59 Sun Oct 25 17:03:01 2026 agy --conversation 33333333-3333-4333-8333-333333333333
"""
        prefs = Path(tempfile.mkdtemp()) / "prefs.json"
        prefs.write_text(json.dumps({"dispatch_scripts": ["agent-ask"]}))
        env_patch = patch.dict(os.environ, {"MIXHUB_PREFERENCES_PATH": str(prefs)})
        env_patch.start()
        self.addCleanup(env_patch.stop)
        sub_out = {}
        procs = server.parse_processes(ps_data, boot_id="bt:1772000000", subagent_pids_out=sub_out)
        self.assertEqual([p["pid"] for p in procs], [700])
        self.assertEqual(procs[0]["boot_id"], "bt:1772000000")
        self.assertEqual(procs[0]["start_time"], "Sun Oct 25 17:01:00 2026")
        self.assertIsInstance(procs[0]["identity"], proc_guard.ProcessIdentity)
        self.assertIn(701, sub_out)
        self.assertIn(703, sub_out)


class ProcessTripleGuardTests(unittest.TestCase):
    def test_safe_kill_process_succeeds_when_all_guards_match(self):
        ident = proc_guard.ProcessIdentity(
            boot_id="bt:1772000000",
            pid=4321,
            start_time="Sun Oct 25 17:01:00 2026",
        )
        fake_res = MagicMock(returncode=0, stdout="Sun Oct 25 17:01:00 2026 claude --resume abc\n")
        killed = []
        ok = proc_guard.safe_kill_process(
            ident,
            signal.SIGTERM,
            run_fn=lambda *a, **kw: fake_res,
            kill_fn=lambda pid, sig: killed.append((pid, sig)),
            current_boot_id="bt:1772000000",
        )
        self.assertTrue(ok)
        self.assertEqual(killed, [(4321, signal.SIGTERM)])

    def test_safe_kill_process_rejects_boot_id_mismatch(self):
        ident = proc_guard.ProcessIdentity(
            boot_id="bt:1772000000",
            pid=4321,
            start_time="Sun Oct 25 17:01:00 2026",
        )
        fake_res = MagicMock(returncode=0, stdout="Sun Oct 25 17:01:00 2026 claude --resume abc\n")
        killed = []
        ok = proc_guard.safe_kill_process(
            ident,
            signal.SIGTERM,
            run_fn=lambda *a, **kw: fake_res,
            kill_fn=lambda pid, sig: killed.append((pid, sig)),
            current_boot_id="bt:1772999999",
        )
        self.assertFalse(ok)
        self.assertEqual(killed, [])

    def test_safe_kill_process_rejects_pid_reuse_start_time_mismatch(self):
        ident = proc_guard.ProcessIdentity(
            boot_id="bt:1772000000",
            pid=4321,
            start_time="Sun Oct 25 17:01:00 2026",
        )
        # Same PID 4321, even running an agent command, but started 5 seconds later (recycled PID)
        fake_res = MagicMock(returncode=0, stdout="Sun Oct 25 17:01:05 2026 claude --resume other\n")
        killed = []
        ok = proc_guard.safe_kill_process(
            ident,
            signal.SIGTERM,
            run_fn=lambda *a, **kw: fake_res,
            kill_fn=lambda pid, sig: killed.append((pid, sig)),
            current_boot_id="bt:1772000000",
        )
        self.assertFalse(ok)
        self.assertEqual(killed, [])

    def test_safe_kill_process_rejects_non_agent_command(self):
        ident = proc_guard.ProcessIdentity(
            boot_id="bt:1772000000",
            pid=4321,
            start_time="Sun Oct 25 17:01:00 2026",
        )
        # Recycled PID or wrong process running Postgres / WindowServer / python server
        for non_agent_cmd in (
            "/usr/sbin/cfprefsd agent",
            "postgres: writer process",
            "/Applications/Claude.app/Contents/MacOS/Claude",
            "python3 -m http.server 8765",
        ):
            fake_res = MagicMock(returncode=0, stdout=f"Sun Oct 25 17:01:00 2026 {non_agent_cmd}\n")
            killed = []
            ok = proc_guard.safe_kill_process(
                ident,
                signal.SIGTERM,
                run_fn=lambda *a, **kw: fake_res,
                kill_fn=lambda pid, sig: killed.append((pid, sig)),
                current_boot_id="bt:1772000000",
            )
            self.assertFalse(ok, f"Should have rejected non-agent command: {non_agent_cmd}")
            self.assertEqual(killed, [])

    def test_safe_kill_process_rejects_protected_pids(self):
        for bad_pid in (-1, 0, 1, os.getpid(), os.getppid()):
            ident = proc_guard.ProcessIdentity(
                boot_id="bt:1772000000",
                pid=bad_pid,
                start_time="Sun Oct 25 17:01:00 2026",
            )
            killed = []
            ok = proc_guard.safe_kill_process(
                ident,
                signal.SIGTERM,
                kill_fn=lambda pid, sig: killed.append((pid, sig)),
                current_boot_id="bt:1772000000",
            )
            self.assertFalse(ok)
            self.assertEqual(killed, [])

    def test_hub_hibernate_uses_process_triple_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "home"
            root = Path(tmp) / "root"
            home.mkdir()
            root.mkdir()
            hub = server.Hub(str(home), str(root))
            session = {"id": "11111111-1111-4111-8111-111111111111", "tool": "claude", "cwd": "/tmp"}
            proc_with_mismatch = {
                "pid": 54321,
                "tty": "ttys009",
                "boot_id": "bt:1772000000",
                "start_time": "Sun Oct 25 17:01:00 2026",
            }
            with patch("server.os.kill") as mock_kill, patch(
                "mixhub.proc.guard.get_boot_id", return_value="bt:1772000000"
            ), patch(
                "mixhub.proc.guard.inspect_live_process",
                return_value=("Sun Oct 25 17:09:99 2026", "/usr/sbin/syslogd"),
            ):
                res = hub.hibernate(session, [proc_with_mismatch], dry=False)
                mock_kill.assert_not_called()
                self.assertEqual(res["pendingPids"], [])


if __name__ == "__main__":
    unittest.main()
