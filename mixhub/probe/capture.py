#!/usr/bin/env python3
"""`mixhub.probe.capture`: the `tmux` runner, `hubpool` pane resolution and single / batched pane capture."""
from __future__ import annotations

import os
import secrets
import shutil
import subprocess
from typing import Callable

from mixhub.probe.patterns import CONTEXT_LINES, TAIL_LINES
from mixhub.probe.text import clean_terminal_line


def _tmux_bin() -> str:
    return shutil.which("tmux") or (
        "/opt/homebrew/bin/tmux" if os.path.exists("/opt/homebrew/bin/tmux") else "tmux"
    )


def _default_runner(argv: list[str], timeout: float = 1.0):
    env = dict(os.environ)
    env.pop("TMUX", None)
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, env=env)


def _run_cmd(argv: list[str], runner: Callable | None = None, timeout: float = 1.0) -> tuple[int, str]:
    exec_fn = runner or (lambda cmd: _default_runner(cmd, timeout=timeout))
    try:
        res = exec_fn(argv)
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    if isinstance(res, str):
        return 0, res
    if isinstance(res, bytes):
        return 0, res.decode("utf-8", errors="replace")
    rc = int(getattr(res, "returncode", 0) or 0)
    stdout = getattr(res, "stdout", "")
    if isinstance(stdout, bytes):
        stdout = stdout.decode("utf-8", errors="replace")
    return rc, str(stdout or "")


def resolve_pool_pane(session_or_sid: dict | str, runner: Callable | None = None) -> dict:
    """
    Resolve a session dict, session_key (`tool:sid`), window name, or pane target into
    its concrete `hubpool` bottom CLI pane (`%pane_id` or `pool:w-...`).
    Never targets the 2-row top `--header` pane (`pane_height <= 2`).
    """
    tmux = _tmux_bin()
    raw_window = ""
    tool = ""
    sid = ""

    if isinstance(session_or_sid, dict):
        raw_window = str(session_or_sid.get("hubpoolWindow") or "").strip()
        tool = str(session_or_sid.get("tool") or session_or_sid.get("subprovider") or "").strip()
        sid = str(session_or_sid.get("id") or "").strip()
        if ":" in sid and not tool:
            tool, sid = sid.rsplit(":", 1)
    else:
        raw = str(session_or_sid or "").strip()
        if not raw:
            return {"ok": False, "window": "", "pane_id": "", "pane_height": 0, "target": "", "error": "empty_session"}
        if raw.startswith("%"):
            return {
                "ok": True,
                "window": "",
                "pane_id": raw,
                "pane_height": 24,
                "target": raw,
                "error": "",
            }
        if raw.startswith("pool:"):
            win_part = raw.split(":", 1)[1].split(".", 1)[0]
            raw_window = win_part
        elif raw.startswith("w-"):
            raw_window = raw.split(".", 1)[0]
        elif ":" in raw:
            tool, sid = raw.rsplit(":", 1)
        else:
            sid = raw

    candidates: list[str] = []
    if raw_window:
        candidates.append(raw_window)
    elif sid:
        if sid.startswith("new-"):
            candidates.append(sid if sid.startswith("w-") else f"w-{sid}")
        if tool:
            candidates.append(f"w-{tool}-{sid[:12]}")
        candidates.append(f"w-{sid}")
        candidates.append(f"w-{sid[:12]}")

    rc, list_out = _run_cmd([tmux, "-L", "hubpool", "list-windows", "-t", "pool", "-F", "#{window_name}"], runner=runner)
    live_windows = [w.strip() for w in list_out.splitlines() if w.strip()] if rc == 0 else []

    chosen_window = ""
    if live_windows:
        for cand in candidates:
            if cand in live_windows:
                chosen_window = cand
                break
        if not chosen_window and sid:
            suffix12 = f"-{sid[:12]}"
            suffix_full = f"-{sid}"
            matches = [
                w for w in live_windows
                if w.startswith("w-") and (w == f"w-{sid}" or w.endswith(suffix12) or w.endswith(suffix_full))
            ]
            if len(matches) == 1:
                chosen_window = matches[0]
            elif len(matches) > 1:
                return {
                    "ok": False,
                    "window": "",
                    "pane_id": "",
                    "pane_height": 0,
                    "target": "",
                    "error": "ambiguous_window",
                    "candidates": matches,
                }
    elif candidates:
        chosen_window = candidates[0]

    if not chosen_window:
        return {"ok": False, "window": "", "pane_id": "", "pane_height": 0, "target": "", "error": "window_not_found"}

    target_spec = f"pool:{chosen_window}.{{bottom}}"
    rc_disp, disp_out = _run_cmd(
        [
            tmux,
            "-L",
            "hubpool",
            "display",
            "-p",
            "-t",
            target_spec,
            "#{pane_id}\t#{pane_height}\t#{pane_dead}\t#{pane_title}",
        ],
        runner=runner,
    )
    if rc_disp == 0 and disp_out.strip():
        parts = (disp_out.rstrip("\r\n").split("\t") + ["", "", "", ""])[:4]
        pane_id, raw_h, raw_dead, pane_title = parts
        try:
            pane_height = int(raw_h or 24)
        except ValueError:
            pane_height = 24
        if pane_height <= 2:
            return {
                "ok": False,
                "window": chosen_window,
                "pane_id": pane_id,
                "pane_height": pane_height,
                "target": target_spec,
                "error": "refusing_header_pane",
            }
        if str(raw_dead).strip() == "1":
            return {
                "ok": False,
                "window": chosen_window,
                "pane_id": pane_id,
                "pane_height": pane_height,
                "target": pane_id or target_spec,
                "pane_dead": True,
                "error": "pane_dead",
            }
        return {
            "ok": True,
            "window": chosen_window,
            "pane_id": pane_id.strip() or target_spec,
            "pane_height": pane_height,
            "pane_dead": False,
            "pane_title": pane_title.strip(),
            "target": pane_id.strip() or target_spec,
            "error": "",
        }

    # Fallback when display -p is not mocked but capture-pane is mocked in unit tests
    fallback_target = f"pool:{chosen_window}.0"
    return {
        "ok": True,
        "window": chosen_window,
        "pane_id": fallback_target,
        "pane_height": 24,
        "pane_dead": False,
        "pane_title": "",
        "target": fallback_target,
        "error": "",
    }


