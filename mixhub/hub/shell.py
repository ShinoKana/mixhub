"""Subprocess runner and Terminal helpers: `run`, AppleScript quoting, the Terminal focus script, resume commands."""
from __future__ import annotations

import json
import os
import re
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


def _claude_project_dir_name(cwd: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def resume_cwd(session: dict) -> str:
    """
    Folder to resume `session` from. Claude Code looks a transcript up by the folder it runs in, so a session whose
    cwd moved after it started (`cd` inside it) resumes from a folder its transcript belongs to: the session's own
    cwd when that matches, else the first cwd its transcript records that does. Other tools use the session's cwd.
    """
    home = os.path.expanduser("~")
    cwd = session.get("cwd") or home
    path = str(session.get("path") or "")
    if session.get("tool") != "claude" or not path:
        return cwd
    want = os.path.basename(os.path.dirname(path))
    if _claude_project_dir_name(cwd) == want:
        return cwd
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for _, line in zip(range(2000), f):
                if '"cwd"' not in line:
                    continue
                try:
                    cand = json.loads(line).get("cwd")
                except ValueError:
                    continue
                if isinstance(cand, str) and _claude_project_dir_name(cand) == want and os.path.isdir(cand):
                    return cand
    except OSError:
        pass
    return home if _claude_project_dir_name(home) == want else cwd


def resume_command(session):
    tool, sid = session["tool"], session["id"]
    if session.get("synthetic"):
        suffix = tool
    else:
        suffix = {"claude": "claude --resume ", "agy": "agy --conversation ", "codex": "codex resume ",
                  "opencode": "opencode -s "}[tool] + shlex.quote(sid)
    return "cd " + shlex.quote(resume_cwd(session)) + " && " + suffix
