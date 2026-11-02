#!/usr/bin/env python3
"""`mixhub.tmux.new_sessions`: Not-yet-identified `w-<tool>-new-N` windows: discovery, draft probing, and the
synthetic session rows shown for them.
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import time

from mixhub.core.textwidth import disp_trunc
from mixhub.tmux.onexit import handle_cli_on_exit
from mixhub.tmux.pool import _HUBPOOL_WINS_CACHE
from mixhub.tmux.session_state import _remove_from_working_set_file
from mixhub.tui import selection as _selection
from mixhub.tui.drafts import _PROMPT_PLACEHOLDERS

_NEW_HUBPOOL_SESSIONS: dict[str, dict] = {}
_NEW_WIN_RE = re.compile(r"^w-(claude|agy|codex|deepseek|opencode)-(new-\d+)$")


# Agents whose first sent message can be seen on disk; for the rest a window once typed into counts as in use.
_SENT_DETECTABLE = ("claude", "deepseek", "agy", "codex")


def _new_window_in_use(wname: str, tool: str) -> bool:
    """Whether an empty-draft new window still holds something: a conversation already started (the box empties when
    the first message is sent), or, for agents where that can't be seen, any draft ever typed. Raises when unknown."""
    if tool not in _SENT_DETECTABLE and (_NEW_HUBPOOL_SESSIONS.get(wname) or {}).get("everDraft"):
        return True
    return _new_window_conversation_started(wname, tool)


def _new_window_conversation_started(wname: str, tool: str) -> bool:
    """Whether the agent in a not-yet-matched new window already holds a conversation, so closing the window would
    cut it off. Claude Code registers each process in `~/.claude/sessions/<pid>.json` with its tmux pane and session
    id; the transcript exists once the first message is sent. agy logs each TUI prompt in its `history.jsonl` with
    the workspace, and so does Codex, both as soon as it is sent (headless runs log nothing there)."""
    if tool not in _SENT_DETECTABLE:
        return False
    env = dict(os.environ)
    env.pop("TMUX", None)
    res = subprocess.run(["tmux", "-L", "hubpool", "display-message", "-p", "-t", f"pool:{wname}.{{bottom}}",
                          "#{pane_id} #{pane_pid}\t#{window_created}\t#{pane_current_path}"],
                         capture_output=True, text=True, env=env, timeout=0.6)
    head, _, rest = (res.stdout or "").rstrip("\n").partition("\t")
    pane_id, _, pane_pid = head.strip().partition(" ")
    if res.returncode or not pane_id:
        raise RuntimeError("Unable to inspect new session pane")
    created_s, _, cwd = rest.partition("\t")
    created = float(created_s) if created_s.strip().isdigit() else 0.0
    if tool == "agy":
        return _agy_prompt_since(created, cwd)
    if tool == "codex":
        return _codex_turn_since(created, cwd)
    home = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    try:
        names = os.listdir(os.path.join(home, "sessions"))
    except OSError:
        return False
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(home, "sessions", name), encoding="utf-8") as f:
                info = json.load(f)
        except (OSError, ValueError):
            continue
        if not isinstance(info, dict) or not str(info.get("tmux") or "").endswith("." + pane_id):
            continue
        # pane ids repeat across tmux servers: the process must be the one running in this pane
        ppid = subprocess.run(["ps", "-o", "ppid=", "-p", str(info.get("pid") or "")], capture_output=True, text=True,
                              timeout=0.6).stdout.strip()
        if ppid != pane_pid:
            continue
        sid = str(info.get("sessionId") or "")
        return bool(sid) and bool(glob.glob(os.path.join(home, "projects", "*", glob.escape(sid) + ".jsonl")))
    return False


def _jsonl_tail(path: str, size: int = 262144) -> list[dict]:
    """The last `size` bytes of a JSON-lines file, parsed (a partial first line is dropped)."""
    try:
        with open(path, "rb") as f:
            f.seek(max(0, os.fstat(f.fileno()).st_size - size))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            out.append(entry)
    return out


