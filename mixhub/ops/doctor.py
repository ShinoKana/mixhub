#!/usr/bin/env python3
"""`mixhub.ops.doctor`: the `mixhub doctor` health report (agent CLIs, sessions, tmux, channels, terminal)."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

from mixhub.tmux.gateway import MIN_TMUX, tmux_version_tuple

AGENT_BINARIES = (("claude", "claude"), ("codex", "codex"), ("agy", "agy"), ("opencode", "opencode"))


def _which(cmd: str) -> str:
    try:
        return shutil.which(cmd) or ""
    except Exception:
        return ""


def _tmux_version(path: str) -> str:
    if not path:
        return ""
    try:
        res = subprocess.run([path, "-V"], capture_output=True, text=True, timeout=2.0)
    except (OSError, subprocess.SubprocessError):
        return ""
    return res.stdout.strip() if res.returncode == 0 else ""


def _platform(env: dict) -> dict:
    wsl_distro = str(env.get("WSL_DISTRO_NAME") or "")
    if sys.platform == "win32":
        mode, note = "windows-native", "Native Windows is not supported; run MixHub inside WSL2."
    elif wsl_distro:
        mode = f"wsl2:{wsl_distro}"
        note = f"Running inside WSL2 ({wsl_distro}); copies go to the Windows clipboard, the web opens in the Windows browser."
    else:
        mode, note = sys.platform, "POSIX native."
    return {"sys_platform": sys.platform, "mode": mode, "is_windows": sys.platform == "win32",
            "is_wsl": bool(wsl_distro), "wsl_distro": wsl_distro, "note": note}


def run_doctor(hub=None, env: dict | None = None) -> dict:
    eff_env = dict(os.environ if env is None else env)
    counts: dict[str, int] = {}
    if hub is not None:
        try:
            for s in hub.discover().values():
                counts[s.get("tool")] = counts.get(s.get("tool"), 0) + 1
        except Exception:
            pass
    agents = {}
    for name, binary in AGENT_BINARIES:
        path = _which(binary)
        agents[name] = {"binary": binary, "installed": bool(path), "binary_path": path, "session_count": counts.get(name, 0)}

    tmux_path = _which("tmux")
    tmux = {"installed": bool(tmux_path), "path": tmux_path, "version": _tmux_version(tmux_path)}

    channels = {}
    try:
        mgr = hub.channel_manager() if hub is not None else None
        for name, info in (mgr.status().get("channels", {}) if mgr else {}).items():
            channels[name] = {"configured": bool(info.get("configured"))}
    except Exception:
        pass

    locale = str(eff_env.get("LC_ALL") or eff_env.get("LC_CTYPE") or eff_env.get("LANG") or "")
    terminal = {key: str(eff_env.get(key) or "") for key in ("TERM", "COLORTERM", "TERM_PROGRAM", "TMUX")}
    terminal["utf8"] = locale.lower().replace("utf8", "utf-8").endswith("utf-8")
    platform = _platform(eff_env)

    lines = ["MixHub doctor", f"Platform: {platform['mode']} · {platform['note']}", "Agents:"]
    for name, info in agents.items():
        mark = "OK  " if info["installed"] else "WARN"
        lines.append(f"  [{mark}] {name:<9} sessions={info['session_count']:<5} {info['binary_path'] or 'not in PATH'}")
    if not any(info["installed"] for info in agents.values()):
        lines.append("  No agent CLI found: install Claude Code, Codex, Gemini CLI (agy) or OpenCode, then reopen MixHub.")
    version = tmux_version_tuple(tmux["version"])
    tmux["too_old"] = version is not None and version < MIN_TMUX
    mark = "OK  " if tmux["installed"] and not tmux["too_old"] else "WARN"
    tmux_text = tmux["version"] or "not in PATH (the terminal workspace needs tmux)"
    if tmux["too_old"]:
        tmux_text += f" (the workspace needs {'.'.join(map(str, MIN_TMUX))} or newer)"
    lines.append(f"tmux: [{mark}] {tmux_text}")
    if sys.platform.startswith("linux") and not platform["is_wsl"] and not any(
            _which(c) for c in ("wl-copy", "xclip", "xsel")):
        lines.append("Clipboard: [WARN] drag-to-copy needs wl-copy (Wayland) or xclip / xsel (X11)")
    configured = [n for n, c in channels.items() if c["configured"]]
    lines.append("Notifications: " + (", ".join(configured) + " configured" if configured else "no channel configured"))
    lines.append(
        f"Terminal: TERM={terminal['TERM'] or '-'} COLORTERM={terminal['COLORTERM'] or '-'} "
        f"program={terminal['TERM_PROGRAM'] or '-'} utf-8={'yes' if terminal['utf8'] else 'no'} "
        f"inside-tmux={'yes' if terminal['TMUX'] else 'no'}"
    )
    return {"ok": True, "platform": platform, "agents": agents, "tmux": tmux, "channels": channels,
            "terminal": terminal, "summary_text": "\n".join(lines)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mixhub doctor", description="Check agent CLIs, tmux, notification channels and terminal settings")
    parser.add_argument("--json", action="store_true", help="Output raw JSON report")
    opts = parser.parse_args(argv)

    from mixhub.hub.hub import Hub

    report = run_doctor(Hub())
    if opts.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(report["summary_text"])
    return 0


__all__ = ["main", "run_doctor"]
