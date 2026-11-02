"""Layout of the hub / provider / web layers.

The real code lives in modules under `mixhub/hub/`, `mixhub/providers/` and `mixhub/web/`; the root
`server.py` / `providers.py` are thin compatibility shims. These tests pin that shape:
  - package modules are real modules, not lazy facades, and never import the root shims back;
  - a shim defines no functions or classes of its own and re-exports each object under the same name its
    owning module uses;
  - patching the owning module reaches callers inside the package;
  - everything imports under `python -S -E` with the standard library only.
"""
from __future__ import annotations

import ast
import importlib
import pkgutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPAT = REPO_ROOT / "tests" / "compat"  # the old root module names (see compat/README.md)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

REAL_PACKAGES: tuple[str, ...] = ("mixhub.hub", "mixhub.providers", "mixhub.web")
SHIMS: dict[str, str] = {"server": "server.py", "providers": "providers.py"}


def _package_modules(pkg_name: str) -> list[str]:
    pkg = importlib.import_module(pkg_name)
    names = [pkg_name]
    for info in pkgutil.iter_modules(pkg.__path__):
        names.append(f"{pkg_name}.{info.name}")
    return names


def _all_real_modules() -> list[str]:
    return [m for pkg in REAL_PACKAGES for m in _package_modules(pkg)]


class HubProvidersLayoutTests(unittest.TestCase):
    def test_package_modules_are_real_not_lazy_facades(self) -> None:
        for mod_name in _all_real_modules():
            mod = importlib.import_module(mod_name)
            self.assertNotIn("_SYMBOL_SOURCES", vars(mod), mod_name)
            self.assertNotIn("__getattr__", vars(mod), mod_name)

    def test_package_modules_never_import_root_shims(self) -> None:
        for mod_name in _all_real_modules():
            path = Path(importlib.import_module(mod_name).__file__)
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    targets = [a.name.split(".")[0] for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                    targets = [node.module.split(".")[0]]
                else:
                    continue
                for target in targets:
                    self.assertNotIn(target, SHIMS, f"{mod_name} imports the root shim `{target}`")

    def test_shims_define_nothing_and_reexport_owning_objects(self) -> None:
        for shim_name, filename in SHIMS.items():
            tree = ast.parse((COMPAT / filename).read_text(encoding="utf-8"))
            defined = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
            self.assertEqual(defined, [], f"{filename} must only re-export")
            shim = importlib.import_module(shim_name)
            for name in getattr(shim, "__all__", ()):
                self.assertTrue(hasattr(shim, name), f"{shim_name}.__all__ lists missing {name}")
            for node in tree.body:
                if isinstance(node, ast.ImportFrom) and node.module and node.module != "__future__":
                    src = importlib.import_module(node.module)
                    for alias in node.names:
                        if not callable(getattr(src, alias.name)):
                            continue  # plain values (e.g. `ROOT`) may be monkeypatched on the owner by the test fixture
                        self.assertIs(getattr(shim, alias.asname or alias.name), getattr(src, alias.name),
                                      f"{shim_name}.{alias.asname or alias.name} is not {node.module}.{alias.name}")
            for name, obj in vars(shim).items():
                owner = getattr(obj, "__module__", "") if callable(obj) or isinstance(obj, type) else ""
                self.assertNotEqual(owner, shim_name, f"{shim_name}.{name} is defined in the shim")

    def test_patching_the_owning_module_reaches_callers_inside_the_package(self) -> None:
        import providers

        with patch("mixhub.providers.deepseek.load_deepseek_api_key", return_value="sk-x"):
            self.assertTrue(providers.is_deepseek_configured())
        with patch("mixhub.providers.deepseek.load_deepseek_api_key", return_value=""):
            self.assertFalse(providers.is_deepseek_configured())

    def test_server_shim_keeps_the_hub_api_used_by_external_tools(self) -> None:
        import server
        from mixhub.hub.hub import Hub

        self.assertIs(server.Hub, Hub)
        for name in ("resolve_session_id", "listing", "discover", "summary", "action", "sleep_blockers"):
            self.assertTrue(callable(getattr(server.Hub, name)), name)
        self.assertIs(server.Hub.get_system_resource_metrics, server.get_system_resource_metrics)

    def test_discover_bumps_the_generation_owned_by_session_filters(self) -> None:
        import tempfile

        import server
        from mixhub.hub import session_filters

        seen = []

        def fake_locked(self):
            seen.append((session_filters._DISCOVER_GEN, session_filters._IN_DISCOVER))
            return {}

        with tempfile.TemporaryDirectory() as tmp, patch.object(server.Hub, "_discover_locked", fake_locked):
            hub = server.Hub(tmp, tmp)
            before = session_filters._DISCOVER_GEN
            hub.discover(force=True)
        self.assertEqual(seen, [(before + 1, True)])
        self.assertFalse(session_filters._IN_DISCOVER)

    def test_layers_import_under_python_minus_S_with_stdlib_only(self) -> None:
        mods = list(SHIMS) + _all_real_modules()
        script = (
            "import importlib, sys\n"
            "sys.path.insert(0, 'tests/compat')\n"
            f"for m in {mods!r}: importlib.import_module(m)\n"
            "local = {'mixhub', 'server', 'providers', 'proc_guard', 'rules_sync', 'probe',\n"
            "         'channels', 'handoff', 'tui', 'wordmark', 'agy_claude_filter'}\n"
            "std = set(sys.stdlib_module_names) | {'builtins', '__main__'}\n"
            "bad = sorted({m.split('.')[0] for m in sys.modules} - std - local)\n"
            "assert not bad, bad\n"
            "print('OK_MINUS_S')\n"
        )
        proc = subprocess.run([sys.executable, "-S", "-E", "-c", script], cwd=str(REPO_ROOT),
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("OK_MINUS_S", proc.stdout)


if __name__ == "__main__":
    unittest.main()
