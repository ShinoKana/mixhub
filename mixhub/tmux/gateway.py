#!/usr/bin/env python3
"""
`mixhub.tmux.gateway`: Unified `TmuxGateway` (`outer` and `pool` sockets) and `FakeTmux` for deterministic tests.

Centralizes:
  - `clean_tmux_env()` (`os.environ.copy()` + `env.pop("TMUX", None)`)
  - `-L hubpool` socket targeting (`server="outer"` vs `server="pool"`)
  - Safe IO gating (`is_io_allowed`) so mocked tests never spawn real outer tmux commands, while tests
    injecting `FakeTmux` can deterministically exercise every tmux code path.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable, Sequence

# Oldest tmux the workspace is built for (pane options, `#{P:}` formats, hook arrays).
MIN_TMUX = (3, 0)


def tmux_version_tuple(text: str) -> tuple[int, int] | None:
    """`(major, minor)` from `tmux -V` output (`tmux 3.6a`, `tmux next-3.5`), or None when unreadable."""
    m = re.search(r"(\d+)\.(\d+)", text or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


# MixHub's own tmux servers: the outer workspace (sidebar + a view of the pool) and the pool of agent windows. Neither
# is the user's default server, so MixHub's sessions, options, key bindings and hooks never reach the user's tmux.
OUTER_SOCKET = "mixhub"
POOL_SOCKET = "hubpool"

_TMUX_BIN = shutil.which("tmux") or "/opt/homebrew/bin/tmux"
if not os.path.exists(_TMUX_BIN) and os.path.exists("/usr/local/bin/tmux"):
    _TMUX_BIN = "/usr/local/bin/tmux"
if not os.path.exists(_TMUX_BIN):
    _TMUX_BIN = "tmux"

_MOCK_TYPE_NAMES = ("Mock", "MagicMock", "AsyncMock", "NonCallableMagicMock")


def clean_tmux_env(base_env: dict[str, str] | None = None) -> dict[str, str]:
    """Return a copy of `os.environ` (or `base_env`) with `TMUX` removed."""
    env = dict(os.environ if base_env is None else base_env)
    env.pop("TMUX", None)
    return env


def is_mock_object(obj: object) -> bool:
    """Return True if `obj` is a `unittest.mock` object."""
    return type(obj).__name__ in _MOCK_TYPE_NAMES


@dataclass
class FakePopen:
    """Deterministic `subprocess.Popen` stand-in returned by `FakeTmux`."""

    args: list[str]
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""

    def poll(self) -> int:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode


class TmuxGateway:
    """
    Single gateway for all `tmux` invocations across MixHub (`outer` workspace and `hubpool` daemon pool).
    """

    is_fake: bool = False

    def __init__(
        self,
        tmux_bin: str | None = None,
        pool_socket: str = "hubpool",
        run_fn: Callable[..., subprocess.CompletedProcess] | None = None,
        popen_fn: Callable[..., Any] | None = None,
    ):
        self.tmux_bin = tmux_bin or _TMUX_BIN
        self.pool_socket = pool_socket
        self._run_fn = run_fn
        self._popen_fn = popen_fn

    def is_io_allowed(self, fn: Any = None, stdscr: Any = None) -> bool:
        """
        Return True if outer tmux IO is allowed.
        - `FakeTmux` always allows IO (in-memory).
        - Real `TmuxGateway` requires `TMUX` in environment and non-mocked `fn` / `stdscr`.
        """
        if self.is_fake:
            return True
        target_fn = fn if fn is not None else (self._run_fn or subprocess.run)
        return (
            bool(os.environ.get("TMUX"))
            and not is_mock_object(target_fn)
            and (stdscr is None or not is_mock_object(stdscr))
        )

    def build_argv(self, args: Sequence[str], *, server: str = "outer", use_tmux_bin: bool = False) -> list[str]:
        binary = self.tmux_bin if use_tmux_bin else "tmux"
        if server == "pool":
            return [binary, "-L", self.pool_socket, *args]
        if server == "outer":
            return [binary, "-L", OUTER_SOCKET, *args]
        return [binary, "-L", server, *args]

    def run(
        self,
        args: Sequence[str],
        *,
        server: str = "outer",
        strip_tmux_env: bool = True,
        env: dict[str, str] | None = None,
        capture_output: bool = False,
        text: bool = True,
        timeout: float | None = None,
        check: bool = False,
        use_tmux_bin: bool = False,
        stdin: Any = subprocess.DEVNULL,
        stdout: Any = None,
        stderr: Any = None,
        close_fds: bool = True,
    ) -> subprocess.CompletedProcess:
        argv = self.build_argv(args, server=server, use_tmux_bin=use_tmux_bin)
        eff_env = env if env is not None else (clean_tmux_env() if strip_tmux_env else None)
        runner = self._run_fn or subprocess.run
        kwargs: dict[str, Any] = {
            "env": eff_env,
            "check": check,
            "close_fds": close_fds,
        }
        if stdin is not None:
            kwargs["stdin"] = stdin
        if capture_output:
            kwargs["capture_output"] = True
            kwargs["text"] = text
        else:
            if stdout is not None:
                kwargs["stdout"] = stdout
            if stderr is not None:
                kwargs["stderr"] = stderr
            if text:
                kwargs["text"] = text
        if timeout is not None:
            kwargs["timeout"] = timeout
        return runner(argv, **kwargs)

    def popen(
        self,
        args: Sequence[str],
        *,
        server: str = "outer",
        strip_tmux_env: bool = True,
        env: dict[str, str] | None = None,
        use_tmux_bin: bool = True,
        stdin: Any = subprocess.DEVNULL,
        stdout: Any = subprocess.DEVNULL,
        stderr: Any = subprocess.DEVNULL,
    ) -> Any:
        argv = self.build_argv(args, server=server, use_tmux_bin=use_tmux_bin)
        eff_env = env if env is not None else (clean_tmux_env() if strip_tmux_env else None)
        spawner = self._popen_fn or subprocess.Popen
        return spawner(argv, stdin=stdin, stdout=stdout, stderr=stderr, env=eff_env)

    def has_session(self, target: str, *, server: str) -> bool:
        try:
            res = self.run(
                ["has-session", "-t", target],
                server=server,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=False,
            )
            return res.returncode == 0
        except OSError:
            return False

    def list_panes(self, target: str, fmt: str = "#{pane_id}|#{pane_left}", *, server: str,
                   all_windows: bool = False) -> list[str]:
        try:
            res = self.run(
                ["list-panes", *(["-s"] if all_windows else []), "-t", target, "-F", fmt],
                server=server,
                capture_output=True,
                text=True,
            )
            raw = res.stdout if isinstance(getattr(res, "stdout", None), str) else ""
            return [ln.strip() for ln in raw.splitlines() if ln.strip()]
        except OSError:
            return []

    def list_windows(
        self,
        target: str = "pool",
        fmt: str = "#{window_name}",
        *,
        server: str = "pool",
    ) -> list[str]:
        try:
            res = self.run(
                ["list-windows", "-t", target, "-F", fmt],
                server=server,
                capture_output=True,
                text=True,
            )
            if res.returncode != 0:
                return []
            raw = res.stdout if isinstance(getattr(res, "stdout", None), str) else ""
            return [ln.strip() for ln in raw.splitlines() if ln.strip()]
        except OSError:
            return []

    def select_pane(
        self,
        target: str,
        *,
        server: str,
        strip_tmux_env: bool = True,
        async_run: bool = False,
    ) -> None:
        try:
            if async_run:
                self.popen(
                    ["select-pane", "-t", target],
                    server=server,
                    strip_tmux_env=strip_tmux_env,
                    use_tmux_bin=True,
                )
            else:
                self.run(
                    ["select-pane", "-t", target],
                    server=server,
                    strip_tmux_env=strip_tmux_env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    text=False,
                    use_tmux_bin=True,
                )
        except OSError:
            pass

    def send_keys(
        self,
        target: str,
        text_or_key: str,
        *,
        server: str = "pool",
        literal: bool = True,
    ) -> None:
        cmd = ["send-keys", "-t", target]
        if literal:
            cmd.extend(["-l", "--", text_or_key])
        else:
            cmd.append(text_or_key)
        try:
            self.run(
                cmd,
                server=server,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=False,
            )
        except OSError:
            pass


@dataclass
class FakeTmux(TmuxGateway):
    """
    Deterministic in-memory `TmuxGateway` double for unit and golden tests.
    Never spawns a real `tmux` subprocess.
    """

    is_fake: bool = True
    calls: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    popen_calls: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    scripted: list[tuple[str, tuple[str, ...], int, str, str]] = field(default_factory=list)
    sessions: dict[str, set[str]] = field(
        default_factory=lambda: {"outer": {"hub"}, "pool": {"pool"}}
    )
    windows: dict[str, list[str]] = field(
        default_factory=lambda: {"pool": ["reader"]}
    )
    pane_active: dict[str, bool] = field(default_factory=lambda: {"%0": True})
    selected_panes: list[str] = field(default_factory=list)
    sent_keys: list[tuple[str, str, bool]] = field(default_factory=list)

    def __post_init__(self) -> None:
        super().__init__(tmux_bin="tmux", pool_socket="hubpool")

    def _match_scripted(self, server: str, args_t: tuple[str, ...]) -> tuple[int, str, str] | None:
        for srv, prefix, rc, out, err in reversed(self.scripted):
            if srv == server and args_t[: len(prefix)] == prefix:
                return rc, out, err
        return None

    def run(
        self,
        args: Sequence[str],
        *,
        server: str = "outer",
        strip_tmux_env: bool = True,
        env: dict[str, str] | None = None,
        capture_output: bool = False,
        text: bool = True,
        timeout: float | None = None,
        check: bool = False,
        use_tmux_bin: bool = False,
        stdin: Any = subprocess.DEVNULL,
        stdout: Any = None,
        stderr: Any = None,
        close_fds: bool = True,
    ) -> subprocess.CompletedProcess:
        args_t = tuple(str(a) for a in args)
        self.calls.append((server, args_t))
        matched = self._match_scripted(server, args_t)
        if matched is not None:
            rc, out, err = matched
            return subprocess.CompletedProcess(self.build_argv(args_t, server=server), rc, out, err)

        if args_t and args_t[0] == "has-session" and "-t" in args_t:
            t_idx = args_t.index("-t") + 1
            sess = args_t[t_idx] if t_idx < len(args_t) else ""
            ok = sess in self.sessions.get(server, set())
            return subprocess.CompletedProcess(self.build_argv(args_t, server=server), 0 if ok else 1, "", "")

        if args_t and args_t[0] == "list-windows":
            wins = self.windows.get(server, [])
            return subprocess.CompletedProcess(
                self.build_argv(args_t, server=server), 0, "\n".join(wins) + ("\n" if wins else ""), ""
            )

        if args_t and args_t[0] == "display-message" and "#{pane_active}" in args_t:
            target = "%0"
            if "-t" in args_t:
                t_idx = args_t.index("-t") + 1
                if t_idx < len(args_t):
                    target = args_t[t_idx]
            active = self.pane_active.get(target, True)
            return subprocess.CompletedProcess(
                self.build_argv(args_t, server=server), 0, "1\n" if active else "0\n", ""
            )

        if args_t and args_t[0] == "select-pane" and "-t" in args_t:
            t_idx = args_t.index("-t") + 1
            if t_idx < len(args_t):
                self.selected_panes.append(args_t[t_idx])
            return subprocess.CompletedProcess(self.build_argv(args_t, server=server), 0, "", "")

        if args_t and args_t[0] == "send-keys" and "-t" in args_t:
            t_idx = args_t.index("-t") + 1
            target = args_t[t_idx] if t_idx < len(args_t) else ""
            literal = "-l" in args_t
            key_val = args_t[-1] if args_t else ""
            self.sent_keys.append((target, key_val, literal))
            return subprocess.CompletedProcess(self.build_argv(args_t, server=server), 0, "", "")

        return subprocess.CompletedProcess(self.build_argv(args_t, server=server), 0, "", "")

    def popen(
        self,
        args: Sequence[str],
        *,
        server: str = "outer",
        strip_tmux_env: bool = True,
        env: dict[str, str] | None = None,
        use_tmux_bin: bool = True,
        stdin: Any = subprocess.DEVNULL,
        stdout: Any = subprocess.DEVNULL,
        stderr: Any = subprocess.DEVNULL,
    ) -> FakePopen:
        args_t = tuple(str(a) for a in args)
        self.popen_calls.append((server, args_t))
        if args_t and args_t[0] == "select-pane" and "-t" in args_t:
            t_idx = args_t.index("-t") + 1
            if t_idx < len(args_t):
                self.selected_panes.append(args_t[t_idx])
        return FakePopen(args=self.build_argv(args_t, server=server, use_tmux_bin=use_tmux_bin))


DEFAULT_TMUX_GATEWAY = TmuxGateway()

__all__ = [
    "DEFAULT_TMUX_GATEWAY",
    "FakePopen",
    "FakeTmux",
    "TmuxGateway",
    "_TMUX_BIN",
    "clean_tmux_env",
    "is_mock_object",
]
