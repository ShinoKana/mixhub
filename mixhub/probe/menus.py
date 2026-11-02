#!/usr/bin/env python3
"""`mixhub.probe.menus`: numbered-menu detection, approval header / command extraction, prompt fingerprints and agy hotkey approval boxes."""
from __future__ import annotations

import hashlib
import re

from mixhub.probe.patterns import (
    AGY_HOTKEY_PAIR_RE,
    AGY_NEEDS_APPROVAL_RE,
    BARE_COMPOSER_PROMPT_RE,
    CLAUDE_HEAD_RE,
    CMD_DOLLAR_RE,
    COMPLETED_VERB_RE,
    GEMINI_EXEC_RE,
    IDLE_STATUS_BAR_RE,
    INLINE_HOTKEY_RE,
    MARKDOWN_LABEL_RE,
    OPTION_RE,
    PROMPT_RE,
    RULE_RE,
    SPINNER_RE,
    STRUCTURAL_FOOTER_KEYBAR_RE,
    TOOL_OUTPUT_RE,
    TRANSCRIPT_RE,
    WORKING_RE,
)
from mixhub.probe.rules import _BUILTIN_COMPILED_RULES, _match_any
from mixhub.probe.text import _build_raw_excerpt, redact_secrets


def _find_numbered_menu(
    tail: list[str],
    *,
    raw_tail_with_rules: list[str] | None = None,
    rules_compiled: dict | None = None,
) -> list[tuple[int, int, bool, str]]:
    """
    Find the last contiguous run of numbered menu options `(tail_idx, number, has_cursor, label)`
    in `tail` satisfying Layer 1 Structural Geometry Heuristics & False-Positive Guards:
      - Numbers start at 1 and increment sequentially (`1..N` with `2 <= N <= 9`) using consistent `.` or `)` delimiter.
      - Options do NOT cross a horizontal rule boundary (`───`) into a composer input box (fixes Case A).
      - No active spinner or `esc to interrupt` appears below option 1 (fixes Case B).
      - No tool-output line (`⎿`) inside the run (fixes Case E).
      - Un-cursored lists inside a transcript speaker block (`⏺` / `•` / `✦`) are rejected (fixes Case F).
      - At least one Structural Evidence signal is present:
          * E1: Exactly 1 option has a focus cursor (`❯`, `›`, `>`, `▶`, `●`, `→`), OR
          * E2: Footer key-hint bar within 2 lines below last option (`esc`/`enter`/`↑`/`↓`), OR
          * E3: >= 2 options have inline hotkeys `(y)` / `(a)` / `(esc)`, OR
          * Layer 2 rules match the non-transcript dialog header.
    """
    # Check raw_tail_with_rules for horizontal rule boundaries so options 1..3 above a composer rule
    # are never merged with a user draft `❯ 4. ...` below the rule (Case A).
    seq_lines = raw_tail_with_rules if raw_tail_with_rules is not None else tail
    block_id_by_tail_idx: dict[int, int] = {}
    current_block = 0
    t_cursor = 0
    for rln in seq_lines:
        if RULE_RE.match(rln):
            current_block += 1
            continue
        if t_cursor < len(tail) and tail[t_cursor] == rln:
            block_id_by_tail_idx[t_cursor] = current_block
            t_cursor += 1

    option_hits: list[tuple[int, int, bool, str, str, int]] = []
    for idx, ln in enumerate(tail):
        if TOOL_OUTPUT_RE.match(ln):
            continue
        m = OPTION_RE.match(ln)
        if m:
            num = int(m.group("num"))
            cur_str = (m.group("cur") or "").strip()
            has_cur = bool(cur_str)
            delim = m.group("delim")
            label = m.group("label").strip()
            blk = block_id_by_tail_idx.get(idx, 0)
            option_hits.append((idx, num, has_cur, label, delim, blk))

    if len(option_hits) < 2:
        return []

    runs: list[list[tuple[int, int, bool, str, str, int]]] = []
    for pos, hit in enumerate(option_hits):
        if hit[1] != 1:
            continue
        cur_run = [hit]
        expected = 2
        last_line_idx = hit[0]
        run_delim = hit[4]
        run_blk = hit[5]
        for nxt in option_hits[pos + 1:]:
            if nxt[5] != run_blk:
                break
            if nxt[0] - last_line_idx > 4:
                break
            if nxt[4] != run_delim:
                break
            if nxt[1] == expected:
                cur_run.append(nxt)
                expected += 1
                last_line_idx = nxt[0]
            elif nxt[1] == 1:
                break
        if len(cur_run) >= 2:
            runs.append(cur_run)

    if not runs:
        return []

    candidate_ext = runs[-1]
    first_opt_line_idx = candidate_ext[0][0]
    last_opt_line_idx = candidate_ext[-1][0]

    # Guard 1 & 2: Reject if an input composer prompt or active spinner appears below the menu
    for below_ln in tail[last_opt_line_idx + 1:]:
        if PROMPT_RE.match(below_ln):
            return []
        if not COMPLETED_VERB_RE.match(below_ln) and (
            re.search(r"\b(?:esc|ctrl\+c)\s+to\s+interrupt\b", below_ln, re.I)
            or SPINNER_RE.match(below_ln)
        ):
            return []
        if IDLE_STATUS_BAR_RE.search(below_ln) and sum(1 for item in candidate_ext if item[2]) == 0:
            return []

    # Guard 3: Reject tool-output lines inside the menu span
    for span_ln in tail[first_opt_line_idx : last_opt_line_idx + 1]:
        if TOOL_OUTPUT_RE.match(span_ln):
            return []

    # Guard 4: Reject markdown prose lists (>= 50% labels have **, `, or ]()
    md_count = sum(1 for item in candidate_ext if MARKDOWN_LABEL_RE.search(item[3]))
    if md_count * 2 >= len(candidate_ext):
        return []

    cursor_count = sum(1 for item in candidate_ext if item[2])
    if cursor_count > 1:
        # More than 1 cursor glyph (e.g. `● item 1`, `● item 2`) is a bulleted list, not a single-focus menu
        return []

    candidate = [(it[0], it[1], it[2], it[3]) for it in candidate_ext]

    # Structural Evidence E1: Single focus cursor on one option
    if cursor_count == 1:
        return candidate

    # Guard against Case F: Un-cursored list inside an assistant transcript block (`⏺`, `•`, `✦`)
    menu_blk = candidate_ext[0][5]
    for above_idx in range(first_opt_line_idx - 1, -1, -1):
        if block_id_by_tail_idx.get(above_idx, menu_blk) != menu_blk:
            break
        if TRANSCRIPT_RE.match(tail[above_idx]) or TOOL_OUTPUT_RE.match(tail[above_idx]):
            return []

    # Structural Evidence E3: >= 2 options have inline hotkeys like `(y)` / `(a)` / `(esc)`
    hotkey_count = sum(1 for item in candidate if INLINE_HOTKEY_RE.search(item[3]))
    if hotkey_count >= 2:
        return candidate

    # Structural Evidence E2: Footer key-hint bar within 2 lines below last option
    footer_lines = tail[last_opt_line_idx + 1 : last_opt_line_idx + 3]
    for fln in footer_lines:
        if len(fln) <= 100 and not IDLE_STATUS_BAR_RE.search(fln) and STRUCTURAL_FOOTER_KEYBAR_RE.search(fln):
            return candidate

    # Layer 2 phrase fallback for un-cursored dialog boxes (only when NOT in a `⏺` transcript block)
    joined_tail = "\n".join(tail)
    comp = (rules_compiled or _BUILTIN_COMPILED_RULES["compiled"])
    if (
        _match_any(comp["permission_patterns"], joined_tail)
        or _match_any(comp["trust_patterns"], joined_tail)
        or _match_any(comp["question_patterns"], joined_tail)
    ):
        return candidate
    return []


