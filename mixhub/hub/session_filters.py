"""Which sessions are automated, subagent or temporary: built-in markers, gitignored local rules
(`data/local_rules.json` + `Preferences.ignore_prompt_patterns`) and the cached runtime `Preferences`.

`_DISCOVER_GEN` / `_IN_DISCOVER` are owned here; `DiscoveryMixin.discover` bumps them so the rule and
Preferences caches are reused within one discovery pass.
"""
from __future__ import annotations

import os

from mixhub.hub import constants
from mixhub.hub.titles import FALLBACK_TITLE_RE
from mixhub.hub.util import read_json


def codex_automation_reason(source=None, title="", first_msg="", originator=None) -> str:
    """
    Why a Codex thread counts as automated, or "" when it doesn't. What Codex recorded about who started the thread
    decides first (`originator`, `source`); text rules only cover threads older than those fields.
    """
    # A task Claude Code dispatched to Codex (codex-companion); its result goes back to that Claude session
    if originator == "Claude Code":
        return "originator"
    if isinstance(source, dict) or (isinstance(source, str) and (source == "exec" or source.startswith("{"))):
        return "source"
    t = (title or "").strip()
    m = (first_msg or "").strip()
    if "<task>" in m or "<task>" in t:
        return "text:<task>"
    for prefix in ("Codex Companion Task:", "## Referenced ChatGPT conversation:"):
        if m.startswith(prefix) or t.startswith(prefix):
            return "text:" + prefix
    for word in ("--help", "exit", "usage"):
        if word in (m, t):
            return "text:" + word
    low = m.casefold()
    for prefix in _load_local_rules().get("codex_automated_prefixes") or ():
        if low.startswith(prefix.casefold()):
            return "text:" + prefix
    rule = automated_text_rule(t) or automated_text_rule(m)
    return "text:" + rule if rule else ""


def is_codex_automated(source=None, title="", first_msg="", originator=None):
    return bool(codex_automation_reason(source, title, first_msg, originator))


_DISCOVER_GEN: int = 0
_IN_DISCOVER: bool = False
_LOCAL_RULES_CACHE: dict = {
    "mtime": None,
    "gen": None,
    "data": {"automated_prefixes": [], "automated_substrings": []},
    "folded_prefixes": (),
    "folded_substrings": (),
}
# MixHub's own branch-fork marker (handoff.py); personal prompt templates belong in local rules / Preferences
_BUILTIN_AUTOMATED_PREFIXES = ("[⑂ BRANCH TASK]",)
_BUILTIN_AUTOMATED_SUBSTRINGS = ("[⑂ BRANCH TASK]",)


_UI_PREFS_CACHE: dict = {"key": None, "gen": None, "data": {}}


def _ui_prefs_path() -> str | None:
    """The runtime `Preferences` JSON path when that file exists (see `mixhub.core.prefs.preferences_path`)."""
    from mixhub.core.prefs import preferences_path

    cand = preferences_path()
    return cand if os.path.isfile(cand) else None


def _load_ui_prefs() -> dict:
    """Read the gitignored runtime `Preferences` JSON with an mtime + generation cache (returns `{}` if missing or malformed)."""
    gen_token = (_DISCOVER_GEN, os.environ.get("MIXHUB_PREFERENCES_PATH"))
    if _IN_DISCOVER and _UI_PREFS_CACHE.get("gen") == gen_token:
        return _UI_PREFS_CACHE["data"]
    path = _ui_prefs_path()
    try:
        st = os.stat(path) if path else None
        key = (path, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns) if st is not None else None
    except OSError:
        key = None
    if key is not None and _UI_PREFS_CACHE["key"] == key:
        _UI_PREFS_CACHE["gen"] = gen_token
        return _UI_PREFS_CACHE["data"]
    data = read_json(path, {}) if key is not None else {}
    if not isinstance(data, dict):
        data = {}
    _UI_PREFS_CACHE["key"] = key
    _UI_PREFS_CACHE["gen"] = gen_token
    _UI_PREFS_CACHE["data"] = data
    return data


