"""Release packaging: the wheel ships only the `mixhub` package; console scripts and the npm file list stay valid."""
from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    import tomllib
except ImportError:  # Python 3.10
    tomllib = None


@unittest.skipIf(tomllib is None, "needs tomllib (Python 3.11+)")
class PyprojectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    def test_wheel_contains_only_the_mixhub_package(self) -> None:
        wheel = self.cfg["tool"]["hatch"]["build"]["targets"]["wheel"]
        self.assertEqual(wheel["packages"], ["mixhub"])
        self.assertNotIn("include", wheel)
        self.assertNotIn("force-include", wheel)

    def test_console_scripts_point_into_the_package(self) -> None:
        scripts = self.cfg["project"]["scripts"]
        self.assertEqual(set(scripts), {"mixhub"})  # no `hub`: that name belongs to GitHub's CLI
        for target in scripts.values():
            module, _, func = target.partition(":")
            self.assertTrue(module.startswith("mixhub."), target)
            self.assertTrue(callable(getattr(importlib.import_module(module), func)), target)


class PackageDataTests(unittest.TestCase):
    def test_web_assets_live_inside_the_package(self) -> None:
        from mixhub.core.paths import WEB_STATIC_DIR

        self.assertEqual(Path(WEB_STATIC_DIR), REPO_ROOT / "mixhub" / "web" / "static")
        for name in ("index.html", "app.js", "style.css"):
            self.assertTrue((Path(WEB_STATIC_DIR) / name).is_file(), name)
        self.assertFalse((REPO_ROOT / "static").exists())


if __name__ == "__main__":
    unittest.main()
