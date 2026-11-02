import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import channels
import handoff
from mixhub.ops.doctor import run_doctor
import mixhub.hub.hub
import server
import tui

# The hub layer modules that guard `import fcntl`; reloaded (with the modules composing them) to import as win32 would.
FCNTL_MODULES = tuple(sorted(
    name for name, mod in list(sys.modules.items())
    if name.startswith(("mixhub.hub.", "mixhub.web.")) and hasattr(mod, "fcntl")
))


def _reload_hub_layer():
    for name in FCNTL_MODULES + ("mixhub.hub.hub", "mixhub.web.server", "mixhub.web", "mixhub.hub"):
        importlib.reload(importlib.import_module(name))
    importlib.reload(server)


class WindowsCompatAndDynamicSavingsTest(unittest.TestCase):
    def test_win32_import_discover_handoff_doctor_and_report(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            root = home / "hub"
            root.mkdir(parents=True, exist_ok=True)

            sid = "c0ffee00-1111-4222-8333-444455556666"
            tx_path = home / f".claude/projects/winproj/{sid}.jsonl"
            tx_path.parent.mkdir(parents=True, exist_ok=True)
            rows = [
                {
                    "type": "user",
                    "cwd": str(home / "repo"),
                    "message": {"role": "user", "content": "Ensure MixHub runs cleanly on Windows win32 and WSL2."},
                },
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "text", "text": "[IRREV] Guard fcntl, termios, tty, pty, and curses imports for win32."}
                        ],
                    },
                },
            ]
            with tx_path.open("w", encoding="utf-8") as f:
                for r in rows:
                    f.write(json.dumps(r) + "\n")

            codex_lock = home / f".codex/thread-writer-locks/{sid}.lock"
            codex_lock.parent.mkdir(parents=True, exist_ok=True)
            codex_lock.write_text("lock", encoding="utf-8")

            with mock.patch.dict(
                sys.modules,
                {"fcntl": None, "termios": None, "tty": None, "pty": None, "curses": None},
            ), mock.patch.object(sys, "platform", "win32"), \
                    mock.patch.dict(os.environ, {"MIXHUB_TGNOTIFY_BIN": "tgnotify"}):  # shutil.which needs real win32
                _reload_hub_layer()
                importlib.reload(tui)

                self.assertTrue(FCNTL_MODULES)
                for name in FCNTL_MODULES:
                    self.assertIsNone(sys.modules[name].fcntl, name)
                self.assertIsNone(tui.curses)

                hub = server.Hub(str(home), str(root))
                discovered = hub.discover(force=True)
                self.assertIn(f"claude:{sid}", discovered)

                ho = hub.handoff({"id": sid, "to": "codex", "mode": "delegate"})
                self.assertTrue(ho["ok"])
                self.assertIn("before_tokens", ho)
                self.assertIn("saved_usd", ho)
                self.assertIn("✦ Handoff", ho["ephemeral_toast"])

                doc_win = run_doctor(hub, env={})
                self.assertTrue(doc_win["ok"])
                self.assertEqual(doc_win["platform"]["mode"], "windows-native")
                self.assertTrue(doc_win["platform"]["is_windows"])

            # Restore normal module state after reload
            _reload_hub_layer()
            importlib.reload(tui)

            doc_wsl = run_doctor(env={"WSL_DISTRO_NAME": "Ubuntu-24.04"})
            self.assertTrue(doc_wsl["platform"]["is_wsl"])
            self.assertEqual(doc_wsl["platform"]["mode"], "wsl2:Ubuntu-24.04")


if __name__ == "__main__":
    unittest.main()
