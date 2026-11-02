#!/usr/bin/env python3
"""`mixhub.probe.patterns`: tail / context / excerpt sizes, the probe rules file path, the built-in rule tables and screen regexes."""
from __future__ import annotations

import os
import re

from mixhub.core.paths import STATE_ROOT


TAIL_LINES = 15
CONTEXT_LINES = 60
EXCERPT_LINES = 6
DEFAULT_PROBE_RULES_PATH = os.path.join(STATE_ROOT, "data", "probe_rules.json")

ANSI_CSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
ANSI_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
BOX_EDGE_RE = re.compile(r"^[\s│┃╭╮╰╯┌┐└┘├┤┬┴┼]+|[\s│┃╭╮╰╯┌┐└┘├┤┬┴┼]+$")
RULE_RE = re.compile(r"^[╭╰┌└├]?[─━═╌┄]{8,}[╮╯┐┘┤]?$")
TRANSCRIPT_RE = re.compile(r"^[⏺•✦]\s")
TOOL_OUTPUT_RE = re.compile(r"^[⎿]\s*")
OPTION_RE = re.compile(
    r"^(?P<cur>[❯›>▶►●→➤]\s*)?(?P<radio>[○◯]\s*)?(?P<num>[1-9])(?P<delim>[.)])\s+(?P<label>\S.*?)$"
)
PROMPT_RE = re.compile(r"^[❯›>](?:\s|$)")
INLINE_HOTKEY_RE = re.compile(r"(?:\((?:[a-z]|esc|escape)\)|\[(?:[a-z]|esc|escape)\])\s*$", re.I)
MARKDOWN_LABEL_RE = re.compile(r"\*\*|`[^`]+`|\]\(")
NESTED_QUANTIFIER_RE = re.compile(r"\([^)]*[+*]\)[+*{]")
SECRET_REDACT_RE = re.compile(
    r"(?:Bearer\s+\S+|sk-[A-Za-z0-9_-]{12,}|ghp_[A-Za-z0-9]{12,}|AKIA[0-9A-Z]{12,}|password\s*=\s*\S+)",
    re.I,
)

BUILTIN_PROBE_RULES: dict[str, list[str]] = {
    "permission_patterns": [
        r"Do you want to (?:proceed|make this edit|create|allow|run|execute|delete|modify)",
        r"Would you like to (?:run|make|apply|allow|execute)",
        r"Allow (?:execution|running|tool|command|edit|write|bash)\b",
        r"Allow\s+[^\n?]+\?",
        r"Apply this change\?",
        r"requires? (?:approval|permission)",
        r"\bneeds\s+approval\s+for\b",
        r"\b(?:ctrl|alt)\+[a-z0-9]\s+approve\b",
    ],
    "trust_patterns": [
        r"\btrust (?:this|the files in this) folder\b",
        r"Do you trust the files in this folder\?",
        r"Yes,\s*I trust this folder",
        r"Is this a project you (?:created|trust)",
        r"allow Codex to work in this folder",
    ],
    "question_patterns": [
        r"(?:↑/↓|up/down|arrow keys)\s+to\s+navigate",
        r"Enter to select",
        r"Space to (?:select|toggle)",
        r"Tab/Arrow keys",
    ],
    "false_positive_patterns": [
        r"^Choose your adventure:",
    ],
    "once_patterns": [
        r"^(?:yes\b|allow\b|approve\b|proceed\b|ok\b|run\b|はい\b|許可|是\b|允许|准许)|\(y\)\s*$|\[y\]",
    ],
    "session_patterns": [
        r"don['’]?t\s+ask\s+again|do\s+not\s+ask\s+again|\balways\b|\bthis\s+session\b"
        r"|\ballow\s+all\b|\bfor\s+this\s+session\b|\(a\)\s*$|shift\+tab|常に|セッション|始终|本会话|不再询问",
    ],
    "deny_patterns": [
        r"^(?:no\b|deny\b|reject\b|cancel\b|skip\b|abort\b|don['’]?t\s+allow\b|exit\b|いいえ\b|拒否|否\b|拒绝|取消)|\(esc\)\s*$|\[n\]",
    ],
    "working_patterns": [
        r"\b(?:esc|ctrl\+c)\s+to\s+(?:interrupt|cancel)\b",
    ],
}

