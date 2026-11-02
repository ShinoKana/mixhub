#!/usr/bin/env python3
"""`mixhub.probe.classify`: classify a captured pane (`classify_pane_text`, alias `probe_pane_state`) and probe a session's live pane (`probe_session_pane`)."""
from __future__ import annotations

import re
from typing import Callable

from mixhub.probe.capture import capture_pane, resolve_pool_pane
from mixhub.probe.menus import (
    _compute_fingerprint,
    _extract_header_and_command,
    _find_numbered_menu,
    _match_hotkey_approval_box,
)
from mixhub.probe.patterns import (
    CLAUDE_HEAD_RE,
    CMD_DOLLAR_RE,
    CODEX_HISTORY_RE,
    COMPLETED_VERB_RE,
    CONTEXT_LINES,
    FOOTER_HINT_RE,
    GEMINI_EXEC_RE,
    OPT_TRUST_DENY_RE,
    RULE_RE,
    SPINNER_RE,
    TAIL_LINES,
    TITLE_BUSY_RE,
    TOOL_OUTPUT_RE,
    WORKING_RE,
    YN_RE,
)
from mixhub.probe.rules import _compile_rules_dict, load_probe_rules, _match_any
from mixhub.probe.text import _build_raw_excerpt, clean_terminal_line, redact_secrets