def _extract_header_and_command(
    context_lines: list[str],
    first_opt_line_text: str,
    rules_compiled: dict | None = None,
) -> tuple[list[str], str, str, str]:
    """
    Locate lines above the menu in `context_lines` and extract:
      `(header_block, tool_summary, command, question)`
    """
    comp = rules_compiled or _BUILTIN_COMPILED_RULES["compiled"]
    first_idx = len(context_lines)
    for i in range(len(context_lines) - 1, -1, -1):
        if context_lines[i] == first_opt_line_text:
            first_idx = i
            break

    header_block: list[str] = []
    for i in range(first_idx - 1, max(-1, first_idx - 25), -1):
        ln = context_lines[i]
        if not ln:
            continue
        if RULE_RE.match(ln) or TRANSCRIPT_RE.match(ln):
            break
        header_block.append(ln)
    header_block.reverse()

    question = ""
    for ln in reversed(header_block):
        if (
            _match_any(comp["permission_patterns"], ln)
            or _match_any(comp["trust_patterns"], ln)
            or ln.endswith("?")
            or ln.endswith("：")
            or ln.endswith(":")
        ):
            question = ln
            break
    if not question and header_block:
        question = header_block[-1]

    command = ""
    tool_summary = ""

    for idx, ln in enumerate(header_block):
        m_dollar = CMD_DOLLAR_RE.match(ln)
        if m_dollar and not command:
            command = m_dollar.group("cmd").strip()
        m_gem = GEMINI_EXEC_RE.search(ln)
        if m_gem and not command:
            command = m_gem.group("cmd").strip()
        if CLAUDE_HEAD_RE.match(ln) and not tool_summary:
            tool_summary = ln[:80]
            cmd_parts: list[str] = []
            for sub in header_block[idx + 1:]:
                if (
                    _match_any(comp["permission_patterns"], sub)
                    or _match_any(comp["trust_patterns"], sub)
                    or _match_any(comp["question_patterns"], sub)
                ):
                    break
                cmd_parts.append(sub)
            if cmd_parts and not command:
                command = " ".join(cmd_parts).strip()[:200]

    if not command:
        for ln in header_block:
            if (
                ln != question
                and not CLAUDE_HEAD_RE.match(ln)
                and not _match_any(comp["permission_patterns"], ln)
                and not _match_any(comp["trust_patterns"], ln)
            ):
                command = ln[:200]
                break

    if not tool_summary:
        tool_summary = (question or command)[:80]

    return (
        header_block,
        redact_secrets(tool_summary[:80]),
        redact_secrets(command[:200]),
        redact_secrets(question[:200]),
    )


