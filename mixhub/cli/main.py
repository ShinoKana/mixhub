#!/usr/bin/env python3
"""
`mixhub.cli.main`: the `hub` / `mixhub` command dispatcher (TUI, tmux workspace, handoff, sync-rules, notify,
and the Web daemon).

`bin/hub` (source checkout) calls `main()`; the `mixhub` / `hub` console scripts and `python -m mixhub` call `entry()`.
Both turn `OSError` / `RuntimeError` / `subprocess.SubprocessError` into a one-line message and exit status 1.
"""
try:
    import fcntl
except ImportError:  # Windows native (win32) fallback
    fcntl = None
import os
import subprocess
import sys

from mixhub.cli.daemon import URL, health, start, stop
from mixhub.cli.sessions import run_handoff
from mixhub.cli.tools import run_doctor, run_notify, run_sync_rules
from mixhub.cli.workspace import apply_window_title_and_hooks, run_on_attach, run_on_detach, run_reload, run_workspace
from mixhub.core.env import env
from mixhub.core.browser import open_url
from mixhub.core.launch import AGY_FILTER_MODULE, TUI_MODULE, child_env, module_argv
from mixhub.core.paths import ROOT, ensure_state_root  # noqa: F401  (`ROOT`: the code root, kept for importers)


USAGE = """Usage: mixhub [command]

  mixhub                 open the workspace (tmux), or reattach to it
  mixhub web             open the web workbench (starts the local daemon)
  mixhub start|stop|status
                         control the web daemon without opening a browser
  mixhub doctor [--hidden]
                         check agents, tmux, channels and terminal settings;
                         --hidden lists sessions hidden only by a text rule
  mixhub handoff SESSION_ID --to claude|codex|agy [--mode delegate|review] [--dry-run]
                         hand a session over to another agent
  mixhub notify ...      send a notification through the configured channels
  mixhub statusline claude|agy
                         statusline command that feeds the footer's quota
  mixhub shell-hook      zsh snippet that reattaches a restored Terminal window
  mixhub update [--check]
                         upgrade MixHub the way it was installed, then reload what is running
  mixhub reload          reload the sidebar and reader after an update
  mixhub --version
"""


def main():
    args = sys.argv[1:]
    if args and args[0] in ("-h", "--help", "help"):
        sys.stdout.write(USAGE)
        return 0
    if args and args[0] in ("-V", "--version", "version"):
        import mixhub
        print("mixhub " + mixhub.__version__)
        return 0
    if sys.platform == "win32":
        raise RuntimeError("MixHub doesn't run on native Windows. Install WSL (`wsl --install`) and run MixHub "
                           "inside it; the web workbench then opens in your Windows browser.")
    command = args[0] if args else ("workspace" if (sys.stdin.isatty() and sys.stdout.isatty() and env("NO_OPEN") != "1") else "open")

    if command == "agy":
        argv = module_argv(AGY_FILTER_MODULE, *args[1:])
        os.execvpe(argv[0], argv, child_env())
    if command == "handoff":
        return run_handoff(args[1:])
    if command == "sync-rules":
        return run_sync_rules(args[1:])
    if command == "notify":
        return run_notify(args[1:])
    if command == "shell-hook":
        # zsh snippet: a Terminal window restored after a crash reconnects to the MixHub it was showing
        from mixhub.tmux.outer import shell_hook
        sys.stdout.write(shell_hook())
        return 0
    if command == "update":
        from mixhub.cli.update import run_update
        return run_update(args[1:])
    if command in ("web", "open", "start", "status", "doctor") and sys.stderr.isatty():
        _print_update_notice()
    if command == "doctor":
        return run_doctor(args[1:])
    if command == "statusline":
        from mixhub.providers.statusline import main as statusline_main
        return statusline_main(args[1:])
    if command in ("title", "update-title") and len(args) <= 2:
        print(apply_window_title_and_hooks(args[1] if len(args) == 2 else "hub"))
        return 0
    if command == "on-detach" and len(args) <= 2:
        return run_on_detach(args[1] if len(args) == 2 else "hub")
    if command == "on-attach" and len(args) <= 2:
        return run_on_attach(args[1] if len(args) == 2 else "hub")
    if command == "reload" and len(args) == 1:
        return run_reload()
    if command in ("sidebar", "reader", "workspace", "tmux"):
        if command in ("workspace", "tmux") and len(args) <= 1:
            return run_workspace()
        extra = []
        if command == "sidebar":
            extra = ["--sidebar"]
        elif command == "reader":
            extra = ["--reader"]
        elif command in ("workspace", "tmux"):
            extra = ["--workspace"]
        argv = module_argv(TUI_MODULE, *extra, *args[1:])
        os.execvpe(argv[0], argv, child_env())

    if command not in ("web", "open", "start", "stop", "status") or len(args) > 1:
        print(f"mixhub: unknown command {command!r}\n", file=sys.stderr)
        sys.stderr.write(USAGE)
        return 2

    with open(os.path.join(ensure_state_root(), ".hub.lock"), "a") as lock:
        if fcntl is not None:
            fcntl.flock(lock, fcntl.LOCK_EX)
        if command == "status":
            live = health()
            print("running · pid %s · %s" % (live["pid"], URL) if live else "stopped · " + URL)
        elif command == "stop":
            stop()
        else:
            start()
            if command in ("open", "web") and env("NO_OPEN") != "1" and not open_url(URL):
                print("MixHub web is running at " + URL)  # no desktop browser to hand it to (headless, SSH)
    return 0


def _print_update_notice() -> None:
    """One line when a newer MixHub is on PyPI (checked at most daily, in the background; never installs)."""
    try:
        from mixhub.cli.update import update_notice

        notice = update_notice()
    except Exception:  # noqa: BLE001  (a hint must never stop the command)
        return
    if notice:
        print(notice, file=sys.stderr)


def entry() -> int:
    """Console-script entry (`mixhub` / `hub`, `python -m mixhub`): `main()` with `bin/hub`'s one-line error report."""
    try:
        return int(main() or 0)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


__all__ = ["entry", "main"]
