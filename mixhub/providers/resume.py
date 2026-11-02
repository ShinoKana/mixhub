"""Guarded resume: detect foreign thinking signatures in a transcript and fork without them before resuming."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any, Iterable
import uuid

from mixhub.providers.jsonl import _d, _iter_jsonl
from mixhub.providers.launch import _claude_home, _codex_home, build_launch, LaunchSpec, ResumeSignatureError
from mixhub.providers.presets import brain_of_model, get_harness_preset, target_brain


def _is_signed_block(b: Any) -> bool:
    return isinstance(b, dict) and (
        (b.get("type") == "thinking" and bool(b.get("signature")))
        or b.get("type") == "redacted_thinking"
    )


@dataclass
class ResumeCheck:
    ok: bool
    target: str
    brains: set[str]
    signed: dict[str, int]
    reason: str
    cross_brain: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "allow_direct_resume": self.ok,
            "blocked": not self.ok,
            "target": self.target,
            "brains": sorted(self.brains),
            "signed": dict(self.signed),
            "has_thinking_signature": bool(self.signed),
            "cross_brain": self.cross_brain,
            "reason": self.reason,
        }


def check_resume(
    session_jsonl: str | Path | Iterable,
    preset_id: str,
    model: str | None = None,
    *,
    block_all_cross_brain: bool = True,
) -> ResumeCheck:
    """
    Cross-Brain Resume Signature Guard:
      1. Signed thinking blocks (Claude `thinking.signature` / `redacted_thinking`,
         Codex `reasoning.encrypted_content`) are only accepted by the brain that minted them;
         replaying them to another brain causes an immediate HTTP 400 signature error.
      2. Even without signed thinking blocks, switching brains in-place on an existing session
         is blocked when `block_all_cross_brain=True` so MixHub can fork or relay cleanly.
    """
    tgt = target_brain(preset_id, model)
    brains: set[str] = set()
    signed: dict[str, int] = {}
    cur: str | None = None
    for o in _iter_jsonl(session_jsonl):
        msg, pl = _d(o.get("message")), _d(o.get("payload"))
        m = msg.get("model") or pl.get("model") or o.get("model")
        if isinstance(m, str) and m and not m.startswith("<"):
            cur = brain_of_model(m) or f"?{m}"
            brains.add(cur)
        content = msg.get("content") if isinstance(msg.get("content"), list) else o.get("content")
        n = sum(map(_is_signed_block, content)) if isinstance(content, list) else 0
        n += int(pl.get("type") == "reasoning" and bool(pl.get("encrypted_content")))
        if n:
            signed[cur or "anthropic"] = signed.get(cur or "anthropic", 0) + n

    foreign_signed = {b: n for b, n in signed.items() if b != tgt}
    foreign_brains = {b for b in brains if b != tgt}
    is_cross_brain = bool(foreign_brains or foreign_signed)

    if foreign_signed:
        return ResumeCheck(
            False,
            tgt,
            brains,
            signed,
            (
                f"thinking_signature_conflict: {sum(foreign_signed.values())} signed thinking block(s) "
                f"from {sorted(foreign_signed)} cannot be replayed to '{tgt}' (HTTP 400 invalid signature); "
                f"fork without thinking or use cross-agent relay"
            ),
            cross_brain=True,
        )
    if block_all_cross_brain and foreign_brains:
        return ResumeCheck(
            False,
            tgt,
            brains,
            signed,
            (
                f"cross_brain_mismatch: session history contains brain(s) {sorted(foreign_brains)} "
                f"which differ from target '{tgt}'; fork session or relay via handoff.yaml"
            ),
            cross_brain=True,
        )
    return ResumeCheck(True, tgt, brains, signed, "ok", cross_brain=is_cross_brain)


def fork_without_thinking(session_jsonl: str | Path) -> tuple[str, Path]:
    """Write a NEW session file next to `session_jsonl` with signed thinking/reasoning blocks stripped; source untouched."""
    src, new_sid = Path(os.path.expanduser(str(session_jsonl))), str(uuid.uuid4())
    old_sid, out, reparent = src.stem, [], {}
    for o in _iter_jsonl(src):
        msg, pl = _d(o.get("message")), _d(o.get("payload"))
        if isinstance(msg.get("content"), list):
            kept = [
                b
                for b in msg["content"]
                if not (isinstance(b, dict) and b.get("type") in ("thinking", "redacted_thinking"))
            ]
            if not kept:
                if o.get("uuid"):
                    reparent[o["uuid"]] = o.get("parentUuid")
                continue
            msg["content"] = kept
        if pl.get("type") == "reasoning":
            continue
        if o.get("type") == "session_meta" and pl.get("id"):
            old_sid, pl["id"] = str(pl["id"]), new_sid
        if "sessionId" in o:
            o["sessionId"] = new_sid
        if "parentUuid" in o:
            par = o["parentUuid"]
            while par in reparent:
                par = reparent[par]
            o["parentUuid"] = par
        out.append(json.dumps(o, ensure_ascii=False))
    name = src.name.replace(old_sid, new_sid) if old_sid in src.name else f"{new_sid}.jsonl"
    dst = src.with_name(name)
    dst.write_text("\n".join(out) + "\n", encoding="utf-8")
    return new_sid, dst


def find_session_file(preset_id: str, sid: str, home_dir: str | None = None) -> Path | None:
    p = get_harness_preset(preset_id)
    harness = p["harness"]
    if harness == "claude":
        hits = (_claude_home(home_dir) / "projects").glob(f"*/{sid}.jsonl")
    elif harness == "codex":
        hits = (_codex_home(home_dir) / "sessions").rglob(f"rollout-*{sid}.jsonl")
    else:
        return None
    return next(iter(hits), None)


def guarded_resume(
    preset_id: str,
    sid: str,
    *,
    session_path: str | Path | None = None,
    auto_fork: bool = True,
    home_dir: str | None = None,
    **kw: Any,
) -> LaunchSpec:
    """
    Guard `--resume` against cross-brain signature conflicts (HTTP 400):
      - If `auto_fork=False` and `check_resume` detects foreign signed thinking blocks or cross-brain switch,
        raises `ResumeSignatureError`.
      - If `auto_fork=True`, forks a clean transcript without signed thinking blocks and resumes the new session ID.
    """
    path = Path(session_path) if session_path else find_session_file(preset_id, sid, home_dir=home_dir)
    if path is not None and path.is_file():
        chk = check_resume(path, preset_id, kw.get("model"))
        if not chk.ok:
            if not auto_fork:
                raise ResumeSignatureError(chk.reason)
            sid, _ = fork_without_thinking(path)
    return build_launch(preset_id, resume=sid, home_dir=home_dir, **kw)