def _compute_fingerprint(state: str, tool_summary: str, command: str, labels: list[str]) -> str:
    if not state.startswith("awaiting_"):
        return ""
    raw = "\x1f".join([state, tool_summary.strip(), command.strip(), *[lbl.strip() for lbl in labels]])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _match_hotkey_approval_box(
    tail: list[str],
    non_empty: list[str],
    pane_title: str,
    comp: dict,
    rules_error: str,
) -> dict | None:
    """
    Detect Antigravity (`agy`) hotkey-driven tool approval boxes in the bottom 15 lines:
      - Header line matching `(<agent>) needs approval for (<tool>)`
      - Hotkey hint line within the last ~6 lines containing both `approve` and `(ctrl|alt)+<key> <action>`
      - No subsequent conversation output, tool output, or active spinner below the hotkey hint line
        (only an optional bare docked composer prompt `>` / `❯` or status bar is allowed below it).
    """
    for hdr_idx in range(len(tail) - 1, -1, -1):
        m_hdr = AGY_NEEDS_APPROVAL_RE.match(tail[hdr_idx])
        if not m_hdr:
            continue
        agent = m_hdr.group("agent").strip()
        tool_name = m_hdr.group("tool").strip()
        hk_idx = -1
        hk_matches: list[re.Match] = []
        for cand_idx in range(hdr_idx + 1, min(len(tail), hdr_idx + 6)):
            cand_ln = tail[cand_idx]
            if "approve" not in cand_ln.lower():
                continue
            pairs = list(AGY_HOTKEY_PAIR_RE.finditer(cand_ln))
            if pairs:
                hk_idx = cand_idx
                hk_matches = pairs
                break
        if hk_idx < 0 or not hk_matches:
            continue

        # Check lines below the hotkey bar:
        # 1) Never match if an active spinner / `esc to interrupt` appears below.
        # 2) When a docked bare composer prompt `>` / `❯` is present below, the hotkey bar must sit
        #    immediately above `>` (`rel_prompt_idx == 0`), with only statusline rows below `>`.
        # 3) When no `>` is present, only idle status-bar rows may appear below the hotkey bar.
        below_lines = tail[hk_idx + 1 :]
        if any(
            not COMPLETED_VERB_RE.match(ln) and (WORKING_RE.search(ln) or SPINNER_RE.match(ln))
            for ln in below_lines
        ):
            continue
        prompt_positions = [i for i, ln in enumerate(below_lines) if BARE_COMPOSER_PROMPT_RE.match(ln)]
        if prompt_positions:
            if prompt_positions[0] != 0:
                continue
        else:
            if any(not IDLE_STATUS_BAR_RE.search(ln) for ln in below_lines):
                continue

        span_lines = tail[hdr_idx : hk_idx + 1]
        if _match_any(comp["false_positive_patterns"], "\n".join(span_lines)):
            continue

        cmd_lines: list[str] = []
        for mid_ln in tail[hdr_idx + 1 : hk_idx]:
            cleaned_mid = re.sub(r"^[●•✦⏺]\s*", "", mid_ln).strip()
            if cleaned_mid:
                cmd_lines.append(cleaned_mid)
        command = " ".join(cmd_lines).strip() or tool_name
        question = tail[hdr_idx].strip()
        tool_summary = f"{agent} needs approval for {tool_name}"

        choices: list[dict] = []
        keys: dict[str, str | None] = {"once": None, "session": None, "deny": None}
        keys_source: dict[str, str | None] = {"once": None, "session": None, "deny": None}
        for idx, m_hk in enumerate(hk_matches):
            mod = m_hk.group("mod").lower()
            k_ch = m_hk.group("key").lower()
            act_id = m_hk.group("action").lower()
            tmux_key = f"C-{k_ch}" if mod == "ctrl" else f"M-{k_ch}"
            if act_id in ("approve", "allow", "yes", "proceed", "run", "ok"):
                decision = "once"
                if keys["once"] is None:
                    keys["once"] = tmux_key
                    keys_source["once"] = "semantic"
            elif act_id in ("deny", "reject", "cancel", "no", "abort"):
                decision = "deny"
                if keys["deny"] is None:
                    keys["deny"] = tmux_key
                    keys_source["deny"] = "semantic"
            else:
                decision = act_id
                keys[act_id] = tmux_key
                keys_source[act_id] = "semantic"
            choices.append(
                {
                    "id": act_id,
                    "key": tmux_key,
                    "label": f"{mod}+{k_ch} {act_id}",
                    "decision": decision,
                    "selected": idx == 0,
                }
            )

        raw_excerpt = _build_raw_excerpt(non_empty, tail, menu=None, question=question)
        prompt_snippet = "\n".join(raw_excerpt)
        fp = _compute_fingerprint(
            "awaiting_permission",
            tool_summary,
            command,
            [f"{c['id']}:{c['key']}" for c in choices],
        )
        return {
            "ok": True,
            "state": "awaiting_permission",
            "rule": "agy_hotkey_approval",
            "match_layer": "rules",
            "choice_style": "hotkey",
            "agent": agent,
            "tool": tool_name,
            "choices": choices,
            "keys": keys,
            "keys_source": keys_source,
            "tool_summary": redact_secrets(tool_summary[:80]),
            "command": redact_secrets(command[:200]),
            "question": redact_secrets(question[:200]),
            "prompt_snippet": prompt_snippet,
            "raw_excerpt": raw_excerpt,
            "fingerprint": fp,
            "pane_title": str(pane_title or ""),
            "tail": tail,
            "rules_error": rules_error,
        }
    return None
