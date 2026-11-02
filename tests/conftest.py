"""Keep every test away from the live MixHub state directory.

The running MixHub (sidebar, reader, headers) reads and writes its state root (`data/`, `Preferences`,
`hub_state.json`, ...): seen/unread marks, drafts, the diagnostic log. A test writing there corrupts the user's real
state, so:
  - `$MIXHUB_HOME` points at a throwaway directory BEFORE any `mixhub` module is imported, so every default state
    path computed at import time (`mixhub.core.paths.STATE_ROOT` and everything derived from it) lands there;
  - each test additionally gets its own `STATE_ROOT` / hub root / TUI state files under `tmp_path`.
Preferences too: a test sees an empty one unless it sets its own, so results match a fresh clone instead of
depending on the maintainer's local settings. `tests/test_state_root.py` fails if a default resolves into the checkout.
"""

import atexit
import os
import shutil
import sys
import tempfile

# Most tests pin the Chinese strings they were written with; tests/test_i18n.py covers the language choice itself
os.environ.setdefault("MIXHUB_LANGUAGE", "zh")

# `server`, `tui`, ...: the old single-file module names many tests still use (see compat/README.md)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "compat"))

# Sessions whose working directory is under /tmp are hidden as temporary, and /tmp is Linux's default temp dir: move the
# fixtures (tempfile and pytest's `tmp_path`) to /var/tmp there so the sessions they create stay listed.
if os.path.realpath(tempfile.gettempdir()).startswith(("/tmp", "/private/tmp")) and os.path.isdir("/var/tmp"):
    tempfile.tempdir = tempfile.mkdtemp(prefix="mixhub-tests-", dir="/var/tmp")
    os.environ["TMPDIR"] = tempfile.tempdir
    atexit.register(shutil.rmtree, tempfile.tempdir, ignore_errors=True)

_SESSION_STATE_ROOT = tempfile.mkdtemp(prefix="mixhub-test-state-")
os.environ["MIXHUB_HOME"] = _SESSION_STATE_ROOT
atexit.register(shutil.rmtree, _SESSION_STATE_ROOT, ignore_errors=True)

import pytest  # noqa: E402

import mixhub.core.paths  # noqa: E402
import mixhub.hub.constants  # noqa: E402
import server  # noqa: E402,F401
import tui  # noqa: E402,F401  (imports the TUI modules the fixture redirects)
from mixhub.tui import paths as tui_paths  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_live_data(tmp_path, monkeypatch):
    data = tmp_path / "mixhub-data"
    data.mkdir()
    state_root = str(tmp_path / "mixhub-root")
    monkeypatch.setattr(mixhub.core.paths, "STATE_ROOT", state_root)
    monkeypatch.setattr(mixhub.hub.constants, "ROOT", state_root)
    monkeypatch.setenv("MIXHUB_PREFERENCES_PATH", str(tmp_path / "Preferences"))
    for name, leaf in (
        ("SEEN_PATH", "seen.json"),
        ("UNSENT_DRAFTS_PATH", "unsent_drafts.json"),
        ("PROMOTED_NEW_MAP_PATH", "promoted_new.json"),
        ("HIBERNATING_MARKER_DIR", "hibernating"),
        ("LAST_EXIT_DIR", "last_exit"),
        ("TUI_DIAG_LOG_PATH", "tui_diag.log"),
        ("OUTER_WINDOW_MARKER", "outer_window"),
        ("HOME_SHELL_HEIGHT_PATH", "home_shell_height"),
    ):
        monkeypatch.setattr(tui_paths, name, str(data / leaf))
    yield
