#!/usr/bin/env python3
"""
`mixhub.core.launch`: the one way MixHub starts its own Python child processes, and how it recognizes them.

Every child runs a package module: `<python> [-S] [-P] -m <module> <args>` (`mixhub.tui --sidebar|--reader|--header|
--on-exit`, `mixhub.web`, `mixhub.filters.agy_claude`, `mixhub on-attach`). That
works the same from a source checkout and from an installed wheel:
  - `-P` (Python >= 3.11) keeps the working directory off `sys.path`, so a project's own `json.py` / `mixhub/`
    can never shadow MixHub or the standard library in a pane that runs in that project;
  - when the package is not importable from the interpreter's default `sys.path` (a source checkout run through
    `bin/hub`), the child gets `PYTHONPATH=<checkout root>`: in its environment (`child_env`) or, for tmux / shell
    command lines, as an `env PYTHONPATH=...` prefix (`module_command` / `module_shell`). Each entry module calls
    `release_pythonpath()` first, so the injected entry never leaks into agent CLIs or tmux servers started below it.

Processes started before this launch form existed run the repo-root scripts (`python3 <root>/tui.py --header ...`);
`command_runs` / `runs_module` accept both forms.
"""
from __future__ import annotations

import functools
import os
import shlex
import sys
from collections.abc import Mapping, MutableMapping, Sequence

from mixhub.core.paths import CODE_ROOT

TUI_MODULE = "mixhub.tui"
WEB_MODULE = "mixhub.web"
AGY_FILTER_MODULE = "mixhub.filters.agy_claude"
CLI_MODULE = "mixhub"

# role -> (package entry module, legacy repo-root script basename)
ENTRY_POINTS: dict[str, tuple[str, str]] = {
    "tui": (TUI_MODULE, "tui.py"),
    "web": (WEB_MODULE, "server.py"),
    "agy_filter": (AGY_FILTER_MODULE, "agy_claude_filter.py"),
}

# Interpreter flags that consume the next argv token
_PY_VALUE_FLAGS = frozenset({"-X", "-W", "-Q", "--check-hash-based-pycs"})


def _default_import_dirs() -> set[str]:
    import site
    import sysconfig

    dirs: list[str] = []
    for key in ("purelib", "platlib"):
        try:
            dirs.append(sysconfig.get_paths()[key])
        except (KeyError, ValueError):
            pass
    try:
        dirs.extend(site.getsitepackages())
    except AttributeError:
        pass
    try:
        dirs.append(site.getusersitepackages())
    except AttributeError:
        pass
    return {os.path.realpath(d) for d in dirs if d}


@functools.lru_cache(maxsize=8)
def needs_pythonpath(python: str | None = None) -> bool:
    """True when `python` (default: this interpreter) can't import `mixhub` from its default `sys.path`."""
    if python and os.path.realpath(python) != os.path.realpath(sys.executable):
        return True
    return os.path.realpath(CODE_ROOT) not in _default_import_dirs()


def python_flags(*, no_site: bool = False, python: str | None = None) -> list[str]:
    """`-S` only where `PYTHONPATH` supplies the package (site-packages would be skipped otherwise); `-P` on >= 3.11."""
    flags = []
    if no_site and needs_pythonpath(python):
        flags.append("-S")
    if (python is None or os.path.realpath(python) == os.path.realpath(sys.executable)) and sys.version_info >= (3, 11):
        flags.append("-P")
    return flags


def module_argv(module: str, *args: object, no_site: bool = False, python: str | None = None) -> list[str]:
    """`[python, flags..., "-m", module, *args]` (pair it with `child_env()` for `subprocess` / `exec`)."""
    return [python or sys.executable, *python_flags(no_site=no_site, python=python), "-m", module, *map(str, args)]


def code_argv(code: str, *args: object, python: str | None = None) -> list[str]:
    """`[python, flags..., "-c", code, *args]` for a one-line package call (pair it with `child_env()`)."""
    return [python or sys.executable, *python_flags(python=python), "-c", code, *map(str, args)]


def child_env(env: Mapping[str, str] | None = None, *, python: str | None = None) -> dict[str, str]:
    """Copy of `env` (default `os.environ`) with the checkout root prepended to `PYTHONPATH` when needed."""
    out = dict(os.environ if env is None else env)
    if needs_pythonpath(python):
        existing = out.get("PYTHONPATH", "")
        out["PYTHONPATH"] = CODE_ROOT + (os.pathsep + existing if existing else "")
    return out


def _env_prefix(extra_env: Mapping[str, str] | None, python: str | None) -> list[str]:
    assigns = [f"PYTHONPATH={CODE_ROOT}"] if needs_pythonpath(python) else []
    assigns += [f"{k}={v}" for k, v in (extra_env or {}).items()]
    return ["env", *assigns] if assigns else []


