"""`tui.py` is the entry script plus compatibility re-exports; the TUI code lives in `mixhub.tui` and `mixhub.tmux`."""

import ast
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
import tui  # noqa: E402
import mixhub.tmux.commands  # noqa: E402
import mixhub.tui.app  # noqa: E402
import mixhub.tui.paths  # noqa: E402


def _package_modules():
    yield from sorted((REPO_ROOT / "mixhub" / "tui").rglob("*.py"))
    yield from sorted((REPO_ROOT / "mixhub" / "tmux").glob("*.py"))


def test_tui_and_tmux_packages_never_import_the_entry_script():
    # Importing `tui` from inside the package would load a second copy of tui.py when it runs as `__main__`
    for path in _package_modules():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                assert all(a.name != "tui" for a in node.names), path
            elif isinstance(node, ast.ImportFrom):
                assert node.module != "tui", path


def test_relocated_state_reads_through_tui_are_live():
    for name, owner in tui._RELOCATED_STATE.items():
        assert name not in vars(tui), f"tui.{name} must not be a static copy that a patch could go stale on"
        assert getattr(tui, name) is getattr(owner, name)


def test_hubtui_is_composed_from_the_hubtui_mixins():
    assert tui.HubTUI is mixhub.tui.app.HubTUI
    bases = tui.HubTUI.__mro__[1:-1]
    assert bases and all(cls.__module__.startswith("mixhub.tui.hubtui.") for cls in bases)


def test_respawned_panes_run_the_tui_entry_module():
    cmd = mixhub.tmux.commands._header_cmd("claude", "abc")
    assert cmd.endswith(" -m mixhub.tui --header claude abc")
    assert "tui.py" not in cmd
