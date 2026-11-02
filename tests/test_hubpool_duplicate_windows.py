"""Opening a session whose `hubpool` window exists never starts a second copy, even when the name is ambiguous.

tmux can't select a window by name once two windows share it ("can't find window"). When the TG delivery script and
a Resume click both opened the same session, every later click took that failure for "no window" and started one more
`claude --resume` of the same session.
"""
import unittest
from unittest import mock

from mixhub.tmux import pool

SID = "7501e2bb-e3ae-4747-ab6b-3b7da4fc0586"
WNAME = f"w-claude-{SID[:12]}"


class FakeTmux:
    """A `tmux -L hubpool` window table that resolves `-t` targets the way tmux does."""

    def __init__(self, names):
        self.windows = [(f"@{i}", name) for i, name in enumerate(names, 1)]
        self.calls = []

    def _resolve(self, target):
        name = target.split(":", 1)[-1]
        if name.startswith("@"):
            return [w for w in self.windows if w[0] == name]
        return [w for w in self.windows if w[1] == name]

    def run(self, argv, **kwargs):
        args = list(argv[3:]) if argv[:3] == ["tmux", "-L", "hubpool"] else list(argv)
        self.calls.append(args)
        out, rc = "", 0
        if args[:1] == ["list-windows"]:
            fmt = args[args.index("-F") + 1]
            out = "".join(fmt.replace("#{window_id}", wid).replace("#{window_name}", name) + "\n"
                          for wid, name in self.windows)
        elif args[:1] == ["select-window"]:
            rc = 0 if len(self._resolve(args[args.index("-t") + 1])) == 1 else 1
        elif args[:1] == ["new-window"]:
            wid = f"@{len(self.windows) + 1}"
            self.windows.append((wid, args[args.index("-n") + 1]))
            out = wid + "\n" if "-P" in args else ""
        return mock.Mock(returncode=rc, stdout=out, stderr="")

    def new_windows(self):
        return [c for c in self.calls if c[:1] == ["new-window"]]


class DuplicateWindowTest(unittest.TestCase):
    def activate(self, tmux):
        session = {"id": SID, "tool": "claude", "cwd": "/tmp"}
        with mock.patch.object(pool.subprocess, "run", side_effect=tmux.run), \
                mock.patch.object(pool.subprocess, "Popen"), \
                mock.patch.object(pool._headers, "ensure_hubpool_window_header"), \
                mock.patch.object(pool, "_enforce_hubpool_header_height", create=True), \
                mock.patch.object(pool, "find_hubpool_window_for_session", return_value=None):
            return pool.activate_session_in_hubpool(session, "hub", force_takeover=True, focus_outer=False)

    def test_two_windows_with_the_same_name_are_reused_not_joined_by_a_third(self):
        tmux = FakeTmux(["reader", WNAME, WNAME])
        self.assertTrue(self.activate(tmux))
        self.assertEqual(tmux.new_windows(), [])
        self.assertIn(["select-window", "-t", "@2"], [c[:3] for c in tmux.calls])

    def test_a_missing_window_is_created_once_and_selected_by_id(self):
        tmux = FakeTmux(["reader"])
        self.assertTrue(self.activate(tmux))
        self.assertEqual(len(tmux.new_windows()), 1)
        # A same-named window opened meanwhile by another process must not make this selection ambiguous
        self.assertIn(["select-window", "-t", "@2"], [c[:3] for c in tmux.calls])


if __name__ == "__main__":
    unittest.main()
