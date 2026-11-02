"""Subprocess runner and Terminal helpers: `run`, AppleScript quoting, the Terminal focus script, resume commands."""
from __future__ import annotations

import os
import shlex
import subprocess

from mixhub.proc.identity import PS_ENV


def run(args, timeout=5):
    env = {**os.environ, **PS_ENV} if (args and os.path.basename(str(args[0])) == "ps") else None
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env)
    if result.returncode:
        raise RuntimeError(result.stderr.strip()[:400] or "Command failed: " + args[0])
    return result.stdout


_DEFAULT_RUN = run


def apple_string(text):
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\r", "\\r").replace("\n", "\\n") + '"'


def focus_script(tty):
    return '''tell application "Terminal"
repeat with w in windows
repeat with t in tabs of w
if tty of t is %s then
set selected tab of w to t
set index of w to 1
activate
return "focused"
end if
end repeat
end repeat
error "No Terminal tab matches this tty"
end tell''' % apple_string("/dev/" + tty.removeprefix("/dev/"))


def resume_command(session):
    tool, sid = session["tool"], session["id"]
    if session.get("synthetic"):
        suffix = tool
    else:
        suffix = {"claude": "claude --resume ", "agy": "agy --conversation ", "codex": "codex resume ",
                  "opencode": "opencode -s "}[tool] + shlex.quote(sid)
    return "cd " + shlex.quote(session.get("cwd") or os.path.expanduser("~")) + " && " + suffix
