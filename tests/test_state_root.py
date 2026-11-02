"""Code location vs state location: `mixhub.core.paths.resolve_state_root` and the test-suite isolation guard.

`STATE_ROOT` = `$MIXHUB_HOME`, else the repository root of a source checkout (existing checkouts keep their state
files in place), else `$XDG_DATA_HOME/mixhub` / `~/.local/share/mixhub`. Every default state path derives from it.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mixhub.core import paths  # noqa: E402


def _inside_checkout(path: str) -> bool:
    real, repo = os.path.realpath(path), os.path.realpath(REPO_ROOT)
    return real == repo or real.startswith(repo + os.sep)


class ResolveStateRootTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="mixhub-stateroot-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.checkout = self.tmp / "checkout"
        (self.checkout / "mixhub").mkdir(parents=True)
        (self.checkout / "pyproject.toml").write_text("[project]\nname = 'x'\n")
        self.site = self.tmp / "site-packages"
        (self.site / "mixhub").mkdir(parents=True)

    def test_mixhub_home_wins(self) -> None:
        (self.checkout / ".git").mkdir()
        env = {"MIXHUB_HOME": "~/somewhere", "HOME": str(self.tmp), "XDG_DATA_HOME": "/xdg"}
        self.assertEqual(paths.resolve_state_root(env, str(self.checkout)),
                         os.path.abspath(os.path.expanduser("~/somewhere")))

    def test_source_checkout_keeps_state_in_the_repo(self) -> None:
        env = {"HOME": str(self.tmp)}
        (self.checkout / ".git").mkdir()
        self.assertEqual(paths.resolve_state_root(env, str(self.checkout)), str(self.checkout))
        shutil.rmtree(self.checkout / ".git")
        (self.checkout / ".git").write_text("gitdir: /elsewhere/.git/worktrees/x\n")  # a git worktree
        self.assertEqual(paths.resolve_state_root(env, str(self.checkout)), str(self.checkout))
        (self.checkout / "pyproject.toml").unlink()  # `.git` alone is not a MixHub checkout
        self.assertNotEqual(paths.resolve_state_root(env, str(self.checkout)), str(self.checkout))

    def test_installed_code_uses_xdg_then_local_share(self) -> None:
        self.assertEqual(paths.resolve_state_root({"HOME": str(self.tmp), "XDG_DATA_HOME": "/xdg/data"}, str(self.site)),
                         "/xdg/data/mixhub")
        for xdg in ("", "relative/dir"):  # XDG requires an absolute path
            self.assertEqual(paths.resolve_state_root({"HOME": str(self.tmp), "XDG_DATA_HOME": xdg}, str(self.site)),
                             str(self.tmp / ".local" / "share" / "mixhub"))

    def test_this_checkout_resolves_to_itself_without_mixhub_home(self) -> None:
        """Zero-migration guarantee: a checkout keeps using `<repo>/data`, `<repo>/Preferences`, ... in place."""
        env = {k: v for k, v in os.environ.items() if k not in ("MIXHUB_HOME", "PYTHONPATH")}
        code = "import mixhub.core.paths as p, mixhub.hub.constants as c, mixhub.cli.daemon as d; print(p.STATE_ROOT); print(c.ROOT); print(d.PID_FILE)"
        out = subprocess.run([sys.executable, "-c", code], cwd=str(REPO_ROOT), env=env, capture_output=True,
                             text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        state, hub_root, pid_file = out.stdout.splitlines()
        self.assertEqual(os.path.realpath(state), os.path.realpath(REPO_ROOT))
        self.assertEqual(os.path.realpath(hub_root), os.path.realpath(REPO_ROOT))
        self.assertEqual(os.path.realpath(pid_file), os.path.realpath(REPO_ROOT / ".hub.pid"))

    def test_an_installed_copy_never_resolves_state_next_to_its_code(self) -> None:
        shutil.rmtree(self.site / "mixhub")
        shutil.copytree(REPO_ROOT / "mixhub", self.site / "mixhub", ignore=shutil.ignore_patterns("__pycache__"))
        home = self.tmp / "home"
        home.mkdir()
        env = {k: v for k, v in os.environ.items() if k not in ("MIXHUB_HOME", "XDG_DATA_HOME", "PYTHONPATH")}
        env.update(HOME=str(home), PYTHONPATH=str(self.site))
        code = (
            "import mixhub.core.paths as p, mixhub.tui.paths as t, mixhub.hub.hub as h\n"
            "print(p.CODE_ROOT); print(p.STATE_ROOT); print(t.SEEN_PATH); print(h.Hub().root)\n"
        )
        out = subprocess.run([sys.executable, "-c", code], cwd=str(self.tmp), env=env,
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        code_root, state, seen, hub_root = out.stdout.splitlines()
        expected = str(home / ".local" / "share" / "mixhub")
        self.assertEqual(os.path.realpath(code_root), os.path.realpath(self.site))
        self.assertEqual(state, expected)
        self.assertEqual(seen, os.path.join(expected, "data", "seen.json"))
        self.assertEqual(hub_root, expected)
        self.assertEqual(sorted(p.name for p in self.site.iterdir()), ["mixhub"])  # nothing written next to the code


class TestSuiteIsolationTests(unittest.TestCase):
    """Fails if any default state path resolves inside the checkout while the tests run (see `tests/conftest.py`)."""

    def test_default_state_paths_stay_out_of_the_checkout(self) -> None:
        import mixhub.cli.daemon as cli_daemon
        import mixhub.filters.agy_claude.prefs as agy_prefs
        import mixhub.hub.constants as constants
        import mixhub.probe.patterns as probe_patterns
        import mixhub.core.prefs as core_prefs
        import mixhub.tui.paths as tui_paths
        from mixhub.hub.hub import Hub
        from mixhub.providers.title_overrides import resolve_overrides_path
        from mixhub.tmux.outer import shell_hook

        self.assertTrue(os.environ.get("MIXHUB_HOME"), "tests/conftest.py must set MIXHUB_HOME before importing mixhub")
        defaults = {
            "paths.STATE_ROOT": paths.STATE_ROOT,
            "paths.DATA_DIR": paths.DATA_DIR,
            "paths.PREFERENCES_PATH": paths.PREFERENCES_PATH,
            "paths.ACTIVE_SEL_PATH": paths.ACTIVE_SEL_PATH,
            "paths.SEEN_PATH": paths.SEEN_PATH,
            "paths.OUTER_WINDOW_MARKER": paths.OUTER_WINDOW_MARKER,
            "constants.ROOT": constants.ROOT,
            "Hub().root": Hub().root,
            "cli.daemon.PID_FILE": cli_daemon.PID_FILE,
            "probe.DEFAULT_PROBE_RULES_PATH": probe_patterns.DEFAULT_PROBE_RULES_PATH,
            "core.prefs.preferences_path()": core_prefs.preferences_path(),
            "agy_filter.PREFERENCES_PATH": agy_prefs.PREFERENCES_PATH,
            "title overrides default": resolve_overrides_path(),
            "shell hook marker": shell_hook().split("\n")[1].split("=", 1)[1],
        }
        defaults.update({f"tui.paths.{n}": getattr(tui_paths, n) for n in (
            "ROOT", "ACTIVE_SEL_PATH", "SEEN_PATH", "TUI_DIAG_LOG_PATH", "OUTER_WINDOW_MARKER", "HOME_SHELL_HEIGHT_PATH",
            "PREFERENCES_PATH", "HIBERNATING_MARKER_DIR", "LAST_EXIT_DIR", "PROMOTED_NEW_MAP_PATH", "UNSENT_DRAFTS_PATH",
        )})
        leaked = {k: v for k, v in defaults.items() if _inside_checkout(v)}
        self.assertEqual(leaked, {})

    def test_the_guard_flags_a_path_inside_the_checkout(self) -> None:
        self.assertTrue(_inside_checkout(str(REPO_ROOT / "data" / "seen.json")))
        self.assertTrue(_inside_checkout(str(REPO_ROOT)))
        self.assertFalse(_inside_checkout(str(REPO_ROOT) + "-sibling/data"))


class LegacyLocationTests(unittest.TestCase):
    """State goes to the state root, never to the old `~/.gemini/session-hub` install location."""

    def test_title_overrides_follow_the_state_root(self) -> None:
        from mixhub.providers.title_overrides import resolve_overrides_path

        own = resolve_overrides_path(home_dir=os.path.expanduser("~"))
        self.assertEqual(own, os.path.join(paths.STATE_ROOT, "data", "title_overrides.json"))
        with tempfile.TemporaryDirectory() as fake_home:
            other = resolve_overrides_path(home_dir=fake_home)
            self.assertTrue(other.startswith(os.path.abspath(fake_home) + os.sep))
        for path in (own, other):
            self.assertNotIn(os.path.join(".gemini", "session-hub"), path)


if __name__ == "__main__":
    unittest.main()
