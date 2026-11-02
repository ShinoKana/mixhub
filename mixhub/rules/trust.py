#!/usr/bin/env python3
"""`mixhub.rules.trust`: opt-in workspace trust pre-seeding for Claude Code, Antigravity / Gemini and Codex (`ensure_workspace_trusted`)."""
from __future__ import annotations

import json
import os
import re
import tempfile

from mixhub.core.env import env
from mixhub.core.prefs import load_preferences


def _is_temp_location(path: str) -> bool:
    if not path:
        return False
    ap = os.path.abspath(os.path.expanduser(path))
    rp = os.path.realpath(ap)
    prefixes = ("/tmp/", "/private/tmp/", "/var/folders/", "/private/var/folders/",
                os.path.join(os.path.realpath(tempfile.gettempdir()), ""))
    return ap.startswith(prefixes) or rp.startswith(prefixes)


def _atomic_write_json(path: str, data: dict, make_backup: bool = False) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    if make_backup and os.path.isfile(path):
        try:
            with open(path, "rb") as src_f:
                raw_bytes = src_f.read()
            bak_path = f"{path}.bak"
            with open(bak_path, "wb") as bak_f:
                bak_f.write(raw_bytes)
        except OSError:
            pass
    tmp_p = f"{path}.tmp.{os.getpid()}"
    with open(tmp_p, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp_p, path)




def _load_trust_preferences(preferences_path: str | None = None) -> dict:
    """`Preferences` JSON for the workspace trust policy: an explicit path, else the shared lookup."""
    return load_preferences(preferences_path)


def _is_within_root(target_dir: str, root_dir: str) -> bool:
    if not target_dir or not root_dir:
        return False
    t_abs = os.path.abspath(os.path.expanduser(target_dir))
    r_abs = os.path.abspath(os.path.expanduser(root_dir))
    t_real = os.path.realpath(t_abs)
    r_real = os.path.realpath(r_abs)
    return (
        t_abs == r_abs
        or t_abs.startswith(r_abs + os.sep)
        or t_real == r_real
        or t_real.startswith(r_real + os.sep)
    )


def _collect_existing_cli_trusted_roots(home_raw: str) -> list[str]:
    """Return directories already trusted in `~/.claude.json`, AGY settings, or Codex config."""
    roots: list[str] = []
    claude_cfg = os.path.join(home_raw, ".claude.json")
    if os.path.isfile(claude_cfg):
        try:
            with open(claude_cfg, encoding="utf-8") as f:
                cdata = json.load(f)
            if isinstance(cdata, dict) and isinstance(cdata.get("projects"), dict):
                for p, info in cdata["projects"].items():
                    if isinstance(info, dict) and info.get("hasTrustDialogAccepted") is True:
                        roots.append(str(p))
        except (OSError, ValueError):
            pass

    agy_cfg = os.path.join(home_raw, ".gemini", "antigravity-cli", "settings.json")
    if os.path.isfile(agy_cfg):
        try:
            with open(agy_cfg, encoding="utf-8") as f:
                adata = json.load(f)
            if isinstance(adata, dict) and isinstance(adata.get("trustedWorkspaces"), list):
                for p in adata["trustedWorkspaces"]:
                    if isinstance(p, str) and p.strip():
                        roots.append(p.strip())
        except (OSError, ValueError):
            pass

    tf_cfg = os.path.join(home_raw, ".gemini", "trustedFolders.json")
    if os.path.isfile(tf_cfg):
        try:
            with open(tf_cfg, encoding="utf-8") as f:
                tdata = json.load(f)
            if isinstance(tdata, dict):
                for p, status in tdata.items():
                    if status == "TRUST_FOLDER" and isinstance(p, str):
                        roots.append(p)
        except (OSError, ValueError):
            pass

    codex_cfg = os.path.join(home_raw, ".codex", "config.toml")
    if os.path.isfile(codex_cfg):
        try:
            with open(codex_cfg, encoding="utf-8") as f:
                ctext = f.read()
            for m in re.finditer(
                r'(?m)^\[projects\."([^"]+)"\]\s*(?:\n(?!\[).*?)*?\btrust_level\s*=\s*"trusted"',
                ctext,
            ):
                roots.append(m.group(1))
        except OSError:
            pass

    return roots


