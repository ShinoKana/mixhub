"""handoff.yaml contract: section titles, valid targets, YAML serialization and directional mode resolution."""
from __future__ import annotations


HANDOFF_MODES = ("auto", "escalate", "delegate", "review")

SECTION_TITLES = (
    "Goal",
    "Acceptance",
    "Pinned Decisions [IRREV]",
    "Rejected Approaches",
    "User Verbatim Constraints",
    "Git & File State",
    "Verification Snapshot",
    "Unresolved Questions",
    "Next Single Action",
    "Evidence Pointers",
)

VALID_TARGETS = ("claude", "agy", "codex", "deepseek")


def _yaml_scalar(val) -> str:
    """Format a scalar safely for clean stdlib YAML serialization."""
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float)):
        return str(val)
    s = str(val or "").replace("\r", " ").replace("\n", " ").strip()
    if not s:
        return '""'
    if any(ch in s for ch in (":", "#", "[", "]", "{", "}", ",", "&", "*", "?", "|", "-", "<", ">", "=", "!", "%", "@", "`", '"', "'")):
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return s


def dump_handoff_yaml(contract: dict) -> str:
    """Serialize a structured handoff contract dictionary into standard YAML."""
    lines: list[str] = []
    for k, v in contract.items():
        if isinstance(v, dict):
            lines.append(f"{k}:")
            for sub_k, sub_v in v.items():
                if isinstance(sub_v, list):
                    if not sub_v:
                        lines.append(f"  {sub_k}: []")
                    else:
                        lines.append(f"  {sub_k}:")
                        for item in sub_v:
                            lines.append(f"    - {_yaml_scalar(item)}")
                else:
                    lines.append(f"  {sub_k}: {_yaml_scalar(sub_v)}")
        elif isinstance(v, list):
            if not v:
                lines.append(f"{k}: []")
            else:
                lines.append(f"{k}:")
                for item in v:
                    lines.append(f"  - {_yaml_scalar(item)}")
        else:
            lines.append(f"{k}: {_yaml_scalar(v)}")
    return "\n".join(lines) + "\n"


def resolve_handoff_mode(
    requested_mode: str,
    from_tool: str,
    to_tool: str,
    provider: str = "",
    has_errors: bool = False,
    note: str = "",
) -> str:
    """
    Resolve directional handoff mode (`escalate` | `delegate` | `review`):
      - Scenario A (`escalate`): Weak/daily agent -> strong mastermind or stuck with errors.
        De-anchors flawed exploration and subjective reasoning; passes only task, git state,
        verified facts, and failed attempts.
      - Scenario B (`delegate`): Strong mastermind -> high-ROI execution workhorse (e.g. DeepSeek)
        or quota relay. Preserves architecture rationale, rejected approaches, interface
        constraints, and acceptance criteria.
      - Scenario C (`review`): Cross-vendor independent audit (e.g. Claude implementation -> Codex review).
        Strips developer self-justification to prevent correlated blindness; passes only task,
        git diff, and objective test outputs.
    """
    mode = (requested_mode or "auto").strip().lower()
    if mode in ("escalate", "delegate", "review"):
        return mode
    note_low = (note or "").lower()
    if any(k in note_low for k in ("review", "audit", "盲审", "审查", "检查")):
        return "review"
    if any(k in note_low for k in ("escalate", "stuck", "卡住", "攻坚", "求救")):
        return "escalate"
    if (to_tool or "").lower() == "deepseek" or (provider or "").lower() == "deepseek":
        return "delegate"
    if has_errors or (from_tool or "").lower() == "deepseek":
        return "escalate"
    return "delegate"
