#!/usr/bin/env python3
"""`mixhub.rules.sync`: the CLAUDE.md read-only guard, `sync_rules` (AGENTS.md -> .gemini/GEMINI.md, CLAUDE.md preview) and `hub sync-rules`."""
from __future__ import annotations

import argparse
import difflib
import json
import os
import sys

from mixhub.core import paths
from mixhub.rules.blocks import (
    build_managed_block,
    extract_hub_policy,
    inspect_managed_block,
    merge_managed_block,
    sha256_text,
    strip_managed_wrappers,
)
from mixhub.rules.trust import ensure_workspace_trusted


CLAUDE_MD_READONLY_MSG = "CLAUDE.md is a read-only source of truth and must never be modified by rules_sync"
CLAUDE_MD_PROTECTION_NOTICE = "read-only source of truth; write skipped"
_TARGET_ALIASES = {
    "claude": "claude",
    "claude.md": "claude",
    "gemini": "gemini",
    "gemini.md": "gemini",
    ".gemini/gemini.md": "gemini",
    "agents": "agents",
    "agents.md": "agents",
}


def _is_claude_md_path(path: str) -> bool:
    if not path:
        return False
    abs_p = os.path.abspath(os.path.expanduser(path))
    real_p = os.path.realpath(abs_p)
    # APFS is case-insensitive by default: Claude.md == CLAUDE.md
    if any(os.path.basename(c).casefold() == "claude.md" for c in (abs_p, real_p)):
        return True
    if not os.path.exists(abs_p):
        return False
    # hardlink / symlink aliasing onto a CLAUDE.md
    for cand in (
        os.path.join(os.path.dirname(abs_p), "CLAUDE.md"),
        os.path.join(os.path.dirname(real_p), "CLAUDE.md"),
        os.path.expanduser("~/.claude/CLAUDE.md"),
    ):
        try:
            if os.path.exists(cand) and os.path.samefile(abs_p, cand):
                return True
        except OSError:
            continue
    return False


def _assert_never_write_claude_md(path: str) -> None:
    """Hard guard: every write in this module goes through here first."""
    # Raised outside any `except OSError` on purpose: PermissionError is an OSError subclass.
    if _is_claude_md_path(path):
        raise PermissionError(CLAUDE_MD_READONLY_MSG)


def _read_text(path: str) -> str:
    if not os.path.isfile(path):
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read()


