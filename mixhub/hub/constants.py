"""Hub-wide constants: state root, transcript head/tail read sizes, session UUID patterns, tool codes, cache schema.

`ROOT` is read as `constants.ROOT` everywhere so one patch (tests point it at a temp dir) reaches every reader.
"""
from __future__ import annotations

import re

from mixhub.core import paths


# The hub's root is the state root (`data/`, `hub_state.json`, ...), not the code location
ROOT = paths.STATE_ROOT
# 64 KiB tail matches Claude Code 2.1.x `Jb = 65536`: `reAppendSessionMetadata` skips re-appending
# title metadata already present in the last `Jb / 2`, so the latest custom/ai title can sit up to ~32 KiB back.
HEAD_BYTES, TAIL_BYTES = 24 * 1024, 64 * 1024
TAIL_SCAN_STEP_BYTES, TAIL_SCAN_MAX_BYTES = 64 * 1024, 2 * 1024 * 1024
UUID = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
_UUID_RE = re.compile(UUID)
_ROLLOUT_UUID_RE = re.compile("(" + UUID + r")\.jsonl$")
_SUBAGENT_CREATED_RE = re.compile(
    rb'Created the following subagent:.{0,600}?\\"conversationId\\":\s*\\"(' + UUID.encode() + rb')\\"',
    re.S,
)
_SUBAGENT_INVOKE_RE = re.compile(
    rb'"name"\s*:\s*"invoke_subagent"[^\n]{0,1600}?"conversationId"\s*:\s*"(' + UUID.encode() + rb')"'
)
_OPEN_LOCK_PATTERNS = {
    "agy": re.compile(r"(?:/presence/|/conversations/)(" + UUID + r")\.(?:lock|db(?:-wal|-shm)?)$"),
    "codex": re.compile(r"/thread-writer-locks/(" + UUID + r")\.lock"),
}
_OPEN_PATH_PATTERNS = {
    "agy": re.compile(r"(?:/brain/|/conversations/|/presence/)(" + UUID + ")"),
    "codex": re.compile(r"(?:rollout-.*-|/thread-writer-locks/)(" + UUID + r")\.(?:jsonl|lock)"),
    "claude": re.compile(r"/projects/[^/]+/(" + UUID + r")\.jsonl"),
    # OpenCode keeps sessions in its background service + one SQLite DB: the TUI opens no per-session file.
    "opencode": re.compile(r"(?!)"),
}
TOOLS = {"claude": "C", "agy": "A", "codex": "X", "opencode": "O"}
# An Active entry with no process and no activity for this long moves to Recent (12h idle hibernate + 12h)
WORKING_SET_IDLE_EXPIRE_SEC = 24 * 3600
INDEX_CACHE_SCHEMA_VERSION = 16  # 16: a trailing /compact summary no longer means the model owes a reply