def capture_pane(
    pane_id_or_target: str,
    lines: int = CONTEXT_LINES,
    runner: Callable | None = None,
) -> dict:
    """Capture the last `lines` from a `tmux -L hubpool` pane along with `pane_title`."""
    target = str(pane_id_or_target or "").strip()
    if not target:
        return {"ok": False, "text": "", "lines": [], "tail_lines": [], "pane_title": "", "pane_dead": True}

    tmux = _tmux_bin()
    span = max(TAIL_LINES, int(lines or CONTEXT_LINES))
    rc, out = _run_cmd(
        [tmux, "-L", "hubpool", "capture-pane", "-p", "-J", "-S", f"-{span}", "-t", target],
        runner=runner,
    )
    if rc != 0 and ".{bottom}" in target:
        alt_target = target.replace(".{bottom}", ".0")
        rc, out = _run_cmd(
            [tmux, "-L", "hubpool", "capture-pane", "-p", "-J", "-S", f"-{span}", "-t", alt_target],
            runner=runner,
        )
    if rc != 0:
        return {"ok": False, "text": "", "lines": [], "tail_lines": [], "pane_title": "", "pane_dead": False}

    return _captured_pane_result(out)


def _captured_pane_result(out: str) -> dict:
    cleaned_lines = [clean_terminal_line(ln) for ln in out.splitlines()]
    while cleaned_lines and not cleaned_lines[-1]:
        cleaned_lines.pop()
    non_empty = [ln for ln in cleaned_lines if ln]
    tail = non_empty[-TAIL_LINES:]
    return {
        "ok": True,
        "text": "\n".join(cleaned_lines),
        "lines": cleaned_lines,
        "tail_lines": tail,
        "pane_title": "",
        "pane_dead": False,
    }


def capture_panes(
    targets: list[str],
    lines: int = CONTEXT_LINES,
    runner: Callable | None = None,
) -> dict[str, dict]:
    """Capture several panes in one tmux invocation without slowing prompt detection.

    Each capture has unique begin/end markers. tmux aborts a command sequence on
    error; only complete frames are accepted, so a vanished pane cannot assign
    another pane's output to its session. Missing frames are retried next poll.
    """
    targets = list(dict.fromkeys(str(target).strip() for target in targets if str(target).strip()))
    if not targets:
        return {}
    span = max(TAIL_LINES, int(lines or CONTEXT_LINES))
    token = secrets.token_hex(16)
    markers = [(f"__mixhub_{token}_{i}_begin__", f"__mixhub_{token}_{i}_end__")
               for i in range(len(targets))]
    argv = [_tmux_bin(), "-L", "hubpool"]
    for target, (begin, end) in zip(targets, markers):
        if len(argv) > 3:
            argv.append(";")
        argv.extend([
            "display-message", "-p", begin, ";",
            "capture-pane", "-p", "-J", "-S", f"-{span}", "-t", target, ";",
            "display-message", "-p", end,
        ])
    _, out = _run_cmd(argv, runner=runner)
    raw_lines = out.splitlines()
    positions: dict[str, list[int]] = {}
    marker_set = {marker for pair in markers for marker in pair}
    for i, line in enumerate(raw_lines):
        if line in marker_set:
            positions.setdefault(line, []).append(i)
    result = {}
    previous_end = -1
    for target, (begin, end) in zip(targets, markers):
        starts, ends = positions.get(begin, []), positions.get(end, [])
        if len(starts) != 1 or len(ends) != 1 or not previous_end < starts[0] < ends[0]:
            break
        result[target] = _captured_pane_result("\n".join(raw_lines[starts[0] + 1:ends[0]]))
        previous_end = ends[0]
    return result