def _atomic_write(path: str, text: str) -> None:
    _assert_never_write_claude_md(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = f"{path}.tmp.{os.getpid()}"
    try:
        with open(tmp_path, "w", encoding="utf-8") as out_f:
            out_f.write(text)
            out_f.flush()
            os.fsync(out_f.fileno())
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def _claude_md_to_agents_text(raw: str) -> str:
    """CLAUDE.md -> AGENTS.md body. Drops managed wrappers and the `@AGENTS.md` import so a
    previously compiled CLAUDE.md can't turn AGENTS.md into a self-import."""
    text = strip_managed_wrappers(raw)
    text = "".join(ln for ln in text.splitlines(keepends=True) if ln.strip() != "@AGENTS.md")
    if not text.strip():
        raise ValueError("CLAUDE.md has no hand-written rules outside the managed block / @AGENTS.md import")
    return text


def _normalize_targets(target: str | None, targets) -> set[str] | None:
    raw: list[str] = []
    if target:
        raw.append(target)
    if isinstance(targets, str):
        raw.append(targets)
    elif targets:
        raw.extend(targets)
    if not raw:
        return None
    out: set[str] = set()
    for name in raw:
        norm = str(name).strip().rstrip("/")
        if _is_claude_md_path(norm):
            out.add("claude")
            continue
        key = os.path.basename(norm).casefold()
        if key not in _TARGET_ALIASES:
            raise ValueError(f"unknown sync target: {name!r} (expected agents|gemini|claude)")
        out.add(_TARGET_ALIASES[key])
    return out


def sync_rules(
    project_dir: str,
    state_dir: str | None = None,
    dry_run: bool = False,
    force: bool = False,
    approve_policy: bool = False,
    source: str = "agents",
    target: str | None = None,
    targets: list[str] | tuple[str, ...] | str | None = None,
    apply: bool | None = None,
) -> dict:
    """
    One-way rules export. CLAUDE.md is hand-maintained and strictly read-only here:
      - source="agents" (default): AGENTS.md -> .gemini/GEMINI.md (managed block).
        CLAUDE.md only gets a read-only diff preview of what the managed block would change.
        If AGENTS.md is missing it is bootstrapped from CLAUDE.md (read-only) or GEMINI.md.
      - source="claude": CLAUDE.md (read-only) -> AGENTS.md + .gemini/GEMINI.md.
      - Explicitly targeting CLAUDE.md outside dry_run (or with apply=True) raises PermissionError;
        force / approve_policy never change that.
      - Verifies SHA-256 integrity on existing managed blocks to detect unauthorized tampering.
      - Enforces anti-self-escalation on `hub-policy` JSON blocks.
    """
    if apply is not None:
        dry_run = not bool(apply)
    if source not in ("agents", "claude"):
        raise ValueError(f"unknown source: {source!r} (expected agents|claude)")
    selected = _normalize_targets(target, targets)
    if selected is not None and "claude" in selected and not dry_run:
        raise PermissionError(CLAUDE_MD_READONLY_MSG)
    if selected is None:
        selected = {"agents", "gemini", "claude"}

    target_dir = os.path.abspath(os.path.expanduser(project_dir or "."))
    if not os.path.isdir(target_dir):
        raise ValueError(f"Directory does not exist: {target_dir}")

    if state_dir is None:
        state_dir = os.path.join(paths.STATE_ROOT, "data")
    os.makedirs(state_dir, exist_ok=True)
    policy_store_path = os.path.join(state_dir, "approved_policies.json")

    agents_path = os.path.join(target_dir, "AGENTS.md")
    claude_path = os.path.join(target_dir, "CLAUDE.md")
    gemini_path = os.path.join(target_dir, ".gemini", "GEMINI.md")

    bootstrapped_from = ""
    if source == "claude":
        if not os.path.isfile(claude_path):
            raise FileNotFoundError(f"CLAUDE.md not found in {target_dir}")
        agents_text = _claude_md_to_agents_text(_read_text(claude_path))
    elif os.path.isfile(agents_path):
        agents_text = _read_text(agents_path)
    else:
        for candidate_rel, candidate_path in (("CLAUDE.md", claude_path), (".gemini/GEMINI.md", gemini_path)):
            if os.path.isfile(candidate_path):
                raw_cand = _read_text(candidate_path)
                if candidate_rel == "CLAUDE.md":
                    agents_text = _claude_md_to_agents_text(raw_cand)
                else:
                    agents_text = strip_managed_wrappers(raw_cand)
                bootstrapped_from = candidate_rel
                if not dry_run:
                    _atomic_write(agents_path, agents_text)
                break
        else:
            raise FileNotFoundError(f"AGENTS.md not found in {target_dir}")

    source_sha256 = sha256_text(agents_text)
    policy_obj, policy_sha256 = extract_hub_policy(agents_text)

    approved_map = {}
    if os.path.isfile(policy_store_path):
        try:
            with open(policy_store_path, encoding="utf-8") as pf:
                approved_map = json.load(pf)
        except (OSError, ValueError):
            approved_map = {}

    prev_policy_sha = approved_map.get(target_dir, "")
    policy_approval_required = False
    if policy_sha256:
        if prev_policy_sha and prev_policy_sha != policy_sha256 and not approve_policy:
            policy_approval_required = True
        elif (not prev_policy_sha or approve_policy) and not dry_run:
            approved_map[target_dir] = policy_sha256
            _atomic_write(policy_store_path, json.dumps(approved_map, ensure_ascii=False, indent=2))

    # (rel_name, abs_path, kind): kind = "raw" (whole-file export) | "managed" | "readonly"
    plan: list[tuple[str, str, str]] = []
    if source == "claude" and "agents" in selected:
        plan.append(("AGENTS.md", agents_path, "raw"))
    if "gemini" in selected:
        plan.append((".gemini/GEMINI.md", gemini_path, "managed"))
    if "claude" in selected:
        plan.append(("CLAUDE.md", claude_path, "readonly"))

    # Fail fast before touching anything if a write target aliases CLAUDE.md (symlink/hardlink).
    if not dry_run:
        for _, abs_path, kind in plan:
            if kind != "readonly":
                _assert_never_write_claude_md(abs_path)

    tampered_targets: list[str] = []
    file_results: dict[str, dict] = {}

    for rel_name, abs_path, kind in plan:
        read_only = kind == "readonly"
        old_text = _read_text(abs_path)
        if kind == "raw":
            check = {"tampered": False, "recorded_sha256": "", "actual_sha256": ""}
            new_text, block_sha256 = agents_text, source_sha256
        elif read_only and source == "claude":
            # CLAUDE.md is the source itself in this mode; nothing to preview.
            check = inspect_managed_block(old_text)
            new_text, block_sha256 = old_text, sha256_text(old_text)
        else:
            check = inspect_managed_block(old_text)
            new_block, block_sha256 = build_managed_block(
                agents_text, include_at_import=(rel_name == "CLAUDE.md")
            )
            new_text = merge_managed_block(old_text, new_block)

        # Hand edits to CLAUDE.md are expected, so they never block `ok`.
        if check["tampered"] and not read_only:
            tampered_targets.append(rel_name)

        changed = new_text != old_text
        diff_str = "".join(
            difflib.unified_diff(
                old_text.splitlines(keepends=True),
                new_text.splitlines(keepends=True),
                fromfile=f"a/{rel_name}",
                tofile=f"b/{rel_name}",
            )
        )

        wrote = False
        if (
            not read_only
            and changed
            and not dry_run
            and (not check["tampered"] or force)
            and not policy_approval_required
        ):
            _atomic_write(abs_path, new_text)
            wrote = True

        file_results[rel_name] = {
            "path": abs_path,
            "changed": changed,
            "wrote": wrote,
            "read_only": read_only,
            "protection_notice": CLAUDE_MD_PROTECTION_NOTICE if read_only else "",
            "sha256": block_sha256,
            "tampered": check["tampered"],
            "recorded_sha256": check["recorded_sha256"],
            "actual_sha256": check["actual_sha256"],
            "diff": diff_str,
        }

    ok = (len(tampered_targets) == 0 or force) and not policy_approval_required
    trust_info = ensure_workspace_trusted(target_dir, dry_run=dry_run)
    return {
        "ok": ok,
        "dry_run": dry_run,
        "dir": target_dir,
        "source": source,
        "agents_path": agents_path,
        "read_only_paths": [claude_path],
        "bootstrapped_from": bootstrapped_from,
        "source_sha256": source_sha256,
        "policy": policy_obj,
        "policy_sha256": policy_sha256,
        "policy_approval_required": policy_approval_required,
        "tampered_targets": tampered_targets,
        "files": file_results,
        "workspace_trust": trust_info,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mixhub sync-rules",
        description="One-way export of rules to AGENTS.md / .gemini/GEMINI.md. CLAUDE.md is read-only (diff preview only).",
    )
    parser.add_argument("dir", nargs="?", default=".", help="Project root directory")
    parser.add_argument("--dry-run", action="store_true", help="Preview unified diff without writing files")
    parser.add_argument("--force", action="store_true", help="Overwrite even if target managed block was modified (never applies to CLAUDE.md)")
    parser.add_argument("--approve-policy", action="store_true", help="Approve updated hub-policy block digest")
    parser.add_argument("--from-claude", action="store_true", help="Read CLAUDE.md and export it to AGENTS.md + .gemini/GEMINI.md")
    parser.add_argument(
        "--target",
        action="append",
        dest="targets",
        metavar="NAME",
        help="Limit to agents|gemini|claude (repeatable; claude = read-only preview, requires --dry-run)",
    )
    opts = parser.parse_args(argv)

    try:
        res = sync_rules(
            project_dir=opts.dir,
            dry_run=opts.dry_run,
            force=opts.force,
            approve_policy=opts.approve_policy,
            source="claude" if opts.from_claude else "agents",
            targets=opts.targets,
        )
    except Exception as exc:
        print(f"sync-rules error: {exc}", file=sys.stderr)
        return 1

    mode_lbl = "DRY-RUN" if opts.dry_run else "SYNC"
    src_lbl = "CLAUDE.md (read-only)" if res["source"] == "claude" else "AGENTS.md"
    print(f"● [{mode_lbl}] {src_lbl} SSoT ({res['dir']}) · sha256:{res['source_sha256'][:12]}")
    if res["bootstrapped_from"]:
        print(f"  - Bootstrapped AGENTS.md from {res['bootstrapped_from']}")
    if res["policy_approval_required"]:
        print("  ! Blocked: hub-policy digest changed; re-run with --approve-policy to activate.")
    if res["tampered_targets"]:
        print(f"  ! Tamper detected in: {', '.join(res['tampered_targets'])} (use --force to overwrite)")
    for rel_name, info in res["files"].items():
        if info["read_only"] and res["source"] == "claude":
            status = "read-only source"
        elif info["read_only"]:
            status = f"read-only, {'differs' if info['changed'] else 'in sync'} ({info['protection_notice']})"
            if info["changed"] and not opts.dry_run:
                status += "; --dry-run to view diff"
        else:
            status = "updated" if info["wrote"] else ("would update" if info["changed"] else "up-to-date")
            if info["tampered"] and not opts.force:
                status = "TAMPERED (skipped)"
        print(f"  - {rel_name}: {status} (sha256:{info['sha256'][:12]})")
        if opts.dry_run and info["diff"]:
            print(info["diff"].rstrip())
    return 0 if res["ok"] else 2