def ensure_workspace_trusted(
    project_dir: str,
    home_dir: str | None = None,
    dry_run: bool = False,
    *,
    preferences_path: str | None = None,
    trust_all_workspaces: bool | None = None,
    trust_roots: list[str] | None = None,
) -> dict:
    """
    Pre-seed workspace trust for Claude (`~/.claude.json`), Gemini/AGY
    (`~/.gemini/antigravity-cli/settings.json` + `~/.gemini/trustedFolders.json`),
    and Codex (`~/.codex/config.toml`) so launching or relaying into `project_dir`
    never blocks on interactive `Do you trust the contents of this directory?` modals.
    Idempotent, atomic, and preserves all existing JSON/TOML fields.

    Trust scope policy:
      - When `Preferences["trust_all_workspaces"] == True` (opt-in local setup): trusts any
        valid workspace directory inside `home_dir`.
      - When `False` (open-source default): trusts `project_dir` ONLY if it is inside
        `Preferences.get("trust_roots", [])` or is already a subdirectory of an existing
        native CLI trusted workspace.
    """
    raw_dir = os.path.abspath(os.path.expanduser(project_dir or "."))
    real_dir = os.path.realpath(raw_dir)
    if not os.path.isdir(raw_dir) or real_dir in ("/", "/tmp", "/private/tmp", "/var", "/private/var"):
        return {"ok": False, "trusted": False, "dir": raw_dir, "updated": []}

    explicit_home = home_dir or env("AGENT_HOME")
    default_home = os.path.abspath(os.path.expanduser(explicit_home or "~"))
    default_home_real = os.path.realpath(default_home)

    if explicit_home or _is_temp_location(default_home):
        home_raw = default_home
    elif _is_temp_location(raw_dir):
        # Sandbox temp workspaces so unit tests calling `ensure_workspace_trusted(tmp_dir)`
        # without `home_dir` never pollute the real user's `~/.claude.json`.
        parent_dir = os.path.dirname(raw_dir)
        if any(
            os.path.exists(os.path.join(parent_dir, marker))
            for marker in (".claude.json", ".codex", ".gemini")
        ):
            home_raw = parent_dir
        else:
            home_raw = raw_dir
    else:
        in_home = (
            real_dir == default_home_real
            or real_dir.startswith(default_home_real + os.sep)
            or raw_dir == default_home
            or raw_dir.startswith(default_home + os.sep)
        )
        if not in_home:
            return {"ok": False, "trusted": False, "dir": raw_dir, "updated": []}
        home_raw = default_home

    prefs = _load_trust_preferences(preferences_path=preferences_path)
    eff_trust_all = (
        bool(prefs.get("trust_all_workspaces", False))
        if trust_all_workspaces is None
        else bool(trust_all_workspaces)
    )
    if not eff_trust_all:
        cfg_roots = (
            list(trust_roots)
            if trust_roots is not None
            else [str(r) for r in (prefs.get("trust_roots") or []) if str(r).strip()]
        )
        allowed_roots = cfg_roots + _collect_existing_cli_trusted_roots(home_raw)
        if not any(_is_within_root(raw_dir, r) for r in allowed_roots):
            return {
                "ok": False,
                "trusted": False,
                "reason": "untrusted_root",
                "dir": raw_dir,
                "home_dir": home_raw,
                "updated": [],
            }

    updated: list[str] = []
    candidate_dirs = list(dict.fromkeys([raw_dir, real_dir]))

    # 1. Claude (~/.claude.json -> projects[cwd].hasTrustDialogAccepted = True)
    claude_cfg_path = os.path.join(home_raw, ".claude.json")
    try:
        claude_data: dict = {}
        abort_claude = False
        if os.path.isfile(claude_cfg_path):
            with open(claude_cfg_path, encoding="utf-8") as f:
                raw_content = f.read().strip()
            if raw_content:
                try:
                    loaded = json.loads(raw_content)
                    if isinstance(loaded, dict):
                        claude_data = loaded
                    else:
                        abort_claude = True
                except ValueError:
                    # Never overwrite a non-empty unparseable ~/.claude.json
                    abort_claude = True
        if not abort_claude:
            projects = claude_data.get("projects")
            if not isinstance(projects, dict):
                projects = {}
                claude_data["projects"] = projects
            claude_changed = False
            for d in candidate_dirs:
                entry = projects.get(d)
                if not isinstance(entry, dict):
                    entry = {}
                    projects[d] = entry
                    claude_changed = True
                for flag_key in (
                    "hasTrustDialogAccepted",
                    "hasClaudeMdExternalIncludesApproved",
                    "hasClaudeMdExternalIncludesWarningShown",
                ):
                    if entry.get(flag_key) is not True:
                        entry[flag_key] = True
                        claude_changed = True
            if claude_changed:
                if not dry_run:
                    _atomic_write_json(claude_cfg_path, claude_data, make_backup=True)
                updated.append("claude")
    except OSError:
        pass

    # 2. Gemini / Antigravity (~/.gemini/antigravity-cli/settings.json + ~/.gemini/trustedFolders.json)
    agy_cfg_path = os.path.join(home_raw, ".gemini", "antigravity-cli", "settings.json")
    gemini_trusted_path = os.path.join(home_raw, ".gemini", "trustedFolders.json")
    try:
        agy_data: dict = {}
        abort_agy = False
        if os.path.isfile(agy_cfg_path):
            with open(agy_cfg_path, encoding="utf-8") as f:
                raw_agy = f.read().strip()
            if raw_agy:
                try:
                    loaded = json.loads(raw_agy)
                    if isinstance(loaded, dict):
                        agy_data = loaded
                    else:
                        abort_agy = True
                except ValueError:
                    abort_agy = True
        agy_changed = False
        if not abort_agy:
            tw = agy_data.get("trustedWorkspaces")
            if not isinstance(tw, list):
                tw = []
                agy_data["trustedWorkspaces"] = tw
            for d in candidate_dirs:
                if d not in tw:
                    tw.append(d)
                    agy_changed = True
            if agy_changed and not dry_run:
                _atomic_write_json(agy_cfg_path, agy_data, make_backup=False)

        tf_data: dict = {}
        abort_tf = False
        if os.path.isfile(gemini_trusted_path):
            with open(gemini_trusted_path, encoding="utf-8") as f:
                raw_tf = f.read().strip()
            if raw_tf:
                try:
                    loaded_tf = json.loads(raw_tf)
                    if isinstance(loaded_tf, dict):
                        tf_data = loaded_tf
                    else:
                        abort_tf = True
                except ValueError:
                    abort_tf = True
        tf_changed = False
        if not abort_tf:
            for d in candidate_dirs:
                if tf_data.get(d) != "TRUST_FOLDER":
                    tf_data[d] = "TRUST_FOLDER"
                    tf_changed = True
            if tf_changed and not dry_run:
                _atomic_write_json(gemini_trusted_path, tf_data, make_backup=False)

        if agy_changed or tf_changed:
            updated.append("agy")
    except OSError:
        pass

    # 3. Codex (~/.codex/config.toml -> [projects."<cwd>"] trust_level = "trusted")
    codex_cfg_path = os.path.join(home_raw, ".codex", "config.toml")
    try:
        codex_text = ""
        if os.path.isfile(codex_cfg_path):
            with open(codex_cfg_path, encoding="utf-8") as f:
                codex_text = f.read()
        codex_changed = False
        new_codex_text = codex_text
        for d in candidate_dirs:
            escaped_d = d.replace("\\", "\\\\").replace('"', '\\"')
            header = f'[projects."{escaped_d}"]'
            if header not in new_codex_text:
                sep = (
                    ""
                    if (not new_codex_text or new_codex_text.endswith("\n\n"))
                    else ("\n" if new_codex_text.endswith("\n") else "\n\n")
                )
                new_codex_text = f'{new_codex_text}{sep}{header}\ntrust_level = "trusted"\n'
                codex_changed = True
            else:
                idx = new_codex_text.find(header)
                after_header = idx + len(header)
                next_sec = re.search(r"(?m)^\[", new_codex_text[after_header:])
                sec_end = (after_header + next_sec.start()) if next_sec else len(new_codex_text)
                sec_body = new_codex_text[after_header:sec_end]
                if not re.search(r'(?m)^\s*trust_level\s*=\s*"trusted"', sec_body):
                    if re.search(r"(?m)^\s*trust_level\s*=", sec_body):
                        new_sec_body = re.sub(
                            r"(?m)^(\s*trust_level\s*=\s*).*$",
                            r'\1"trusted"',
                            sec_body,
                            count=1,
                        )
                    else:
                        new_sec_body = '\ntrust_level = "trusted"' + (
                            sec_body if sec_body.startswith("\n") else "\n" + sec_body
                        )
                    new_codex_text = (
                        new_codex_text[:after_header] + new_sec_body + new_codex_text[sec_end:]
                    )
                    codex_changed = True
        if codex_changed:
            if not dry_run:
                os.makedirs(os.path.dirname(codex_cfg_path), exist_ok=True)
                tmp_p = f"{codex_cfg_path}.tmp.{os.getpid()}"
                with open(tmp_p, "w", encoding="utf-8") as f:
                    f.write(new_codex_text)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_p, codex_cfg_path)
            updated.append("codex")
    except OSError:
        pass

    return {
        "ok": True,
        "trusted": True,
        "dir": raw_dir,
        "home_dir": home_raw,
        "updated": updated,
        "claude_config": claude_cfg_path,
        "agy_config": agy_cfg_path,
        "gemini_trusted_config": gemini_trusted_path,
        "codex_config": codex_cfg_path,
    }