def module_command(
    module: str,
    *args: object,
    extra_env: Mapping[str, str] | None = None,
    no_site: bool = False,
    python: str | None = None,
) -> list[str]:
    """argv for a shell / tmux command line: `[env PYTHONPATH=<checkout> K=V ...] python [flags] -m module args`."""
    return [*_env_prefix(extra_env, python), *module_argv(module, *args, no_site=no_site, python=python)]


def code_command(code: str, *args: object, python: str | None = None) -> list[str]:
    """Like `module_command` for a `-c` one-liner."""
    return [*_env_prefix(None, python), *code_argv(code, *args, python=python)]


def module_shell(module: str, *args: object, extra_env: Mapping[str, str] | None = None, exec_: bool = True) -> str:
    """Shell command string for `module_command` (prefixed with `exec ` by default), e.g. a tmux pane command."""
    cmd = shlex.join(module_command(module, *args, extra_env=extra_env))
    return f"exec {cmd}" if exec_ else cmd


def release_pythonpath(environ: MutableMapping[str, str] | None = None) -> None:
    """Drop the checkout root this launcher put first on `PYTHONPATH` (call it first thing in an entry module)."""
    env = os.environ if environ is None else environ
    raw = env.get("PYTHONPATH")
    if not raw:
        return
    parts = raw.split(os.pathsep)
    if not parts[0] or os.path.realpath(parts[0]) != os.path.realpath(CODE_ROOT):
        return
    rest = [p for p in parts[1:] if p]
    if rest:
        env["PYTHONPATH"] = os.pathsep.join(rest)
    else:
        env.pop("PYTHONPATH", None)


def mixhub_executable(hub_root: str | None = None) -> str:
    """Path of the MixHub command for configs written outside MixHub (agent hooks): `<hub_root or checkout>/bin/hub`
    when it exists, else the `mixhub` console script next to this interpreter, else `mixhub` on `PATH`. Never a bare
    `hub`: on many machines that is GitHub's CLI."""
    for root in (hub_root, CODE_ROOT):
        if root:
            cand = os.path.join(os.path.expanduser(str(root)), "bin", "hub")
            if os.path.isfile(cand):
                return cand
    beside = os.path.join(os.path.dirname(sys.executable), "mixhub")
    if os.path.isfile(beside):
        return beside
    import shutil

    return shutil.which("mixhub") or "mixhub"


def _argv(command: str | Sequence[str]) -> list[str]:
    if not isinstance(command, str):
        return [str(a) for a in command]
    try:
        return shlex.split(command)
    except ValueError:
        return command.split()


def python_module_invocation(command: str | Sequence[str]) -> tuple[str, list[str]] | None:
    """For `[env K=V ...] python [flags] -m <module> args...` return `(module, args)`, else None."""
    argv = _argv(command)
    i = 0
    if argv and os.path.basename(argv[0]) == "env":
        i = 1
        while i < len(argv) and (argv[i].startswith("-") or "=" in argv[i]):
            i += 2 if argv[i] in ("-u", "--unset", "-C", "--chdir", "-S", "--split-string") else 1
    if i >= len(argv) or not os.path.basename(argv[i]).lower().startswith("python"):
        return None
    i += 1
    while i < len(argv):
        tok = argv[i]
        if tok == "-m":
            return (argv[i + 1], argv[i + 2:]) if i + 1 < len(argv) else None
        if tok.startswith("-m") and not tok.startswith("--"):
            return tok[2:], argv[i + 1:]
        if tok in _PY_VALUE_FLAGS:
            i += 2
            continue
        if tok.startswith("-") and tok not in ("-", "-c") and not tok.startswith("-c"):
            i += 1
            continue
        return None
    return None


def runs_module(command: str | Sequence[str], module: str) -> bool:
    """True when `command` is `python [flags] -m <module> ...` (optionally behind `env`)."""
    inv = python_module_invocation(command)
    return bool(inv and inv[0] == module)


def command_runs(command: str | Sequence[str], role: str) -> bool:
    """True when `command` starts the `role` entry point (`tui`, `web`, `agy_filter`) in either
    the package form (`-m mixhub.tui`) or the legacy repo-root script form (`.../tui.py`)."""
    module, script = ENTRY_POINTS[role]
    argv = _argv(command)
    if runs_module(argv, module):
        return True
    return any(os.path.basename(tok) == script for tok in argv)


__all__ = [
    "AGY_FILTER_MODULE",
    "CLI_MODULE",
    "ENTRY_POINTS",
    "TUI_MODULE",
    "WEB_MODULE",
    "child_env",
    "code_argv",
    "code_command",
    "command_runs",
    "mixhub_executable",
    "module_argv",
    "module_command",
    "module_shell",
    "needs_pythonpath",
    "python_flags",
    "python_module_invocation",
    "release_pythonpath",
    "runs_module",
]
