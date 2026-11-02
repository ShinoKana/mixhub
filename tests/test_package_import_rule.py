"""The `mixhub` package never imports a repo-root module, and the root files are thin shims / entry points.

Every line of real code lives under `mixhub/`. The repo-root `*.py` files and `bin/hub` only re-export package names
(for tests and external tools) or start a package entry point. These tests pin that shape:
  - no module under `mixhub/` imports a root module (top-level, lazy, `importlib.import_module` or `__import__`),
    and none edits `sys.path`;
  - no module under `mixhub/` is a lazy facade (`__getattr__` / `_SYMBOL_SOURCES`);
  - the root shims define nothing, import only from `mixhub`, re-export the owning objects and keep their
    `__main__` entry where they are run as scripts;
  - `bin/hub` is a thin launcher over `mixhub.cli.main`.
"""
from __future__ import annotations

import ast
import importlib
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
COMPAT = REPO_ROOT / "tests" / "compat"  # the old root module names (see compat/README.md)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Out-of-tree, local-only tools that live at the repo root but are not part of the package (gitignored).
LOCAL_TOOLS: frozenset[str] = frozenset({"slim_storage", "mixhub_private"})
# (package file, root module or "sys.path") pairs that are allowed: none.
ALLOWED: frozenset[tuple[str, str]] = frozenset()

# Root shims moved into the package by this layout: shim -> owning package prefix.
SHIMS: dict[str, str] = {
    "probe": "mixhub.probe.",
    "proc_guard": "mixhub.proc.",
    "rules_sync": "mixhub.rules.",
    "agy_claude_filter": "mixhub.",
    "wordmark": "mixhub.tui.widgets.wordmark",
}
SCRIPT_SHIMS: tuple[str, ...] = ("rules_sync", "agy_claude_filter", "mixhub_cli")


def _root_module_names() -> set[str]:
    return {p.stem for p in (*REPO_ROOT.glob("*.py"), *COMPAT.glob("*.py"))} | set(LOCAL_TOOLS)