def _agy_prompt_since(created: float, cwd: str) -> bool:
    """A prompt the user typed in an agy TUI in `cwd` since `created`. history.jsonl isn't strictly in time order, so
    every line of the tail is checked. Another agy TUI in the same folder may match: that keeps the window."""
    from mixhub.hub.session_filters import is_users_own_prompt

    path = os.path.join(os.path.expanduser("~"), ".gemini", "antigravity-cli", "history.jsonl")
    for entry in _jsonl_tail(path):
        try:
            ts = float(entry["timestamp"]) / 1000
        except (ValueError, TypeError, KeyError):
            continue
        if ts < created - 1 or entry.get("type") == "slash_command":
            continue
        if (not cwd or entry.get("workspace") in ("", None, cwd)) and is_users_own_prompt(str(entry.get("display") or "")):
            return True
    return False


def _codex_rollout_meta(root: str, sid: str = "", day_globs: tuple = ("*/*/*",)) -> list[tuple[str, dict]]:
    out = []
    for pattern in day_globs:
        for path in glob.glob(os.path.join(root, "sessions", pattern, f"rollout-*{glob.escape(sid)}.jsonl" if sid else "rollout-*.jsonl")):
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    meta = json.loads(f.readline() or "{}").get("payload") or {}
            except (OSError, ValueError, AttributeError):
                continue
            out.append((path, meta))
    return out


def _codex_turn_since(created: float, cwd: str) -> bool:
    """A prompt the user typed in a Codex TUI session in `cwd` since `created`: Codex logs each one in history.jsonl
    (`codex exec` doesn't). Without that file, a turn started (`task_started`, older versions `user_message`) in an
    interactive rollout for `cwd` written since then; exec runs and subagents don't count."""
    from mixhub.hub.session_filters import is_users_own_prompt

    root = os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")
    hist = os.path.join(root, "history.jsonl")
    if os.path.isfile(hist):
        sids = {str(e.get("session_id") or "") for e in _jsonl_tail(hist)
                if float(e.get("ts") or 0) >= created - 1 and is_users_own_prompt(str(e.get("text") or ""))}
        return any(not cwd or meta.get("cwd") in (None, cwd)
                   for sid in sids if sid for _, meta in _codex_rollout_meta(root, sid))
    days = tuple(sorted({time.strftime("%Y/%m/%d", time.localtime(t)) for t in (time.time(), created or time.time())}))
    for path, meta in _codex_rollout_meta(root, day_globs=days):
        if not isinstance(meta.get("source"), str) or meta.get("source") == "exec":
            continue
        if cwd and meta.get("cwd") not in (None, cwd):
            continue
        try:
            if os.path.getmtime(path) < created - 1:
                continue
            with open(path, encoding="utf-8", errors="replace") as f:
                if any('"task_started"' in line or '"user_message"' in line for line in f):
                    return True
        except OSError:
            continue
    return False


def _make_new_hubpool_session_dict(tool: str, sid: str, wname: str, cwd: str | None = None, created_ts: float | None = None) -> dict:
    eff_cwd = cwd or os.path.expanduser("~")
    short_cwd = eff_cwd.replace(os.path.expanduser("~"), "~")
    label = {"claude": "Claude", "agy": "Gemini", "codex": "Codex", "deepseek": "DeepSeek"}.get(tool, tool.capitalize())
    return {
        "id": sid,
        "tool": tool,
        "title": f"New {label} Session",
        "cwd": eff_cwd,
        "shortCwd": short_cwd,
        "isLive": True,
        "resident": True,
        "inWorkingSet": True,
        "synthetic": True,
        "agentState": "done_idle",
        "updatedAt": created_ts if created_ts is not None else time.time(),
        "hubpoolWindow": wname,
    }