RULE_KEY_ALIASES: dict[str, str] = {
    "permission_patterns": "permission_patterns",
    "permission_question": "permission_patterns",
    "trust_patterns": "trust_patterns",
    "trust_question": "trust_patterns",
    "question_patterns": "question_patterns",
    "question_hints": "question_patterns",
    "false_positive_patterns": "false_positive_patterns",
    "veto": "false_positive_patterns",
    "once_patterns": "once_patterns",
    "once_label": "once_patterns",
    "session_patterns": "session_patterns",
    "session_label": "session_patterns",
    "session_choice_keywords": "session_patterns",
    "deny_patterns": "deny_patterns",
    "deny_label": "deny_patterns",
    "working_patterns": "working_patterns",
}

TRUST_RE = re.compile("|".join(BUILTIN_PROBE_RULES["trust_patterns"]), re.I)
PERM_Q_RE = re.compile("|".join(BUILTIN_PROBE_RULES["permission_patterns"]), re.I)
QUESTION_HINT_RE = re.compile("|".join(BUILTIN_PROBE_RULES["question_patterns"]), re.I)
OPT_DENY_RE = re.compile("|".join(BUILTIN_PROBE_RULES["deny_patterns"]), re.I)
OPT_SESSION_RE = re.compile("|".join(BUILTIN_PROBE_RULES["session_patterns"]), re.I)
OPT_ONCE_RE = re.compile("|".join(BUILTIN_PROBE_RULES["once_patterns"]), re.I)
OPT_TRUST_DENY_RE = re.compile(r"^(?:no\b|exit\b|cancel\b|don['’]?t\s+trust\b|いいえ\b|拒否|否\b|拒绝)", re.I)

YN_RE = re.compile(
    r"(?:\[\s*y(?:es)?\s*/\s*n(?:o)?\s*\]|\(\s*y(?:es)?\s*/\s*n(?:o)?\s*\))\s*[:?]?\s*$",
    re.I,
)
WORKING_RE = re.compile(r"\b(?:esc|ctrl\+c)\s+(?:to\s+)?(?:interrupt|cancel)\b", re.I)  # OpenCode: "esc interrupt"
SPINNER_RE = re.compile(r"^[\u2800-\u28ff✻✶✳✢✽·*•]\s*\S[^…\n]{0,48}(?:…|\.\.\.)")
COMPLETED_VERB_RE = re.compile(
    r"^[\u2800-\u28ff✻✶✳✢✽·*•]\s*(?:Cooked|Worked|Baked|Brewed|Churned|Cogitated|Thought|Ran|Done)\s+for\s+\d+",
    re.I,
)
TITLE_BUSY_RE = re.compile(r"^[\u2800-\u28ff]")
CMD_DOLLAR_RE = re.compile(r"^\$\s+(?P<cmd>.+)$")
GEMINI_EXEC_RE = re.compile(r"Allow execution of:?\s*['\"`]?(?P<cmd>[^'\"`\n?]+)", re.I)
CLAUDE_HEAD_RE = re.compile(
    r"^(?:Bash(?:\s+command)?|Edit(?:\s+file)?|Create(?:\s+file)?|Write(?:\s+file)?"
    r"|Read(?:\s+file)?|Fetch|Web\s*Fetch|Web\s*Search|Tool\s+use|Notebook\s*edit"
    r"|Run\s+command|Execute\s+command)\b",
    re.I,
)
FOOTER_HINT_RE = re.compile(
    r"^(?:Esc to (?:cancel|interrupt)|↑/↓ to navigate|Enter to (?:select|confirm)|Tab to|ctrl\+c to)",
    re.I,
)
STRUCTURAL_FOOTER_KEYBAR_RE = re.compile(
    r"(?:\b(?:esc|escape|enter|return|tab|space)\b|[⏎↵↑↓←→⌃⇧⌥]|(?:ctrl|shift|alt)\+[a-z])",
    re.I,
)
IDLE_STATUS_BAR_RE = re.compile(
    r"(?:\?\s+for\s+shortcuts|accept\s+edits\s+on|⏎\s*send\b|shift\+tab\s+to\s+cycle)",
    re.I,
)
AGY_NEEDS_APPROVAL_RE = re.compile(
    r"^(?P<agent>\S+)\s+needs\s+approval\s+for\s+(?P<tool>\S+)\s*$",
    re.I,
)
AGY_HOTKEY_PAIR_RE = re.compile(
    r"\b(?P<mod>ctrl|alt)\+(?P<key>[a-z0-9])\s+(?P<action>[a-z_]+)\b",
    re.I,
)
BARE_COMPOSER_PROMPT_RE = re.compile(r"^[❯›>]\s*$")
