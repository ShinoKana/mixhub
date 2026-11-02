"""Provider storage schema adapters: every upstream path / DB / marker literal per CLI lives in this one table."""
from __future__ import annotations

import glob
import os
import re


# ---------------------------------------------------------------------------
# Provider storage schema adapters (single source of truth).
# When an upstream agent renames a path / DB file / marker field, edit THIS table only.
# Contract: (1) only whitelisted conversational turns drive activity time and prompts;
# (2) headless/subagent detection uses POSITIVE marker sets, unknown values fail open
#     (session stays visible in Recent rather than being silently hidden);
# (3) every provider-specific literal lives here, not in scattered call sites.
# ---------------------------------------------------------------------------
PROVIDER_SCHEMA_ADAPTERS = {
    "claude": {
        "transcript_glob": ".claude/projects/*/*.jsonl",
        "pid_state_glob": ".claude/sessions/*.json",
        "turn_types": ("user", "assistant"),
        # Synthetic `type == "user"` rows injected by hooks / slash expansion / compaction.
        "synthetic_user_flags": ("isMeta", "isVisibleInTranscriptOnly", "isCompactSummary"),
        "headless_entrypoints": ("sdk-cli", "headless"),
        "headless_kinds": ("headless", "print", "sdk", "subagent", "background", "non-interactive"),
    },
    "codex": {
        "state_db_glob": ".codex/state_*.sqlite",
        "state_db_fallback": ".codex/state_5.sqlite",
        "threads_table": "threads",
    },
    "agy": {
        "transcript_glob": ".gemini/antigravity-cli/brain/*/.system_generated/logs/transcript.jsonl",
        "summary_db": ".gemini/antigravity-cli/conversation_summaries.db",
        "summary_table": "conversation_summaries",
        # Seconds an on-disk transcript may lack its DB row before we stop assuming "transient headless".
        "db_lag_grace_s": 120,
        # Prompts sent from the TUI (never `agy -p`); `conversationId` is set from a conversation's second prompt on.
        "tui_history": ".gemini/antigravity-cli/history.jsonl",
        # A first prompt has no id; it matches the row's last_user_input_time within this many seconds.
        "tui_history_match_s": 3,
    },
    "opencode": {
        # v2: a background service owns sessions; clients (TUI, web, `run`) talk to it. Read its DB read-only.
        "db": ".local/share/opencode/opencode.db",
        "sessions_table": "session_v2",
        "messages_table": "session_message",
        # `opencode run` never marks a session viewed; a client that shows it does. Child sessions have parent_id.
        "headless_unviewed": True,
        # Subcommands that are not an interactive client (the TUI is `opencode [dir]`, `-c`, `-s <id>`, or `mini`).
        "non_interactive_subcommands": ("run", "serve", "api", "session", "service", "auth", "mcp", "plugin",
                                        "models", "stats", "pair", "reload", "debug", "acp", "upgrade", "update",
                                        "uninstall"),
    },
}

_CODEX_STATE_DB_RE = re.compile(r"^state_(\d+)\.sqlite$")


def claude_is_headless_marker(entrypoint=None, kind=None):
    """Positive headless set; unknown entrypoint/kind values are NOT headless (fail open)."""
    adapter = PROVIDER_SCHEMA_ADAPTERS["claude"]
    if str(entrypoint or "").strip().lower() in adapter["headless_entrypoints"]:
        return True
    return str(kind or "").strip().lower() in adapter["headless_kinds"]


def claude_is_synthetic_user(row):
    if not isinstance(row, dict) or row.get("type") != "user":
        return False
    return any(row.get(flag) for flag in PROVIDER_SCHEMA_ADAPTERS["claude"]["synthetic_user_flags"])


def codex_state_db_candidates(path_fn):
    """All `.codex/state_<N>.sqlite` files, newest schema first: sort key (N, mtime) descending."""
    adapter = PROVIDER_SCHEMA_ADAPTERS["codex"]
    ranked = []
    for candidate in glob.glob(path_fn(adapter["state_db_glob"])):
        match = _CODEX_STATE_DB_RE.match(os.path.basename(candidate))
        if not match:
            continue
        try:
            mtime = os.stat(candidate).st_mtime
        except OSError:
            continue
        ranked.append(((int(match.group(1)), mtime), candidate))
    ranked.sort(reverse=True)
    paths = [p for _, p in ranked]
    fallback = path_fn(adapter["state_db_fallback"])
    if fallback not in paths:
        paths.append(fallback)
    return paths
