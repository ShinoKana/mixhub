#!/usr/bin/env python3
"""`mixhub.proc.classify`: classify a command line as a coding-agent CLI family, dispatch script, headless run or same-family wrapper."""
from __future__ import annotations

import os
import re
import shlex


# Case-sensitive basename patterns so desktop `/Applications/Claude.app/Contents/MacOS/Claude`
# or directory paths containing `.claude` / `.gemini` are never misclassified as CLI agents.
_AGENT_FAMILY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("claude", re.compile(r"^(?:claude|claude-code|cc-ds)$")),
    ("codex", re.compile(r"^codex(?:-[a-z0-9_]+-[a-z0-9_-]+)?$")),
    ("agy", re.compile(r"^(?:agy|agy-claude|agy_claude_filter|antigravity)$")),
    ("gemini", re.compile(r"^gemini$")),
    ("opencode", re.compile(r"^opencode$")),
)

AGENT_CLI_NAMES = frozenset({"claude", "codex", "agy", "gemini", "opencode"})
_DISPATCH_CACHE: dict = {"key": None, "names": frozenset()}


def dispatch_script_names() -> frozenset:
    """Wrapper scripts that start agents (Preferences `dispatch_scripts`, e.g. `["my-agent-run"]`); none by default.
    Cached on the Preferences file's mtime: this runs once per process in every scan."""
    from mixhub.core.prefs import load_preferences, preferences_path

    path = preferences_path()
    try:
        key = (path, os.stat(path).st_mtime_ns)
    except OSError:
        key = (path, None)
    if _DISPATCH_CACHE["key"] != key:
        names = load_preferences(path).get("dispatch_scripts")
        _DISPATCH_CACHE["names"] = (frozenset(str(n) for n in names if isinstance(n, str) and n.strip())
                                    if isinstance(names, list) else frozenset())
        _DISPATCH_CACHE["key"] = key
    return _DISPATCH_CACHE["names"]

_INTERPRETER_RE = re.compile(r"^(?:node|nodejs|bun|deno|[Pp]ython(?:\d+(?:\.\d+)*)?|sh|bash|zsh|dash|ksh|env)$")
_SHELL_NAMES = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
_INTERP_VALUE_FLAGS = frozenset({"-r", "--require", "--import", "--loader", "-X", "-W"})
_INTERNAL_BEACON_RE = re.compile(r"(?:^|\s)MIXHUB_INTERNAL=1(?:\s|$)")


def _split_argv_safe(command: str) -> list[str]:
    cmd = str(command or "").strip()
    if not cmd:
        return []
    if cmd.startswith("(") and cmd.endswith(")"):
        cmd = cmd[1:-1].strip()
    try:
        return shlex.split(cmd)
    except ValueError:
        return cmd.split()


