"""Handoff bundle rendering (10-section Markdown + handoff.yaml on disk) and target CLI prompt/command builders."""
from __future__ import annotations

import datetime
import os
import shlex

from mixhub.handoff.resume import RESUME_STATE_ACTIONS
from mixhub.handoff.contract import SECTION_TITLES
from mixhub.handoff.text import _clean_verbatim_turn_text, _snippet, estimate_tokens, redact_secrets
from mixhub.i18n import t


def render_bundle_markdown(
    session: dict,
    to_tool: str,
    provider: str,
    mode: str,
    handoff_chain: list[str],
    sections: dict[str, list[str]],
    yaml_text: str = "",
) -> str:
    """
    Render the rich handoff bundle with:
      - `## 0 接续点 (Immediate Action)` (`resume_state` + explicit action directive)
      - `## 3 近期对话原文 (Recent Verbatim Turns)` (recent 2~5 turns verbatim, folded tools, earlier transcript pointer)
      - `## 4 关键文件与锚点` (`effective_root`, modified files, and Read/Grep inspected files with line ranges)
      - `## Handoff Contract (handoff.yaml)` and `## 1. Goal` .. `## 10. Evidence Pointers`
    Token budget: target 4,000-6,500 tokens (hard cap 10,000 tokens).
    """
    sid = session["id"]
    from_tool = session["tool"]
    title = redact_secrets(session.get("title") or f"{from_tool}-{sid[:8]}")
    effective_root = session.get("effective_root") or session.get("cwd") or ""
    cwd = effective_root or session.get("cwd") or ""
    resume_state = session.get("resume_state") or "completed_idle"
    chain_str = " -> ".join(handoff_chain)
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    tx_path = session.get("path") or "transcript.jsonl"
    tx_basename = os.path.basename(tx_path) if tx_path else "transcript.jsonl"
    serialized_turns: list[dict] = list(session.get("_handoff_verbatim_turns") or [])
    raw_turn_chars = sum(
        len(t.get("user_text") or "") + len(t.get("assistant_text") or "")
        for t in serialized_turns
    )
    is_compact_single = bool(session.get("_handoff_is_synthetic_benchmark")) or (
        len(serialized_turns) <= 1 and raw_turn_chars < 800
    )
    target_budget = 1450 if is_compact_single else 6500
    hard_cap_budget = 1450 if is_compact_single else 10000

    def _render_verbatim_turns(num_turns: int, user_max: int, asst_max: int) -> list[str]:
        if not serialized_turns:
            fallback = sections.get("Recent Verbatim Turns") or ["(None)"]
            return [redact_secrets(item) for item in fallback]
        selected = serialized_turns[-num_turns:] if num_turns > 0 else []
        total_turns = int(session.get("_handoff_total_turns") or len(serialized_turns))
        if selected and (selected[0]["turn_index"] > 1 or selected[0]["start_line"] > 1):
            early_end = max(1, int(selected[0]["start_line"]) - 1)
            pointer_str = (
                f"{tx_basename}#L1-L{early_end} ({tx_path}#L1-L{early_end}) — "
                f"use `rg -n \"<keyword>\" {tx_path}` to search earlier turns (1..{selected[0]['turn_index'] - 1} of {total_turns}) on demand"
            )
        else:
            pointer_str = str(session.get("_handoff_earlier_pointer") or f"{tx_basename}#L1-L1")
        out_items = [f"**Earlier Turns Pointer**: `{redact_secrets(pointer_str)}`"]
        for st in selected:
            u_txt = _clean_verbatim_turn_text(st.get("user_text") or "", user_max) or "(None)"
            tools_list = st.get("tools") or []
            tools_line = f"[Tools: {', '.join(tools_list)}]" if tools_list else ""
            if mode in ("escalate", "review") and st.get("assistant_text"):
                a_txt = f"*(omitted in {mode} mode to prevent anchoring)*"
            else:
                a_txt = _clean_verbatim_turn_text(st.get("assistant_text") or "", asst_max) or "*(pending — no assistant reply yet)*"
            block = (
                f"**Turn {st['turn_index']} ({tx_basename}#L{st['start_line']}-L{st['end_line']})**:\n"
                f"  - **User**: {u_txt}"
            )
            if tools_line:
                block += f"\n  - **{redact_secrets(tools_line)}**"
            block += f"\n  - **Assistant**: {a_txt}"
            out_items.append(block)
        return out_items

    def build_md(
        max_item_chars: int,
        include_yaml: bool = True,
        num_turns: int = 5,
        user_max: int = 1500,
        asst_max: int = 2000,
    ) -> str:
        lines = [
            f"# Handoff Bundle: {title}",
            f"- **Source Session**: `{from_tool}:{sid}`",
            f"- **Target Agent**: `{to_tool}`" + (f" (provider: `{provider}`)" if provider else ""),
            f"- **Handoff Mode**: `{mode}`",
            f"- **Resume State (`resume_state`)**: `{resume_state}`",
            f"- **Handoff Chain**: `{chain_str}`",
            f"- **Workspace**: `{cwd}`",
            f"- **Effective Root (`effective_root`)**: `{effective_root}`",
            f"- **Generated At**: {now_str}",
            "",
            t("handoff.section.immediate"),
        ]
        imm_items = sections.get("Immediate Action") or [
            f"**Resume State (`resume_state`)**: `{resume_state}`",
            f"**Action Directive**: {RESUME_STATE_ACTIONS.get(resume_state, '')}",
            f"**Effective Project Root (`effective_root`)**: `{effective_root}`",
        ]
        imm_cap = max_item_chars if is_compact_single else max(max_item_chars, 500)
        for item in imm_items:
            lines.append(f"- {_snippet(redact_secrets(item), imm_cap)}")
        lines.append("")

        lines.append(t("handoff.section.recent"))
        for vt_item in _render_verbatim_turns(num_turns, user_max, asst_max):
            lines.append(f"- { vt_item }")
        lines.append("")

        lines.append(t("handoff.section.files"))
        kf_items = sections.get("Key Files & Anchors") or [f"**Effective Root (`effective_root`)**: `{effective_root}`"]
        kf_cap = max_item_chars if is_compact_single else max(max_item_chars, 360)
        for item in kf_items:
            lines.append(f"- {_snippet(redact_secrets(item), kf_cap)}")
        lines.append("")

        if include_yaml and yaml_text:
            lines.extend(
                [
                    "## Handoff Contract (handoff.yaml)",
                    "```yaml",
                    yaml_text.rstrip(),
                    "```",
                    "",
                ]
            )
        for idx, sec_title in enumerate(SECTION_TITLES, 1):
            lines.append(f"## {idx}. {sec_title}")
            items = sections.get(sec_title) or ["(None)"]
            for item in items:
                lines.append(f"- {_snippet(redact_secrets(item), max_item_chars)}")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"

    if is_compact_single:
        for inc_yaml, limits in ((True, (220, 180, 150, 120)), (False, (200, 160, 120, 85, 55))):
            for limit in limits:
                md = build_md(limit, include_yaml=inc_yaml, num_turns=1, user_max=min(320, limit * 2), asst_max=min(360, limit * 2))
                if estimate_tokens(md) < target_budget:
                    return md
        return md

    # Normal sessions: target 4,000-6,500 tokens (hard cap 10,000 tokens), keeping 2..5 recent verbatim turns
    turn_candidates = (5, 4, 3, 2) if len(serialized_turns) >= 2 else (max(1, len(serialized_turns)),)
    char_profiles = (
        (1500, 2000, 320, True),
        (1300, 1700, 280, True),
        (1000, 1300, 240, True),
        (750, 950, 220, True),
        (500, 650, 190, False),
    )
    last_md = ""
    for u_max, a_max, sec_max, inc_yaml in char_profiles:
        for n_turns in turn_candidates:
            md = build_md(sec_max, include_yaml=inc_yaml, num_turns=n_turns, user_max=u_max, asst_max=a_max)
            last_md = md
            toks = estimate_tokens(md)
            if toks <= target_budget and toks <= hard_cap_budget:
                return md
    if estimate_tokens(last_md) > hard_cap_budget:
        last_md = build_md(140, include_yaml=False, num_turns=2, user_max=350, asst_max=450)
    return last_md


