"""End-to-end cross-agent handoff: extract, bundle, launch and notify (`create_handoff`)."""
from __future__ import annotations

import json
import os
import re
import uuid

from mixhub.handoff.contract import HANDOFF_MODES, VALID_TARGETS, dump_handoff_yaml
from mixhub.handoff.text import estimate_tokens
from mixhub.handoff.extract import extract_handoff_artifacts
from mixhub.handoff.bundle import (
    build_handoff_command,
    build_handoff_prompt,
    render_bundle_markdown,
)


def create_handoff(
    hub,
    session_id_or_prefix: str,
    to_tool: str,
    provider: str = "",
    from_tool: str = "",
    target_session_id: str = "",
    mode: str = "auto",
    note: str = "",
    dry_run: bool = False,
) -> dict:
    """
    Full cross-agent handoff workflow:
      1. Resolve source session (`claude` / `agy` / `codex`).
      2. Extract 3-mode directional `handoff.yaml` contract (`verified`, `unverified`, `failed_attempts`)
         and rich Handoff Bundle (`resume_state`, `effective_root`, recent verbatim turns, key files).
      3. Persist `<id>.md` and `<id>.yaml` to `<hub.root>/data/handoffs/`.
      4. Update `handoffChain` in `hub_state.json` so `server.py`, `tui.py` (`●●`),
         and `mixhub/web/static/index.html` (stacked SVG badges) reflect the relay chain.
    """
    target = (to_tool or "").strip().lower()
    prov = (provider or "").strip().lower()
    req_mode = (mode or "auto").strip().lower()
    if target not in VALID_TARGETS:
        raise ValueError(f"to_tool must be one of {VALID_TARGETS}")
    if req_mode not in HANDOFF_MODES:
        raise ValueError(f"mode must be one of {HANDOFF_MODES}")
    if target == "deepseek" and not prov:
        prov = "deepseek"

    sessions = hub.sessions if getattr(hub, "sessions", None) else hub.discover(False)
    matched = []
    for s in sessions.values():
        if from_tool and s["tool"] != from_tool:
            continue
        if s["id"] == session_id_or_prefix or s["id"].startswith(session_id_or_prefix):
            matched.append(s)
    if not matched:
        sessions = hub.discover(True)
        for s in sessions.values():
            if from_tool and s["tool"] != from_tool:
                continue
            if s["id"] == session_id_or_prefix or s["id"].startswith(session_id_or_prefix):
                matched.append(s)
    if not matched:
        raise KeyError(f"Session not found: {session_id_or_prefix}")
    matched.sort(key=lambda x: (-int(bool(x.get("isLive"))), -float(x.get("updatedAt") or 0)))
    session = dict(matched[0])
    sid = session["id"]
    src_tool = session["tool"]

    existing_chain = list(session.get("handoffChain") or session.get("relayTools") or [])
    if not existing_chain:
        existing_chain = [src_tool]
    if existing_chain[-1] != src_tool and src_tool not in existing_chain:
        existing_chain.append(src_tool)

    chain_target = "codex" if (target == "deepseek" and prov == "deepseek") else target
    handoff_chain = list(existing_chain)
    if handoff_chain[-1] != chain_target:
        handoff_chain.append(chain_target)

    sections, contract, resolved_mode = extract_handoff_artifacts(
        hub,
        session,
        target,
        provider=prov,
        mode=req_mode,
        note=note,
    )
    effective_root = str(contract.get("effective_root") or session.get("effective_root") or session.get("cwd") or "")
    resume_state = str(contract.get("resume_state") or session.get("resume_state") or "completed_idle")

    task_id = str(session.get("handoffTaskId") or session.get("taskId") or sid)
    resolved_target_sid = str(target_session_id or "").strip()
    if resolved_target_sid:
        for s_cand in sessions.values():
            if s_cand["tool"] == chain_target and (
                s_cand["id"] == resolved_target_sid or s_cand["id"].startswith(resolved_target_sid)
            ):
                resolved_target_sid = s_cand["id"]
                break
    else:
        same_cwd_targets = [
            s_cand
            for s_cand in sessions.values()
            if s_cand["tool"] == chain_target
            and s_cand.get("cwd")
            and s_cand.get("cwd") in (session.get("cwd"), effective_root)
            and s_cand["id"] != sid
        ]
        if same_cwd_targets:
            same_cwd_targets.sort(key=lambda x: (-int(bool(x.get("isLive"))), -float(x.get("updatedAt") or 0)))
            resolved_target_sid = same_cwd_targets[0]["id"]
    to_session_key = f"{chain_target}:{resolved_target_sid or sid}"

    yaml_text = dump_handoff_yaml(contract)
    markdown = render_bundle_markdown(
        session=session,
        to_tool=target,
        provider=prov,
        mode=resolved_mode,
        handoff_chain=handoff_chain,
        sections=sections,
        yaml_text=yaml_text,
    )
    tok_est = estimate_tokens(markdown)
    before_t = max(tok_est + 1200, max(1, int(session.get("messageCount") or 2) // 2) * 2100)
    saved_t = max(0, before_t - tok_est)
    saved_u = round((saved_t / 1_000_000.0) * 29.0, 2)
    dyn_savings = {
        "before_tokens": before_t,
        "after_tokens": tok_est,
        "saved_tokens": saved_t,
        "saved_usd": saved_u,
        "ephemeral_toast": (
            f"✦ Handoff {before_t / 1000.0:.1f}k → {tok_est / 1000.0:.1f}k ctx "
            f"(-{saved_t / 1000.0:.1f}k tokens · ~${saved_u:.2f} saved) "
            f"· How was this handoff? [1] Great  [2] Fine  [3] Dismiss"
        ),
    }

    handoff_dir = os.path.join(hub.root, "data", "handoffs")
    bundle_path = os.path.join(handoff_dir, f"{sid}.md")
    yaml_path = os.path.join(handoff_dir, f"{sid}.yaml")
    launch_cmd = build_handoff_command(
        session,
        target,
        bundle_path,
        yaml_path=yaml_path,
        provider=prov,
        mode=resolved_mode,
    )
    bundle_id = f"hb-{sid[:12]}-{uuid.uuid4().hex[:6]}"
    raw_src_title = str(session.get("title") or f"{src_tool}-{sid[:8]}").strip()
    clean_src_title = re.sub(r"^(?:↳\s*)+", "", raw_src_title).strip() or f"{src_tool}-{sid[:8]}"
    relay_title = f"↳ {clean_src_title}"
    session_names_path = os.path.join(hub.root, "data", "session_names.json")

    if not dry_run:
        os.makedirs(handoff_dir, exist_ok=True)
        for out_path, content in ((bundle_path, markdown), (yaml_path, yaml_text)):
            tmp_path = f"{out_path}.tmp.{os.getpid()}"
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, out_path)

        # Persist inherited relay title (`↳ <source_title>`) into MixHub's non-invasive
        # `session_names.json` overlay without polluting native CLI transcript files.
        try:
            existing_names: dict[str, str] = {}
            if os.path.isfile(session_names_path):
                with open(session_names_path, encoding="utf-8") as sn_f:
                    loaded_sn = json.load(sn_f)
                if isinstance(loaded_sn, dict):
                    existing_names = loaded_sn
            existing_names[f"relay:{bundle_id}"] = relay_title
            existing_names[f"relay_target:{task_id}"] = relay_title
            if resolved_target_sid and resolved_target_sid != sid:
                existing_names[to_session_key] = relay_title
                existing_names[resolved_target_sid] = relay_title
            elif to_session_key != f"{src_tool}:{sid}":
                existing_names[to_session_key] = relay_title
            sn_tmp = f"{session_names_path}.tmp.{os.getpid()}"
            with open(sn_tmp, "w", encoding="utf-8") as sn_f:
                json.dump(existing_names, sn_f, ensure_ascii=False, indent=2)
                sn_f.write("\n")
                sn_f.flush()
                os.fsync(sn_f.fileno())
            os.replace(sn_tmp, session_names_path)
        except OSError:
            pass

        state_updates = {
            "taskId": task_id,
            "handoffTaskId": task_id,
            "handoffChain": handoff_chain,
            "relayTools": handoff_chain,
            "handoffFrom": handoff_chain[-2] if len(handoff_chain) >= 2 else src_tool,
            "handoffTo": target,
            "handoffProvider": prov,
            "handoffMode": resolved_mode,
            "handoffBundlePath": bundle_path,
            "handoffYamlPath": yaml_path,
            "effectiveRoot": effective_root,
            "resumeState": resume_state,
        }
        if effective_root:
            state_updates["cwd"] = effective_root
        # Retain source session 100% intact in Recent list for side-by-side comparison
        hub.save(f"{src_tool}:{sid}", state_updates)
        if resolved_target_sid and resolved_target_sid != sid:
            target_updates = dict(
                state_updates,
                relayTitle=relay_title,
                relaySourceId=sid,
                relaySourceTitle=clean_src_title,
            )
            hub.save(f"{chain_target}:{resolved_target_sid}", target_updates)
        try:
            if hasattr(hub, "working_set") and hasattr(hub, "_save_working_set"):
                wset = hub.working_set()
                if resolved_target_sid:
                    wset.add(f"{chain_target}:{resolved_target_sid}")
                    hub._save_working_set(wset)
        except Exception:
            pass
        try:
            from mixhub.rules.trust import ensure_workspace_trusted

            ws_cwd = effective_root or session.get("cwd") or getattr(hub, "home", "")
            if ws_cwd:
                ensure_workspace_trusted(ws_cwd, home_dir=getattr(hub, "home", None), dry_run=False)
        except Exception:
            pass
        if f"{src_tool}:{sid}" not in getattr(hub, "sessions", {}):
            hub.scanned = 0

    return {
        "ok": True,
        "dry_run": dry_run,
        "bundle_id": bundle_id,
        "id": sid,
        "task_id": task_id,
        "from_tool": src_tool,
        "to_tool": target,
        "to_session_key": to_session_key,
        "relay_title": relay_title,
        "source_title": clean_src_title,
        "source_retained": True,
        "session_names_path": session_names_path,
        "provider": prov,
        "mode": resolved_mode,
        "status": contract["status"],
        "resume_state": resume_state,
        "immediate_action": contract.get("immediate_action", ""),
        "cwd": effective_root,
        "effective_root": effective_root,
        "handoffChain": handoff_chain,
        "bundle_path": bundle_path,
        "yaml_path": yaml_path,
        "token_estimate": tok_est,
        "before_tokens": dyn_savings["before_tokens"],
        "after_tokens": dyn_savings["after_tokens"],
        "saved_tokens": dyn_savings["saved_tokens"],
        "saved_usd": dyn_savings["saved_usd"],
        "ephemeral_toast": dyn_savings["ephemeral_toast"],
        "contract": contract,
        "yaml": yaml_text,
        "sections": sections,
        "markdown": markdown,
        "command": launch_cmd,
        "prompt": build_handoff_prompt(bundle_path, yaml_path, resolved_mode),
    }