def _inspect_new_hubpool_window_draft(wname: str) -> str:
    """Read the native input box at the bottom composer region only, excluding ANSI-styled placeholder suggestions.

    Capture failures propagate so callers never mistake an unreadable pane for an
    empty draft and close it.
    """
    env = dict(os.environ)
    env.pop("TMUX", None)
    res = subprocess.run(
        ["tmux", "-L", "hubpool", "capture-pane", "-e", "-p", "-t", f"pool:{wname}.{{bottom}}"],
        capture_output=True, text=True, env=env, timeout=0.6,
    )
    if res.returncode:
        raise RuntimeError("Unable to inspect new session pane")
    raw = res.stdout
    # Dim spans are placeholders, except a dim rule (agy's), which bounds the draft; a span ends at its reset or the
    # line end, since agy resets its rule's color only on the next line.
    raw = re.sub(r"\x1b\[(?:(?:0;)?2|90|38;5;23[89]|38;5;24[0-6])m([^\x1b\n]*)(?:\x1b\[[0-9;]*m)?",
                 lambda m: m.group(1) if "───" in m.group(1) else "", raw)
    raw = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", raw)
    lines = [ln for ln in raw.splitlines() if ln.strip()]
    chrome = re.compile(r"\? for shortcuts|esc to dismiss|press up to edit|context left|for shortcuts|auto mode on", re.I)
    tail = lines[-8:]
    for idx in range(len(tail) - 1, -1, -1):
        line = tail[idx].strip().strip("│┃").strip()
        if line in (">", "❯", "›"):
            return ""
        prompt = re.match(r"^[>❯›](?:\s+|\xa0)(.*)", line)
        if not prompt:
            continue
        draft_lines = []
        first = prompt.group(1).strip()
        if not first or chrome.search(first):
            return ""
        low = first.lower()
        if any(ph in low for ph in _PROMPT_PLACEHOLDERS):
            return ""
        draft_lines.append(first)
        for continuation in tail[idx + 1:]:
            text = continuation.strip().strip("│┃").strip()
            if not text or "───" in text or chrome.search(text):
                break
            if not continuation[:1].isspace():
                break
            draft_lines.append(text)
        return "\n".join(draft_lines).strip()
    return ""