def classify_proc_args(command: str) -> tuple[str | None, str, list[str]]:
    """
    Classify a process command line into `(name, kind, tail_argv)`:
      - `kind == "tmux"`     : `tmux` server / client boundary
      - `kind == "dispatch"` : agent dispatch wrapper script (Preferences `dispatch_scripts`)
      - `kind == "agent"`    : coding agent CLI (`claude`, `codex`, `agy`, `gemini`, `opencode`)
      - `kind == "other"`    : shell, system daemon, editor, or unrelated process
    """
    dispatch = dispatch_script_names()
    argv = _split_argv_safe(command)
    if not argv:
        return None, "other", []

    idx = 0
    # Strip leading `env KEY=VAL ...` assignments
    if os.path.basename(argv[0]) == "env":
        idx = 1
        while idx < len(argv) and ("=" in argv[idx] or argv[idx].startswith("-")):
            idx += 1
        if idx >= len(argv):
            return None, "other", []
    else:
        while idx < len(argv) and "=" in argv[idx] and not argv[idx].startswith("-") and not argv[idx].startswith("/"):
            idx += 1
        if idx >= len(argv):
            return None, "other", []

    exe_path = argv[idx]
    exe_base = os.path.basename(exe_path).lstrip("-")
    idx += 1

    if exe_base == "tmux":
        return "tmux", "tmux", argv[idx:]

    if _INTERPRETER_RE.match(exe_base):
        if exe_base in _SHELL_NAMES:
            # Check if shell is executing a script file directly (e.g. `bash ~/bin/my-agent-run ...`)
            # vs `-c "..."` inline shell command.
            while idx < len(argv) and argv[idx].startswith("-"):
                flag = argv[idx]
                idx += 1
                if flag in ("-c", "-lc", "-ic"):
                    # Inline shell command: inspect if it directly invokes a dispatch script
                    inline_cmd = argv[idx] if idx < len(argv) else ""
                    for disp in dispatch:
                        if re.search(r"(?:^|[/\s\"'])" + re.escape(disp) + r"(?:\s|$|[\"'])", inline_cmd):
                            return disp, "dispatch", argv[idx:]
                    return None, "other", argv[idx:]
            if idx >= len(argv):
                return None, "other", []
            script_path = argv[idx]
            script_base = os.path.basename(script_path)
            if script_base in dispatch:
                return script_base, "dispatch", argv[idx + 1 :]
            for fam, rx in _AGENT_FAMILY_PATTERNS:
                if rx.match(re.sub(r"\.(?:c?m?js|py|sh)$", "", script_base)):
                    return fam, "agent", argv[idx + 1 :]
            return None, "other", argv[idx + 1 :]

        # node / bun / deno / python interpreter
        while idx < len(argv) and argv[idx].startswith("-"):
            flag = argv[idx]
            if flag in ("-c", "-e", "--eval"):
                return None, "other", argv[idx + 1 :]
            if flag == "-m":
                idx += 1
                break
            idx += 2 if flag in _INTERP_VALUE_FLAGS else 1
        if idx >= len(argv):
            return None, "other", []
        script_path = argv[idx]
        script_base = os.path.basename(script_path)
        script_stem = re.sub(r"\.(?:c?m?js|py)$", "", script_base)
        idx += 1
        if script_base in dispatch or script_stem in dispatch:
            return (script_base if script_base in dispatch else script_stem), "dispatch", argv[idx:]
        if "@openai/codex" in script_path or script_stem == "codex":
            return "codex", "agent", argv[idx:]
        if "@anthropic-ai/claude-code" in script_path or script_stem in ("claude", "cc-ds"):
            return "claude", "agent", argv[idx:]
        if script_stem in ("agy_claude_filter", "agy-claude", "agy") or script_path == "mixhub.filters.agy_claude":
            return "agy", "agent", argv[idx:]
        for fam, rx in _AGENT_FAMILY_PATTERNS:
            if rx.match(script_stem):
                return fam, "agent", argv[idx:]
        return None, "other", argv[idx:]

    if exe_base in dispatch:
        return exe_base, "dispatch", argv[idx:]

    exe_stem = re.sub(r"\.(?:c?m?js|py|sh)$", "", exe_base)
    for fam, rx in _AGENT_FAMILY_PATTERNS:
        if rx.match(exe_base) or rx.match(exe_stem):
            return fam, "agent", argv[idx:]

    return None, "other", argv[idx:]


def is_recognized_agent_command(command: str) -> bool:
    """Return True iff `command` is a recognized coding agent CLI or wrapper (`claude`, `codex`, `agy`, `gemini`, `opencode`)."""
    fam, kind, _ = classify_proc_args(command)
    return kind == "agent" and fam in AGENT_CLI_NAMES


def _is_headless_invocation(fam: str | None, tail_argv: list[str]) -> bool:
    if not fam:
        return False
    cmd_args = tail_argv[: tail_argv.index("--")] if "--" in tail_argv else tail_argv
    if any(
        a in ("-p", "--print", "--headless", "--output-format", "--no-session-persistence")
        or a.startswith(("--output-format=", "--input-format="))
        for a in cmd_args
    ):
        return True
    if fam == "codex":
        non_flag = [a for a in cmd_args if not a.startswith("-")]
        if non_flag and non_flag[0] in ("exec", "e"):
            return True
    return False


def _is_same_family_wrapper(
    child_fam: str | None,
    child_tail: list[str],
    parent_fam: str | None,
    parent_tail: list[str],
) -> bool:
    """
    Return True when `parent` is a direct launcher/PTY shim of `child` for the SAME agent session
    (e.g., `node /opt/homebrew/bin/codex resume` -> `.../vendor/.../codex resume`, or
    `python3 agy_claude_filter.py --conversation <id>` -> `agy --conversation <id>`, or
    `cc-ds --resume <id>` -> `claude --resume <id>`), NOT an interactive agent spawning a child subagent.
    """
    if not child_fam or child_fam != parent_fam:
        return False
    if _is_headless_invocation(child_fam, child_tail) and not _is_headless_invocation(parent_fam, parent_tail):
        return False
    # If parent and child share the same tail arguments (or parent has empty/subset tail), it's a launcher wrapper.
    if not parent_tail or child_tail == parent_tail or child_tail[-len(parent_tail) :] == parent_tail:
        return True
    return False


def has_internal_beacon(command_or_env: str, env_map: dict[str, str] | None = None) -> bool:
    """Return True if `MIXHUB_INTERNAL=1` is present in the command string or environment mapping."""
    if env_map and str(env_map.get("MIXHUB_INTERNAL") or "").strip() == "1":
        return True
    if command_or_env and _INTERNAL_BEACON_RE.search(str(command_or_env)):
        return True
    return False
