#!/usr/bin/env python3
"""`mixhub.tui.drafts`: Unsent prompt drafts: extraction from a captured CLI pane and `unsent_drafts.json` storage."""
from __future__ import annotations

import json
import os
import re
import subprocess

from mixhub.tmux.commands import hubpool_win_name
from mixhub.tui import diag as _diag
from mixhub.tui import paths as _paths

_PROMPT_PLACEHOLDERS = (
    "type your message",
    "press enter",
    "for shortcuts",
    "? for help",
    "send a message",
    "ask anything",
    "enter a prompt",
)


_ESC_RE = re.compile(r"\x1b(?:\[([0-9;:?]*)([A-Za-z])|\][^\x07\x1b]*(?:\x07|\x1b\\)|.)")


def strip_dim_text(pane_text: str) -> str:
    """Plain text of a `capture-pane -e` capture without the cells drawn dim (SGR 2): the CLIs draw their prompt
    suggestion and placeholder dim in the input box, where it reads like typed text once the colors are gone."""
    out = []
    dim = False
    pos = 0
    for m in _ESC_RE.finditer(pane_text):
        if not dim:
            out.append(pane_text[pos:m.start()])
        pos = m.end()
        if m.group(2) != "m":
            continue
        params = (m.group(1) or "").split(";")
        i = 0
        while i < len(params):
            code = params[i].split(":")[0]
            if code in ("", "0", "22"):
                dim = False
            elif code == "2":
                dim = True
            elif code in ("38", "48", "58") and ":" not in params[i] and i + 1 < len(params):
                # `38;5;n` / `38;2;r;g;b`: the color numbers are not attributes
                i += {"5": 2, "2": 4}.get(params[i + 1], 0)
            i += 1
    if not dim:
        out.append(pane_text[pos:])
    return "".join(out)


def load_unsent_drafts(path: str | None = None) -> dict[str, str]:
    target = path or _paths.UNSENT_DRAFTS_PATH
    try:
        if os.path.exists(target):
            with open(target, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return {str(k): str(v) for k, v in data.items() if v}
    except (OSError, ValueError, TypeError):
        pass
    return {}


def save_unsent_draft(session_id: str, text: str, path: str | None = None) -> None:
    if not session_id:
        return
    target = path or _paths.UNSENT_DRAFTS_PATH
    drafts = load_unsent_drafts(target)
    cleaned = (text or "").strip()
    if cleaned:
        drafts[str(session_id)] = cleaned
    else:
        drafts.pop(str(session_id), None)
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        tmp = f"{target}.tmp.{os.getpid()}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(drafts, f, ensure_ascii=False)
        os.replace(tmp, target)
    except OSError:
        pass


def pop_unsent_draft(session_id: str, path: str | None = None) -> str:
    if not session_id:
        return ""
    target = path or _paths.UNSENT_DRAFTS_PATH
    drafts = load_unsent_drafts(target)
    val = drafts.pop(str(session_id), "")
    if val:
        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            tmp = f"{target}.tmp.{os.getpid()}"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(drafts, f, ensure_ascii=False)
            os.replace(tmp, target)
        except OSError:
            pass
    return val


clear_unsent_draft = pop_unsent_draft


def extract_unsent_draft_from_pane_text(pane_text: str, last_sent_prompt: str = "") -> str:
    """Extract unsent input text from the bottom prompt (`❯ ...` or `> ...`) of a captured CLI pane."""
    if not pane_text:
        return ""
    lines = [ln.rstrip() for ln in pane_text.splitlines() if ln.strip()]
    tail = lines[-12:]
    sep_indices = [i for i, ln in enumerate(tail) if "────" in ln]
    search_region = tail[sep_indices[-2] + 1 : sep_indices[-1]] if len(sep_indices) >= 2 else tail
    for ln in reversed(search_region):
        s = ln.lstrip(" │┃╭╰─")
        if s in ("❯", ">", "›"):
            return ""
        for prefix in ("❯ ", "❯\xa0", "> ", "› "):
            if s.startswith(prefix):
                candidate = s[len(prefix):].strip()
                if not candidate:
                    return ""
                low = candidate.lower()
                if any(ph in low for ph in _PROMPT_PLACEHOLDERS):
                    return ""
                if last_sent_prompt and candidate == last_sent_prompt.strip():
                    return ""
                return candidate
    return ""


def capture_and_save_hubpool_draft(session: dict | None, path: str | None = None) -> str:
    """Capture bottom CLI pane of `pool:{wname}` and persist any unsent prompt text to `unsent_drafts.json`."""
    if not session or not session.get("id"):
        return ""
    wname = hubpool_win_name(session)
    env = dict(os.environ)
    env.pop("TMUX", None)
    try:
        res = subprocess.run(
            ["tmux", "-L", "hubpool", "capture-pane", "-e", "-p", "-t", f"pool:{wname}"],
            capture_output=True,
            text=True,
            env=env,
        )
        raw = strip_dim_text(res.stdout) if isinstance(getattr(res, "stdout", None), str) else ""
        draft = extract_unsent_draft_from_pane_text(raw, last_sent_prompt=str(session.get("lastPrompt") or ""))
        if draft:
            save_unsent_draft(session["id"], draft, path=path)
            # Length only: enough to trace a draft that reappears in another session, without logging what was typed
            _diag.log_tui_diag("draft_saved", session_id=str(session["id"]), wname=wname, chars=len(draft))
        return draft
    except Exception:
        return ""
