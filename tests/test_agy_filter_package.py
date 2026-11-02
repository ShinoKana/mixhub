#!/usr/bin/env python3
"""
The AGY PTY filter package `mixhub.filters.agy_claude` (`prefs`, `patterns`, `vscreen`, `debuglog`, `cursor`, `mouse`,
`stream`, `main`): module layout, behavior, and source hygiene.
"""
from __future__ import annotations

import ast
import importlib
from pathlib import Path
import re
import subprocess
import sys
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FILTER_PACKAGE = "mixhub.filters.agy_claude"
FILTER_MODULES: tuple[str, ...] = (
    "mixhub.filters",
    "mixhub.filters.agy_claude",
    "mixhub.filters.agy_claude.prefs",
    "mixhub.filters.agy_claude.patterns",
    "mixhub.filters.agy_claude.vscreen",
    "mixhub.filters.agy_claude.debuglog",
    "mixhub.filters.agy_claude.cursor",
    "mixhub.filters.agy_claude.mouse",
    "mixhub.filters.agy_claude.stream",
    "mixhub.filters.agy_claude.main",
)

# Public filter API (what the former facades exported) -> owning module.
FILTER_API: dict[str, str] = {
    "MouseDebugLog": "debuglog",
    "PromptCursorTracker": "cursor",
    "VScreen": "vscreen",
    "_SelClear": "mouse",
    "_cell_width": "vscreen",
    "_char_disp_width": "vscreen",
    "_debug_header": "main",
    "_get_subagent_badge": "stream",
    "_resolve_real_agy": "prefs",
    "char_idx_to_col": "vscreen",
    "col_to_char_idx": "vscreen",
    "exit_restore_bytes": "stream",
    "filter_stdin_mouse": "mouse",
    "is_mouse_shim_enabled": "prefs",
    "load_filter_preferences": "prefs",
    "main": "main",
    "sync_winsize": "mouse",
    "transform_chunk": "stream",
}


def _filter_sources() -> list[Path]:
    return sorted((REPO_ROOT / "mixhub" / "filters").rglob("*.py"))


class AgyFilterPackageTests(unittest.TestCase):
    """Verification of the B7-B9 filter package and its invariants."""

    def test_filter_api_lives_in_owning_modules_and_shim_preserves_identity(self) -> None:
        import agy_claude_filter

        for sym, owner in FILTER_API.items():
            mod = importlib.import_module(f"{FILTER_PACKAGE}.{owner}")
            self.assertTrue(hasattr(mod, sym), f"{FILTER_PACKAGE}.{owner} must define {sym}")
            val = getattr(mod, sym)
            self.assertEqual(getattr(val, "__module__", None), mod.__name__, f"{sym} must be defined in {mod.__name__}")
            self.assertIs(
                getattr(agy_claude_filter, sym),
                val,
                f"Identity mismatch: agy_claude_filter.{sym} is not {mod.__name__}.{sym}",
            )

    def test_filter_modules_are_real_code_and_the_shim_holds_no_logic(self) -> None:
        for fpath in _filter_sources():
            tree = ast.parse(fpath.read_text(encoding="utf-8"), filename=str(fpath))
            top_names = {
                t.id
                for node in tree.body
                if isinstance(node, ast.Assign)
                for t in node.targets
                if isinstance(t, ast.Name)
            }
            top_defs = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
            self.assertNotIn("_SYMBOL_SOURCES", top_names, f"{fpath} is a lazy facade")
            self.assertNotIn("__getattr__", top_defs, f"{fpath} is a lazy facade")
        shim = REPO_ROOT / "tests" / "compat" / "agy_claude_filter.py"
        tree = ast.parse(shim.read_text(encoding="utf-8"), filename=str(shim))
        self.assertFalse(
            [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))],
            "agy_claude_filter.py must only re-export (and run `main` as a script)",
        )
        for node in tree.body:
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertTrue(
                    node.module.startswith(FILTER_PACKAGE + ".") or node.module.startswith("mixhub.core."),
                    f"agy_claude_filter.py re-exports from unexpected module {node.module}",
                )

    def test_live_monkeypatch_on_root_modules_penetrates_facades(self) -> None:
        import channels
        import mixhub.channels.telegram

    def test_unidirectional_dependency_filter_never_imports_hub_layers(self) -> None:
        forbidden_prefixes = (
            "mixhub.hub",
            "mixhub.providers",
            "mixhub.handoff",
            "mixhub.channels",
            "mixhub.web",
            "mixhub.tui",
        )
        # The PTY filter wraps every `agy` start: it may only depend on itself and `mixhub.core`.
        for fpath in [REPO_ROOT / "tests" / "compat" / "agy_claude_filter.py", *_filter_sources()]:
            tree = ast.parse(fpath.read_text(encoding="utf-8"), filename=str(fpath))
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                    names = [node.module]
                for name in names:
                    for prefix in forbidden_prefixes:
                        self.assertFalse(
                            name == prefix or name.startswith(prefix + "."),
                            f"{fpath.relative_to(REPO_ROOT)} must not import {name}",
                        )

    def test_zero_dependency_python_minus_S_import_and_lazy_resolution(self) -> None:
        script = """
import importlib, sys
sys.path.insert(0, "tests/compat")
mods = %r
for mname in mods:
    importlib.import_module(mname)
import agy_claude_filter
for attr in %r:
    getattr(agy_claude_filter, attr)
local_top = {
    "mixhub", "server", "providers", "handoff", "channels",
    "agy_claude_filter", "proc_guard", "rules_sync",
    "wordmark", "slim_storage", "tui", "probe",
}
stdlib = set(sys.stdlib_module_names) | {"builtins", "__main__"}
non_stdlib = set()
for loaded in list(sys.modules):
    top = loaded.split(".")[0]
    if top not in stdlib and top not in local_top:
        non_stdlib.add(top)
assert not non_stdlib, f"Unexpected non-stdlib modules under -S: {sorted(non_stdlib)}"
print("OK_MINUS_S")
""" % (FILTER_MODULES, sorted(FILTER_API))
        proc = subprocess.run(
            [sys.executable, "-S", "-E", "-c", script],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(proc.returncode, 0, f"python3 -S failed:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}")
        self.assertIn("OK_MINUS_S", proc.stdout)

    def test_source_hygiene_no_emoji_and_no_personal_paths(self) -> None:
        emoji_re = re.compile(r"[\U0001F300-\U0001FAFF\u2615\u2693\u26A0\u2B06\u2B07]")
        user_prefix = "/" + "Users/"
        home_re = re.compile(re.escape(user_prefix) + r"([A-Za-z0-9._-]+)")
        allowed_placeholders = {"you", "yourname", "username", "user", "alice", "bob", "shared"}

        targets: list[Path] = [Path(__file__)]
        for subpkg in ("hub", "providers", "handoff", "channels", "web", "filters"):
            targets.extend(sorted((REPO_ROOT / "mixhub" / subpkg).rglob("*.py")))

        for fpath in targets:
            self.assertTrue(fpath.is_file(), f"Missing expected file: {fpath}")
            text = fpath.read_text(encoding="utf-8")
            self.assertIsNone(
                emoji_re.search(text),
                f"Forbidden emoji found in {fpath.relative_to(REPO_ROOT)}",
            )
            for m in home_re.finditer(text):
                uname = m.group(1).lower()
                self.assertIn(
                    uname,
                    allowed_placeholders,
                    f"Personal home path found in {fpath.relative_to(REPO_ROOT)}",
                )


if __name__ == "__main__":
    unittest.main()