def classify_pane_text(
    text_or_lines: str | list[str],
    pane_title: str = "",
    *,
    rules_path: str | None = None,
    rules: dict | None = None,
) -> dict:
    """
    Two-Layer Declarative Classifier over the bottom 15 non-empty lines (`tail`) + context:
      - **Layer 1 (`match_layer="structural"`)**: Structural Geometry Heuristics (focus cursor `❯`/`>`/`●`
        pointing at a contiguous `1..N` option list, footer key-hint bar `Esc`/`Enter`/`↑↓`, or `[y/n]`
        suffix) that detect interactive blockers even when CLI versions change their English/CJK phrasing.
      - **Layer 2 (`match_layer="rules"`)**: Hot-reloadable regex rules from `data/probe_rules.json`
        (`load_probe_rules`) + built-in patterns (`permission_patterns`, `trust_patterns`,
        `question_patterns`, `false_positive_patterns`).
    Returns:
      - `state`: `"awaiting_permission"` | `"awaiting_trust"` | `"awaiting_question"` | `"working"` | `"idle"` | `"unknown"`
      - `match_layer`: `"rules"` | `"structural"` | `""`
      - `prompt_snippet`: 6-line raw terminal excerpt (with cursor preserved and secrets redacted)
      - `raw_excerpt`: `list[str]` (up to 6 lines)
      - `choice_style`: `"numbered"` | `"yn"` | `""`
      - `choices`: list of `{"key": str, "label": str, "decision": str, "selected": bool}`
      - `keys`: `{"once": str | None, "session": str | None, "deny": str | None}`
      - `keys_source`: `{"once": str | None, "session": str | None, "deny": str | None}`
      - `tool_summary`, `command`, `question`, `fingerprint`, `tail`, `rules_error`
    """
    if rules is not None:
        if "compiled" in rules:
            active_rules = rules
        else:
            active_rules, _ = _compile_rules_dict(rules, source="custom")
    else:
        active_rules = load_probe_rules(rules_path)
    comp = active_rules["compiled"]
    rules_error = str(active_rules.get("error") or "")

    if text_or_lines is None:
        return {
            "ok": False,
            "state": "unknown",
            "rule": "empty_input",
            "match_layer": "",
            "choice_style": "",
            "choices": [],
            "keys": {"once": None, "session": None, "deny": None},
            "keys_source": {"once": None, "session": None, "deny": None},
            "tool_summary": "",
            "command": "",
            "question": "",
            "prompt_snippet": "",
            "raw_excerpt": [],
            "fingerprint": "",
            "pane_title": str(pane_title or ""),
            "tail": [],
            "rules_error": rules_error,
        }

    if isinstance(text_or_lines, (list, tuple)):
        raw_lines = [clean_terminal_line(str(x)) for x in text_or_lines]
    else:
        raw_lines = [clean_terminal_line(ln) for ln in str(text_or_lines).splitlines()]

    while raw_lines and not raw_lines[-1]:
        raw_lines.pop()
    raw_non_empty_with_rules = [ln for ln in raw_lines if ln]
    non_empty = [ln for ln in raw_lines if ln and not RULE_RE.match(ln)]
    tail = non_empty[-TAIL_LINES:]
    raw_tail_with_rules = raw_non_empty_with_rules[-(TAIL_LINES + 6):]

    if not tail:
        busy_title = bool(TITLE_BUSY_RE.search(str(pane_title or "").strip()))
        return {
            "ok": True,
            "state": "working" if busy_title else "idle",
            "rule": "title_spinner" if busy_title else "blank_pane",
            "match_layer": "",
            "choice_style": "",
            "choices": [],
            "keys": {"once": None, "session": None, "deny": None},
            "keys_source": {"once": None, "session": None, "deny": None},
            "tool_summary": "",
            "command": "",
            "question": "",
            "prompt_snippet": "",
            "raw_excerpt": [],
            "fingerprint": "",
            "pane_title": str(pane_title or ""),
            "tail": [],
            "rules_error": rules_error,
        }

    hotkey_box = _match_hotkey_approval_box(tail, non_empty, pane_title, comp, rules_error)
    if hotkey_box is not None:
        return hotkey_box

    menu = _find_numbered_menu(
        tail,
        raw_tail_with_rules=raw_tail_with_rules,
        rules_compiled=comp,
    )
    if menu:
        first_opt_line = tail[menu[0][0]]
        header_block, tool_summary, command, question = _extract_header_and_command(
            raw_non_empty_with_rules, first_opt_line, rules_compiled=comp
        )
        header_text = "\n".join(header_block)
        opt_labels = [item[3] for item in menu]
        all_menu_text = header_text + "\n" + "\n".join(opt_labels)

        # Veto / false_positive_patterns check from probe_rules.json
        if not _match_any(comp["false_positive_patterns"], all_menu_text):
            raw_excerpt = _build_raw_excerpt(non_empty, tail, menu=menu, question=question)
            prompt_snippet = "\n".join(raw_excerpt)

            # Layer 2 Rule A: Folder / workspace trust prompt
            if _match_any(comp["trust_patterns"], all_menu_text):
                once_key: str | None = None
                deny_key: str | None = None
                choices = []
                for _, num, has_cur, label in menu:
                    k = str(num)
                    if (OPT_TRUST_DENY_RE.search(label) or _match_any(comp["deny_patterns"], label)) and deny_key is None:
                        decision = "deny"
                        deny_key = k
                    elif once_key is None:
                        decision = "once"
                        once_key = k
                    else:
                        decision = "other"
                    choices.append({"key": k, "label": label, "decision": decision, "selected": has_cur})
                keys = {"once": once_key or "1", "session": once_key or "1", "deny": deny_key or "2"}
                fp = _compute_fingerprint("awaiting_trust", tool_summary, command, opt_labels)
                return {
                    "ok": True,
                    "state": "awaiting_trust",
                    "rule": "numbered_trust_menu",
                    "match_layer": "rules",
                    "choice_style": "numbered",
                    "choices": choices,
                    "keys": keys,
                    "keys_source": {"once": "semantic", "session": "semantic", "deny": "semantic"},
                    "tool_summary": tool_summary or "Trust folder",
                    "command": command,
                    "question": question or "Do you trust the files in this folder?",
                    "prompt_snippet": prompt_snippet,
                    "raw_excerpt": raw_excerpt,
                    "fingerprint": fp,
                    "pane_title": str(pane_title or ""),
                    "tail": tail,
                    "rules_error": rules_error,
                }

            # Evaluate semantic label matches and Layer 2 header matches
            has_once_opt = any(_match_any(comp["once_patterns"], lbl) for lbl in opt_labels)
            has_perm_header = bool(
                _match_any(comp["permission_patterns"], header_text)
                or CLAUDE_HEAD_RE.match(header_text)
                or GEMINI_EXEC_RE.search(header_text)
            )
            has_session_or_deny = any(
                _match_any(comp["session_patterns"], lbl) or _match_any(comp["deny_patterns"], lbl)
                for lbl in opt_labels
            )
            has_question_hint = _match_any(comp["question_patterns"], "\n".join(tail))

            # Layer 2 Rule B: Numbered permission menu matched by rules / semantic labels
            if (has_once_opt and (has_perm_header or has_session_or_deny)) or (has_perm_header and not has_question_hint):
                once_key = None
                session_key = None
                deny_key = None
                keys_source: dict[str, str | None] = {"once": None, "session": None, "deny": None}
                choices = []
                for _, num, has_cur, label in menu:
                    k = str(num)
                    if _match_any(comp["deny_patterns"], label):
                        decision = "deny"
                        if deny_key is None:
                            deny_key = k
                            keys_source["deny"] = "semantic"
                    elif _match_any(comp["session_patterns"], label):
                        decision = "session"
                        if session_key is None:
                            session_key = k
                            keys_source["session"] = "semantic"
                    elif _match_any(comp["once_patterns"], label):
                        decision = "once"
                        if once_key is None:
                            once_key = k
                            keys_source["once"] = "semantic"
                    else:
                        decision = "other"
                    choices.append({"key": k, "label": label, "decision": decision, "selected": has_cur})

                # Positional fallback for novel option labels under a matched permission header
                if once_key is None and choices:
                    once_key = choices[0]["key"]
                    choices[0]["decision"] = "once"
                    keys_source["once"] = "positional"
                if session_key is None and len(choices) >= 3 and choices[1]["decision"] == "other":
                    session_key = choices[1]["key"]
                    choices[1]["decision"] = "session"
                    keys_source["session"] = "positional"
                if deny_key is None:
                    if len(choices) >= 2 and choices[-1]["decision"] == "other":
                        deny_key = choices[-1]["key"]
                        choices[-1]["decision"] = "deny"
                        keys_source["deny"] = "positional"
                    elif re.search(r"\b(?:esc|escape)\b", "\n".join(tail), re.I):
                        deny_key = "Escape"
                        keys_source["deny"] = "positional"

                keys = {"once": once_key, "session": session_key, "deny": deny_key}
                fp = _compute_fingerprint("awaiting_permission", tool_summary, command, opt_labels)
                return {
                    "ok": True,
                    "state": "awaiting_permission",
                    "rule": "numbered_permission_menu",
                    "match_layer": "rules",
                    "choice_style": "numbered",
                    "choices": choices,
                    "keys": keys,
                    "keys_source": keys_source,
                    "tool_summary": tool_summary,
                    "command": command,
                    "question": question,
                    "prompt_snippet": prompt_snippet,
                    "raw_excerpt": raw_excerpt,
                    "fingerprint": fp,
                    "pane_title": str(pane_title or ""),
                    "tail": tail,
                    "rules_error": rules_error,
                }

            # Layer 1 Structural Heuristic vs Question Menu when prompt wording is completely novel:
            # Check whether options are tightly packed (no multi-line option descriptions) or have
            # command/tool/session/deny cues.
            max_idx_gap = max(
                (menu[i + 1][0] - menu[i][0] for i in range(len(menu) - 1)),
                default=1,
            )
            is_compact_menu = max_idx_gap == 1 and 2 <= len(menu) <= 4
            has_cmd_or_path_preview = bool(
                command
                or CMD_DOLLAR_RE.search(header_text)
                or re.search(r"(?:^|\s)(?:/[\w.-]+)+", header_text)
            )

            if (not has_question_hint and is_compact_menu) or has_session_or_deny or has_cmd_or_path_preview:
                once_key = None
                session_key = None
                deny_key = None
                keys_source = {"once": None, "session": None, "deny": None}
                choices = []
                for _, num, has_cur, label in menu:
                    k = str(num)
                    if _match_any(comp["deny_patterns"], label):
                        decision = "deny"
                        if deny_key is None:
                            deny_key = k
                            keys_source["deny"] = "semantic"
                    elif _match_any(comp["session_patterns"], label):
                        decision = "session"
                        if session_key is None:
                            session_key = k
                            keys_source["session"] = "semantic"
                    elif _match_any(comp["once_patterns"], label):
                        decision = "once"
                        if once_key is None:
                            once_key = k
                            keys_source["once"] = "semantic"
                    else:
                        decision = "other"
                    choices.append({"key": k, "label": label, "decision": decision, "selected": has_cur})

                if once_key is None and choices and choices[0]["decision"] == "other":
                    once_key = choices[0]["key"]
                    choices[0]["decision"] = "once"
                    keys_source["once"] = "positional"
                if session_key is None and len(choices) >= 3 and choices[1]["decision"] == "other":
                    session_key = choices[1]["key"]
                    choices[1]["decision"] = "session"
                    keys_source["session"] = "positional"
                if deny_key is None:
                    if len(choices) >= 2 and choices[-1]["decision"] == "other":
                        deny_key = choices[-1]["key"]
                        choices[-1]["decision"] = "deny"
                        keys_source["deny"] = "positional"
                    elif re.search(r"\b(?:esc|escape)\b", "\n".join(tail), re.I):
                        deny_key = "Escape"
                        keys_source["deny"] = "positional"

                keys = {"once": once_key, "session": session_key, "deny": deny_key}
                fp = _compute_fingerprint("awaiting_permission", tool_summary, command, opt_labels)
                return {
                    "ok": True,
                    "state": "awaiting_permission",
                    "rule": "structural_permission_menu",
                    "match_layer": "structural",
                    "choice_style": "numbered",
                    "choices": choices,
                    "keys": keys,
                    "keys_source": keys_source,
                    "tool_summary": tool_summary,
                    "command": command,
                    "question": question,
                    "prompt_snippet": prompt_snippet,
                    "raw_excerpt": raw_excerpt,
                    "fingerprint": fp,
                    "pane_title": str(pane_title or ""),
                    "tail": tail,
                    "rules_error": rules_error,
                }

            # Rule 4: Interactive multiple-choice question (AskUserQuestion / selection menu)
            choices = [
                {"key": str(num), "label": label, "decision": f"option_{num}", "selected": has_cur}
                for _, num, has_cur, label in menu
            ]
            q_layer = "rules" if has_question_hint else "structural"
            fp = _compute_fingerprint("awaiting_question", tool_summary, command, opt_labels)
            return {
                "ok": True,
                "state": "awaiting_question",
                "rule": "numbered_question_menu" if q_layer == "rules" else "structural_question_menu",
                "match_layer": q_layer,
                "choice_style": "numbered",
                "choices": choices,
                "keys": {"once": "1", "session": None, "deny": "Escape"},
                "keys_source": {"once": "positional", "session": None, "deny": "positional"},
                "tool_summary": tool_summary,
                "command": command,
                "question": question,
                "prompt_snippet": prompt_snippet,
                "raw_excerpt": raw_excerpt,
                "fingerprint": fp,
                "pane_title": str(pane_title or ""),
                "tail": tail,
                "rules_error": rules_error,
            }

    # Rule 5: [y/n] / (y/n) / [Y/n] / [y/N] confirmation prompt on the bottom line(s)
    for check_ln in reversed(tail[-3:]):
        if FOOTER_HINT_RE.match(check_ln):
            continue
        if TOOL_OUTPUT_RE.match(check_ln):
            break
        if YN_RE.search(check_ln):
            header_block, tool_summary, command, question = _extract_header_and_command(
                raw_non_empty_with_rules, check_ln, rules_compiled=comp
            )
            if _match_any(comp["false_positive_patterns"], "\n".join(header_block + [check_ln])):
                break
            question = question or redact_secrets(check_ln)
            yn_layer = (
                "rules"
                if _match_any(comp["permission_patterns"], "\n".join(header_block + [check_ln]))
                else "structural"
            )
            raw_excerpt = _build_raw_excerpt(non_empty, tail, menu=None, question=question)
            prompt_snippet = "\n".join(raw_excerpt)
            fp = _compute_fingerprint("awaiting_permission", tool_summary or check_ln, command, ["y", "n"])
            return {
                "ok": True,
                "state": "awaiting_permission",
                "rule": "yn_permission_prompt",
                "match_layer": yn_layer,
                "choice_style": "yn",
                "choices": [
                    {"key": "y", "label": "Yes", "decision": "once", "selected": False},
                    {"key": "n", "label": "No", "decision": "deny", "selected": False},
                ],
                "keys": {"once": "y", "session": None, "deny": "n"},
                "keys_source": {"once": "semantic", "session": None, "deny": "semantic"},
                "tool_summary": redact_secrets((tool_summary or check_ln)[:80]),
                "command": command,
                "question": question[:200],
                "prompt_snippet": prompt_snippet,
                "raw_excerpt": raw_excerpt,
                "fingerprint": fp,
                "pane_title": str(pane_title or ""),
                "tail": tail,
                "rules_error": rules_error,
            }
        break

    # Rule 6: Working / active spinner / "esc to interrupt"
    for ln in reversed(tail):
        if COMPLETED_VERB_RE.match(ln) or CODEX_HISTORY_RE.match(ln):
            continue
        if _match_any(comp["working_patterns"], ln) or WORKING_RE.search(ln) or SPINNER_RE.match(ln):
            return {
                "ok": True,
                "state": "working",
                "rule": "working_indicator",
                "match_layer": "rules",
                "choice_style": "",
                "choices": [],
                "keys": {"once": None, "session": None, "deny": None},
                "keys_source": {"once": None, "session": None, "deny": None},
                "tool_summary": ln[:80],
                "command": "",
                "question": "",
                "prompt_snippet": "",
                "raw_excerpt": [],
                "fingerprint": "",
                "pane_title": str(pane_title or ""),
                "tail": tail,
                "rules_error": rules_error,
            }

    if TITLE_BUSY_RE.search(str(pane_title or "").strip()):
        return {
            "ok": True,
            "state": "working",
            "rule": "title_spinner",
            "match_layer": "structural",
            "choice_style": "",
            "choices": [],
            "keys": {"once": None, "session": None, "deny": None},
            "keys_source": {"once": None, "session": None, "deny": None},
            "tool_summary": str(pane_title or "").strip()[:80],
            "command": "",
            "question": "",
            "prompt_snippet": "",
            "raw_excerpt": [],
            "fingerprint": "",
            "pane_title": str(pane_title or ""),
            "tail": tail,
            "rules_error": rules_error,
        }

    # Rule 7: Idle
    return {
        "ok": True,
        "state": "idle",
        "rule": "idle_prompt",
        "match_layer": "",
        "choice_style": "",
        "choices": [],
        "keys": {"once": None, "session": None, "deny": None},
        "keys_source": {"once": None, "session": None, "deny": None},
        "tool_summary": "",
        "command": "",
        "question": "",
        "prompt_snippet": "",
        "raw_excerpt": [],
        "fingerprint": "",
        "pane_title": str(pane_title or ""),
        "tail": tail,
        "rules_error": rules_error,
    }