def _load_local_rules() -> dict:
    """
    Merge gitignored automation rules with an mtime + generation cache:
      - `data/local_rules.json` (`automated_prefixes` / `automated_substrings`, and `relayed_prefixes`: notes your own
        tools type into an agent on another agent's behalf);
      - `Preferences["ignore_prompt_patterns"]` (`has:<substr>` -> substrings, plain strings -> prefixes).
    """
    gen_token = (_DISCOVER_GEN, os.environ.get("MIXHUB_PREFERENCES_PATH"))
    if _IN_DISCOVER and _LOCAL_RULES_CACHE.get("gen") == gen_token:
        return _LOCAL_RULES_CACHE["data"]
    path = os.path.join(constants.ROOT, "data", "local_rules.json")
    try:
        st = os.stat(path)
        mtime = (st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
    except OSError:
        mtime = -1
    prefs = _load_ui_prefs()
    cache_key = (mtime, _UI_PREFS_CACHE["key"])
    if _LOCAL_RULES_CACHE["mtime"] == cache_key:
        _LOCAL_RULES_CACHE["gen"] = gen_token
        return _LOCAL_RULES_CACHE["data"]
    raw = read_json(path, {}) if mtime != -1 else {}
    if not isinstance(raw, dict):
        raw = {}
    data = {}
    for key in ("automated_prefixes", "automated_substrings", "relayed_prefixes", "codex_automated_prefixes"):
        vals = raw.get(key)
        data[key] = [str(v) for v in vals if isinstance(v, str) and v.strip()] if isinstance(vals, list) else []
    patterns = prefs.get("ignore_prompt_patterns")
    if isinstance(patterns, list):
        for pat in patterns:
            if not isinstance(pat, str) or not pat.strip():
                continue
            if pat.startswith("has:"):
                sub = pat[4:]
                if sub.strip() and sub not in data["automated_substrings"]:
                    data["automated_substrings"].append(sub)
            elif pat not in data["automated_prefixes"]:
                data["automated_prefixes"].append(pat)
    _LOCAL_RULES_CACHE["mtime"] = cache_key
    _LOCAL_RULES_CACHE["gen"] = gen_token
    _LOCAL_RULES_CACHE["data"] = data
    _LOCAL_RULES_CACHE["folded_prefixes"] = tuple(p.casefold() for p in data["automated_prefixes"])
    _LOCAL_RULES_CACHE["folded_substrings"] = tuple(m.casefold() for m in data["automated_substrings"])
    return data


def automated_text_rule(text: str) -> str:
    """The built-in or local rule that marks `text` as automation, or "" when none does."""
    if not text:
        return ""
    s = str(text).strip()
    if not s:
        return ""
    for p in _BUILTIN_AUTOMATED_PREFIXES:
        if s.startswith(p):
            return p
    for m in _BUILTIN_AUTOMATED_SUBSTRINGS:
        if m in s:
            return m
    low = s.casefold()
    rules = _load_local_rules()
    # Local rules match case-insensitively (casefold) so user-supplied patterns need not mirror exact casing.
    prefixes = _LOCAL_RULES_CACHE.get("folded_prefixes") or tuple(p.casefold() for p in (rules.get("automated_prefixes") or []))
    for p in prefixes:
        if low.startswith(p):
            return p
    substrings = _LOCAL_RULES_CACHE.get("folded_substrings") or tuple(m.casefold() for m in (rules.get("automated_substrings") or []))
    for m in substrings:
        if m in low:
            return "has:" + m
    return ""


def is_automated_or_subagent_text(text: str) -> bool:
    return bool(automated_text_rule(text))


def is_users_own_prompt(text: str) -> bool:
    """A prompt typed in an agent's TUI came from the user: not automation text, and not a note another agent relayed
    (`relayed_prefixes`). Relayed notes don't decide a session either way, unlike `automated_prefixes`."""
    if is_automated_or_subagent_text(text):
        return False
    relayed = tuple(p.casefold() for p in (_load_local_rules().get("relayed_prefixes") or []))
    return not (relayed and str(text or "").strip().casefold().startswith(relayed))


def is_temp_session(s: dict) -> bool:
    if s.get("parentId") or s.get("isSubagent") or s.get("consultant") or s.get("automated") or s.get("agentName"):
        return True
    cwd = str(s.get("cwd") or "")
    if cwd.startswith(("/private/tmp", "/tmp")):
        return True
    for field in ("title", "firstPrompt", "lastPrompt", "snapshotSummary", "sourceTitle"):
        if is_automated_or_subagent_text(s.get(field) or ""):
            return True
    title = str(s.get("title") or "").strip()
    if FALLBACK_TITLE_RE.match(title):
        path = s.get("path")
        msg_cnt = int(s.get("messageCount") or 0)
        if (not path or not os.path.isfile(path) or msg_cnt == 0) and not s.get("hibernatedAt"):
            return True
    # Codex thread with no rollout `.jsonl` on disk: nothing to preview or resume unless it is live / hibernated.
    if s.get("tool") == "codex" and (not s.get("path") or not os.path.isfile(s["path"])) \
            and not s.get("isLive") and not s.get("hibernatedAt") and not s.get("hibernatedByHub"):
        return True
    return False
