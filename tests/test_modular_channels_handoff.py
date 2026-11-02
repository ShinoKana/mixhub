#!/usr/bin/env python3
"""
Invariants for the real `mixhub.handoff` / `mixhub.channels` packages and their root shims.

The implementation lives in the packages; the test-suite modules `handoff` / `channels` (tests/compat)
are thin re-export shims. These tests pin:
  - every name a shim re-exports is the very object defined in its owning package module;
  - shims carry no logic of their own;
  - package modules never import the root shims (no back-edges);
  - the packages import under `python -S -E` (stdlib only).
"""
from __future__ import annotations

import ast
import importlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPAT = REPO_ROOT / "tests" / "compat"  # the old root module names (see compat/README.md)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

HANDOFF_MODULES: tuple[str, ...] = (
    "mixhub.handoff.text",
    "mixhub.handoff.resume",
    "mixhub.handoff.contract",
    "mixhub.handoff.extract",
    "mixhub.handoff.bundle",
    "mixhub.handoff.relay",
    "mixhub.handoff.disposition",
    "mixhub.handoff",
)

CHANNELS_MODULES: tuple[str, ...] = (
    "mixhub.channels.config",
    "mixhub.channels.text",
    "mixhub.channels.base",
    "mixhub.channels.telegram",
    "mixhub.channels.slack",
    "mixhub.channels.hooks",
    "mixhub.channels.manager",
    "mixhub.channels.cli",
    "mixhub.channels",
)

REAL_MODULES: tuple[str, ...] = HANDOFF_MODULES + CHANNELS_MODULES

SHIMS: dict[str, str] = {
    "handoff": "mixhub.handoff",
    "channels": "mixhub.channels",
}

# Shims whose whole implementation has moved into the package.
COMPLETE_SHIMS: tuple[str, ...] = ("handoff", "channels")

PACKAGES: tuple[str, ...] = ("mixhub.handoff", "mixhub.channels")

ROOT_SHIM_NAMES = frozenset({"handoff", "channels", "slack_bridge", "mixhub_private"})


def _shim_imports(shim: str) -> list[tuple[str, str]]:
    """(owning module, name) for every `from mixhub.... import name` in a root shim."""
    tree = ast.parse((COMPAT / f"{shim}.py").read_text(encoding="utf-8"))
    out = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("mixhub."):
            out.extend((node.module, a.asname or a.name) for a in node.names)
    return out


class RealPackageShimTests(unittest.TestCase):
    def test_shim_reexports_are_the_owning_objects(self) -> None:
        for shim, package in SHIMS.items():
            shim_mod = importlib.import_module(shim)
            pairs = _shim_imports(shim)
            self.assertTrue(pairs, f"{shim}.py re-exports nothing")
            for owner, name in pairs:
                self.assertTrue(owner.startswith(package + "."), f"{shim}.{name} comes from {owner}")
                owner_mod = importlib.import_module(owner)
                obj = getattr(owner_mod, name)
                self.assertIs(getattr(shim_mod, name), obj, f"{shim}.{name} is not {owner}.{name}")
                if isinstance(obj, type) or callable(obj) and hasattr(obj, "__module__"):
                    if getattr(obj, "__module__", "").startswith("mixhub."):
                        self.assertEqual(obj.__module__, owner, f"{shim}.{name} is defined in {obj.__module__}, not {owner}")

    def test_shims_contain_no_logic(self) -> None:
        for shim in COMPLETE_SHIMS:
            tree = ast.parse((COMPAT / f"{shim}.py").read_text(encoding="utf-8"))
            for node in tree.body:
                self.assertNotIsInstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
                    f"{shim}.py defines {getattr(node, 'name', '?')}; move it into the package",
                )

    def test_real_modules_never_import_root_shims(self) -> None:
        for mod_name in REAL_MODULES:
            path = REPO_ROOT / (mod_name.replace(".", "/") + ".py")
            if not path.exists():
                path = REPO_ROOT / mod_name.replace(".", "/") / "__init__.py"
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn(alias.name.split(".")[0], ROOT_SHIM_NAMES, f"{mod_name} imports {alias.name}")
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    self.assertNotIn(node.module.split(".")[0], ROOT_SHIM_NAMES, f"{mod_name} imports from {node.module}")

    def test_package_all_resolves_to_shim_objects(self) -> None:
        for package in PACKAGES:
            pkg = importlib.import_module(package)
            shim = next((s for s, p in SHIMS.items() if p == package), None)
            shim_mod = importlib.import_module(shim) if shim else None
            self.assertGreater(len(pkg.__all__), 0)
            for name in pkg.__all__:
                obj = getattr(pkg, name)
                if shim_mod is not None and hasattr(shim_mod, name):
                    self.assertIs(obj, getattr(shim_mod, name), f"{package}.{name} differs from {shim}.{name}")

    def test_real_modules_import_with_stdlib_only(self) -> None:
        script = (
            "import importlib, sys\n"
            f"for m in {list(REAL_MODULES)!r}:\n"
            "    importlib.import_module(m)\n"
            "local = {'mixhub', 'probe', 'handoff', 'channels'}\n"
            "std = set(sys.stdlib_module_names) | {'builtins', '__main__'}\n"
            "bad = sorted({n.split('.')[0] for n in sys.modules} - std - local)\n"
            "assert not bad, bad\n"
            "print('OK_MINUS_S')\n"
        )
        proc = subprocess.run(
            [sys.executable, "-S", "-E", "-c", script],
            cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("OK_MINUS_S", proc.stdout)


if __name__ == "__main__":
    unittest.main()