probe_pane_state = classify_pane_text


def probe_session_pane(
    session_or_sid: dict | str,
    runner: Callable | None = None,
    *,
    rules_path: str | None = None,
) -> dict:
    """Resolve a session's `hubpool` pane, capture its bottom lines, and classify its state."""
    resolved = resolve_pool_pane(session_or_sid, runner=runner)
    if not resolved["ok"]:
        return {
            "ok": False,
            "state": "unknown",
            "rule": resolved.get("error") or "resolve_failed",
            "match_layer": "",
            "choice_style": "",
            "choices": [],
            "keys": {"once": None, "session": None, "deny": None},
            "keys_source": {"once": None, "session": None, "deny": None},
            "tool_summary": "",
            "command": "",
            "question": "",
            "prompt_snippet": "",
            "raw_excerpt": [],
            "fingerprint": "",
            "pane_id": resolved.get("pane_id") or "",
            "window": resolved.get("window") or "",
            "pane_title": "",
            "tail": [],
            "error": resolved.get("error") or "resolve_failed",
        }

    cap = capture_pane(resolved["target"], lines=CONTEXT_LINES, runner=runner)
    if not cap["ok"]:
        return {
            "ok": False,
            "state": "unknown",
            "rule": "capture_failed",
            "match_layer": "",
            "choice_style": "",
            "choices": [],
            "keys": {"once": None, "session": None, "deny": None},
            "keys_source": {"once": None, "session": None, "deny": None},
            "tool_summary": "",
            "command": "",
            "question": "",
            "prompt_snippet": "",
            "raw_excerpt": [],
            "fingerprint": "",
            "pane_id": resolved["pane_id"],
            "window": resolved["window"],
            "pane_title": resolved.get("pane_title") or "",
            "tail": [],
            "error": "capture_failed",
        }

    pane_title = resolved.get("pane_title") or cap.get("pane_title") or ""
    classified = classify_pane_text(cap["lines"], pane_title=pane_title, rules_path=rules_path)
    classified["pane_id"] = resolved["pane_id"]
    classified["window"] = resolved["window"]
    classified["target"] = resolved["target"]
    return classified