render_markdown_bundle = render_bundle_markdown


def build_handoff_prompt(bundle_path: str, yaml_path: str = "", mode: str = "delegate") -> str:
    """The `<1.5k tok` boot prompt handed to the target CLI so it continues from the handoff bundle."""
    contract_ref = f" (and contract {yaml_path})" if yaml_path else ""
    if mode == "escalate":
        return (
            f"[ESCALATE MODE - DE-ANCHORED] Read handoff contract at {bundle_path}{contract_ref}. "
            "Predecessor narrative was stripped; investigate verified facts and failed_attempts independently."
        )
    if mode == "review":
        return (
            f"[BLIND REVIEW MODE] Read handoff contract at {bundle_path}{contract_ref}. "
            "Independently audit the git diff and verified test outputs against the task requirements."
        )
    return (
        f"Read handoff bundle at {bundle_path}{contract_ref} "
        "and continue from section 0 (Immediate Action) and section 9 (Next Single Action)."
    )


def build_handoff_command(
    session: dict,
    to_tool: str,
    bundle_path: str,
    yaml_path: str = "",
    provider: str = "",
    mode: str = "delegate",
) -> str:
    """
    Build the target CLI command to continue from a handoff bundle.
    When provider='deepseek' or to_tool='deepseek', uses native Codex harness
    (`codex -c model_provider="deepseek"`) to avoid Claude 2.1.113 spinner freeze.
    """
    cwd = session.get("effective_root") or session.get("cwd") or os.path.expanduser("~")
    prompt = build_handoff_prompt(bundle_path, yaml_path, mode)

    target = (to_tool or "").lower()
    prov = (provider or "").lower()

    if target == "deepseek" or prov == "deepseek":
        cmd = (
            f"cd {shlex.quote(cwd)} && "
            f'codex --no-daemon -c model_provider="deepseek" -C {shlex.quote(cwd)} {shlex.quote(prompt)}'
        )
    elif target == "codex":
        cmd = f"cd {shlex.quote(cwd)} && codex --no-daemon -C {shlex.quote(cwd)} {shlex.quote(prompt)}"
    elif target == "claude":
        cmd = f"cd {shlex.quote(cwd)} && claude --remote-control -- {shlex.quote(prompt)}"
    elif target == "agy":
        cmd = f"cd {shlex.quote(cwd)} && agy -i {shlex.quote(prompt)}"
    else:
        raise ValueError(f"Unsupported handoff target: {to_tool}")
    return cmd