def _collect_live_new_hubpool_sessions(allow_probe: bool = False) -> list[dict]:
    """Return active synthetic `New <Agent> Session` entries currently alive in `hubpool` or `_NEW_HUBPOOL_SESSIONS`."""
    if allow_probe and os.environ.get("TMUX"):
        run_fn = subprocess.run
        is_mock_run = type(run_fn).__name__ in ("MagicMock", "AsyncMock", "NonCallableMagicMock")
        now_mono = time.monotonic()
        if not is_mock_run and (now_mono - float(_HUBPOOL_WINS_CACHE.get("ts") or 0.0)) >= 0.8:
            env = dict(os.environ)
            env.pop("TMUX", None)
            try:
                res = subprocess.run(
                    ["tmux", "-L", "hubpool", "list-windows", "-t", "pool", "-F", "#{window_name}|#{window_panes}|#{window_active}|#{window_activity}|#{@mixhub_adopted}|#{pane_current_path}"],
                    capture_output=True,
                    text=True,
                    env=env,
                )
                if res.returncode:
                    return [dict(v) for v in _NEW_HUBPOOL_SESSIONS.values()]
                raw_out = res.stdout if isinstance(getattr(res, "stdout", None), str) else ""
                wins = set()
                active_sel_id = (_selection.read_active_selection() or {}).get("id")
                for ln in raw_out.splitlines():
                    parts = ln.strip().split("|")
                    w = parts[0].strip() if parts else ""
                    if not w.startswith("w-"):
                        continue
                    win_panes = parts[1].strip() if len(parts) > 1 else ""
                    win_is_active = len(parts) > 2 and parts[2] == "1"
                    if win_panes == "1":
                        try:
                            pres = subprocess.run(
                                ["tmux", "-L", "hubpool", "list-panes", "-t", f"pool:{w}", "-F", "#{pane_start_command}"],
                                capture_output=True,
                                text=True,
                                env=env,
                                timeout=1.0,
                            )
                            pout = (pres.stdout if isinstance(getattr(pres, "stdout", None), str) else "").strip()
                            m_hdr = re.search(r"--header\s+(\S+)\s+(\S+)", pout)
                            if m_hdr:
                                handle_cli_on_exit(
                                    m_hdr.group(1).strip("'\""),
                                    m_hdr.group(2).strip("'\""),
                                    w,
                                    0,
                                )
                                _NEW_HUBPOOL_SESSIONS.pop(w, None)
                                continue
                        except Exception:
                            pass
                    m_new = _NEW_WIN_RE.match(w)
                    if m_new:
                        new_id = m_new.group(2)
                        win_cwd = parts[5].strip() if len(parts) > 5 else ""
                        if w not in _NEW_HUBPOOL_SESSIONS:
                            created_ts = float(parts[3]) if len(parts) > 3 and parts[3].isdigit() and int(parts[3]) > 0 else None
                            _NEW_HUBPOOL_SESSIONS[w] = _make_new_hubpool_session_dict(
                                m_new.group(1), new_id, w, cwd=win_cwd or None, created_ts=created_ts)
                        existing = _NEW_HUBPOOL_SESSIONS[w]
                        if win_cwd and existing.get("cwd") != win_cwd:
                            # Registered from its window name alone (a selection handed over by another pane)
                            existing["cwd"] = win_cwd
                            existing["shortCwd"] = win_cwd.replace(os.path.expanduser("~"), "~")
                        try:
                            draft_text = _inspect_new_hubpool_window_draft(w)
                        except (OSError, RuntimeError, subprocess.TimeoutExpired):
                            wins.add(w)
                            continue
                        if draft_text:
                            existing["everDraft"] = True
                        # Moved out of the Home shell: whatever the user ran there (a resume, say) is theirs to close
                        adopted = len(parts) > 4 and bool(parts[4].strip())
                        if not draft_text and not win_is_active and active_sel_id != new_id and not adopted:
                            # A sent message empties the draft box too: only a window whose agent has no conversation
                            # yet is abandoned. Unknown counts as in use.
                            try:
                                in_use = _new_window_in_use(w, m_new.group(1))
                            except (OSError, RuntimeError, subprocess.SubprocessError):
                                in_use = True
                        else:
                            in_use = True
                        if not in_use:
                            subprocess.Popen(
                                ["tmux", "-L", "hubpool", "kill-window", "-t", f"pool:{w}"],
                                stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL,
                                env=env,
                            )
                            _NEW_HUBPOOL_SESSIONS.pop(w, None)
                            _remove_from_working_set_file(m_new.group(1), new_id)
                            continue
                        existing["hasDraft"] = bool(draft_text)
                        existing["title"] = (disp_trunc(draft_text, 48) if draft_text else
                                             _make_new_hubpool_session_dict(m_new.group(1), new_id, w)["title"])
                    wins.add(w)
                _HUBPOOL_WINS_CACHE["ts"] = now_mono
                _HUBPOOL_WINS_CACHE["wins"] = wins
                for w in list(_NEW_HUBPOOL_SESSIONS.keys()):
                    if w not in wins:
                        _NEW_HUBPOOL_SESSIONS.pop(w, None)
            except Exception:
                pass
    cached_wins = _HUBPOOL_WINS_CACHE.get("wins")
    if isinstance(cached_wins, set):
        for w in cached_wins:
            m = _NEW_WIN_RE.match(w)
            if m and w not in _NEW_HUBPOOL_SESSIONS:
                _NEW_HUBPOOL_SESSIONS[w] = _make_new_hubpool_session_dict(m.group(1), m.group(2), w)
    out = [dict(v) for v in _NEW_HUBPOOL_SESSIONS.values()]
    out.sort(key=lambda s: float(s.get("updatedAt") or 0.0), reverse=True)
    return out