def _package_files() -> list[Path]:
    """Tracked package files only: maintainer-local, gitignored tools are not part of the package."""
    try:
        out = subprocess.run(["git", "ls-files", "mixhub"], cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout
        return sorted(REPO_ROOT / line for line in out.splitlines() if line.endswith(".py"))
    except (OSError, subprocess.CalledProcessError):
        return sorted((REPO_ROOT / "mixhub").rglob("*.py"))


def _imported_top_names(tree: ast.AST) -> list[tuple[int, str]]:
    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((node.lineno, a.name.split(".")[0]) for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.append((node.lineno, node.module.split(".")[0]))
        elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
            func = node.func
            fname = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if fname in ("import_module", "__import__") and isinstance(node.args[0].value, str):
                found.append((node.lineno, node.args[0].value.split(".")[0]))
    return found


def _mutates_sys_path(tree: ast.AST) -> list[int]:
    lines = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "path"
            and isinstance(node.value, ast.Name)
            and node.value.id == "sys"
        ):
            lines.append(node.lineno)
    return lines


class PackageImportRuleTests(unittest.TestCase):
    def test_package_never_imports_a_root_module(self) -> None:
        root_names = _root_module_names()
        self.assertIn("server", root_names)
        violations = []
        for path in _package_files():
            rel = path.relative_to(REPO_ROOT).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
            for lineno, name in _imported_top_names(tree):
                if name in root_names and (rel, name) not in ALLOWED:
                    violations.append(f"{rel}:{lineno} imports root module `{name}`")
            if (rel, "sys.path") not in ALLOWED:
                violations.extend(f"{rel}:{lineno} touches sys.path" for lineno in _mutates_sys_path(tree))
        self.assertEqual(violations, [])

    def test_the_rule_catches_lazy_and_dynamic_root_imports(self) -> None:
        src = (
            "def f():\n"
            "    import server\n"
            "    from tui import main\n"
            "    import importlib\n"
            "    importlib.import_module('providers')\n"
            "    import sys\n"
            "    sys.path.insert(0, '.')\n"
        )
        tree = ast.parse(src)
        self.assertEqual([n for _, n in _imported_top_names(tree) if n in _root_module_names()],
                         ["server", "tui", "providers"])
        self.assertEqual(_mutates_sys_path(tree), [7])

    def test_package_modules_are_not_lazy_facades(self) -> None:
        for path in _package_files():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            top_defs = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
            top_assigns = {t.id for n in tree.body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}
            self.assertNotIn("__getattr__", top_defs, path)
            self.assertNotIn("_SYMBOL_SOURCES", top_assigns, path)

    def test_root_shims_define_nothing_and_reexport_owning_objects(self) -> None:
        for shim_name, owner_prefix in SHIMS.items():
            filename = f"{shim_name}.py"
            tree = ast.parse((COMPAT / filename).read_text(encoding="utf-8"), filename=filename)
            defined = [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
            self.assertEqual(defined, [], f"{filename} must only re-export")
            shim = importlib.import_module(shim_name)
            n_reexports = 0
            for node in tree.body:
                if not isinstance(node, ast.ImportFrom) or node.module == "__future__":
                    continue
                self.assertTrue(node.module.startswith("mixhub."), f"{filename} imports from {node.module}")
                src = importlib.import_module(node.module)
                for alias in node.names:
                    n_reexports += 1
                    self.assertIs(getattr(shim, alias.asname or alias.name), getattr(src, alias.name),
                                  f"{shim_name}.{alias.name} is not {node.module}.{alias.name}")
            self.assertGreater(n_reexports, 0, filename)
            self.assertTrue(
                any(isinstance(n, ast.ImportFrom) and n.module.startswith(owner_prefix) for n in tree.body),
                f"{filename} must re-export from {owner_prefix}",
            )
            for name, obj in vars(shim).items():
                if callable(obj) or isinstance(obj, type):
                    self.assertNotEqual(getattr(obj, "__module__", ""), shim_name, f"{shim_name}.{name} defined in shim")

    def test_script_shims_keep_their_main_entry(self) -> None:
        for shim_name in SCRIPT_SHIMS:
            src = (COMPAT / f"{shim_name}.py").read_text(encoding="utf-8")
            self.assertIn('if __name__ == "__main__":', src, shim_name)
            self.assertTrue(callable(importlib.import_module(shim_name).main), shim_name)

    def test_names_used_by_external_tools_stay_on_the_shims(self) -> None:
        import probe
        import rules_sync
        from mixhub.probe.patterns import WORKING_RE
        from mixhub.rules.trust import ensure_workspace_trusted

        self.assertIs(probe.WORKING_RE, WORKING_RE)
        self.assertIs(rules_sync.ensure_workspace_trusted, ensure_workspace_trusted)

    def test_bin_hub_is_a_thin_launcher_over_mixhub_cli(self) -> None:
        tree = ast.parse((REPO_ROOT / "bin" / "hub").read_text(encoding="utf-8"))
        self.assertEqual([n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))], [])
        self.assertIn("mixhub.cli.main", [n.module for n in tree.body if isinstance(n, ast.ImportFrom)])
        import mixhub.cli.main
        import mixhub.cli.workspace
        import mixhub_cli
        from mixhub.core.paths import ROOT

        self.assertEqual(mixhub.cli.main.ROOT, ROOT)
        self.assertEqual(mixhub.cli.workspace.ROOT, ROOT)
        self.assertEqual(os.path.realpath(ROOT), os.path.realpath(str(REPO_ROOT)))
        self.assertTrue(callable(mixhub_cli.main))

    def test_every_package_module_and_shim_imports_under_python_minus_S(self) -> None:
        mods = [".".join(p.relative_to(REPO_ROOT).with_suffix("").parts).removesuffix(".__init__") for p in _package_files()]
        mods = [m for m in mods if m != "mixhub.__main__"] + list(SHIMS) + ["mixhub_cli"]
        script = (
            "import importlib, sys\n"
            "sys.path.insert(0, 'tests/compat')\n"
            f"for m in {mods!r}: importlib.import_module(m)\n"
            f"local = {sorted(_root_module_names() | {'mixhub'})!r}\n"
            "std = set(sys.stdlib_module_names) | {'builtins', '__main__'}\n"
            "bad = sorted({m.split('.')[0] for m in sys.modules} - std - set(local))\n"
            "assert not bad, bad\n"
            "print('OK_MINUS_S', len(sys.modules))\n"
        )
        proc = subprocess.run([sys.executable, "-S", "-E", "-c", script], cwd=str(REPO_ROOT),
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("OK_MINUS_S", proc.stdout)

    def test_bin_hub_unknown_command_prints_usage(self) -> None:
        env = dict(os.environ, HUB_NO_OPEN="1")
        proc = subprocess.run([sys.executable, str(REPO_ROOT / "bin" / "hub"), "no-such-command"], env=env,
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")
        self.assertTrue(proc.stderr.startswith("mixhub: unknown command 'no-such-command'"), proc.stderr)
        self.assertIn("Usage: mixhub [command]", proc.stderr)


if __name__ == "__main__":
    unittest.main()
