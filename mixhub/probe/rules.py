#!/usr/bin/env python3
"""`mixhub.probe.rules`: compile rule pattern lists and load the hot-reloadable `data/probe_rules.json` (mtime / size / inode cached)."""
from __future__ import annotations

import json
import os
import re

from mixhub.probe.patterns import BUILTIN_PROBE_RULES, DEFAULT_PROBE_RULES_PATH, NESTED_QUANTIFIER_RE, RULE_KEY_ALIASES


def _compile_pattern_list(patterns: list[str], category: str, errors: list[str]) -> list[re.Pattern]:
    compiled: list[re.Pattern] = []
    for raw in patterns:
        pat = str(raw or "").strip()
        if not pat:
            continue
        if len(pat) > 250:
            errors.append(f"{category}: pattern exceeds 250 chars")
            continue
        if NESTED_QUANTIFIER_RE.search(pat):
            errors.append(f"{category}: rejected nested quantifier in {pat!r}")
            continue
        try:
            compiled.append(re.compile(pat, re.I))
        except re.error as exc:
            errors.append(f"{category}: invalid regex {pat!r} ({exc})")
    return compiled


def _compile_rules_dict(raw_data: dict | None, source: str = "builtin") -> tuple[dict, str]:
    errors: list[str] = []
    merged_raw: dict[str, list[str]] = {k: list(v) for k, v in BUILTIN_PROBE_RULES.items()}
    custom_raw: dict[str, list[str]] = {k: [] for k in BUILTIN_PROBE_RULES}

    if isinstance(raw_data, dict):
        for k, val in raw_data.items():
            if k in ("version", "_comment", "description", "updated_at"):
                continue
            canonical = RULE_KEY_ALIASES.get(str(k).strip())
            if not canonical:
                errors.append(f"unknown key: {k}")
                continue
            mode = "extend"
            items: list[str] = []
            if isinstance(val, list):
                items = [str(x) for x in val if str(x).strip()]
            elif isinstance(val, dict):
                mode = str(val.get("mode") or "extend").strip().lower()
                raw_items = val.get("patterns")
                if isinstance(raw_items, list):
                    items = [str(x) for x in raw_items if str(x).strip()]
                else:
                    errors.append(f"{k}: dict must contain 'patterns' list")
                    continue
            elif isinstance(val, str) and val.strip():
                items = [val.strip()]
            else:
                errors.append(f"{k}: expected list or dict")
                continue

            if mode == "replace":
                merged_raw[canonical] = list(items)
                custom_raw[canonical] = list(items)
            else:
                for it in items:
                    if it not in merged_raw[canonical]:
                        merged_raw[canonical].append(it)
                    if it not in custom_raw[canonical]:
                        custom_raw[canonical].append(it)

    compiled = {
        cat: _compile_pattern_list(pats, cat, errors)
        for cat, pats in merged_raw.items()
    }
    compiled_custom = {
        cat: _compile_pattern_list(pats, f"custom.{cat}", errors)
        for cat, pats in custom_raw.items()
    }
    err_str = "; ".join(errors)
    return {
        "source": source,
        "raw": merged_raw,
        "custom_raw": custom_raw,
        "compiled": compiled,
        "compiled_custom": compiled_custom,
        "error": err_str,
    }, err_str


_BUILTIN_COMPILED_RULES, _ = _compile_rules_dict(None, source="builtin")
_RULES_CACHE: dict[str, tuple[tuple[int, int, int] | None, dict, str]] = {}


def load_probe_rules(rules_path: str | None = None, *, force: bool = False) -> dict:
    """
    Load and cache external hot-reloadable probe rules from `data/probe_rules.json`
    (keyed by `(st_mtime_ns, st_size, st_ino)`).
    - When `rules_path` does not exist on disk, falls back to `BUILTIN_PROBE_RULES`.
    - When `rules_path` is modified on disk, automatically hot-reloads on the next call.
    - When `rules_path` contains invalid JSON mid-edit, retains the last-good compiled rules
      (or built-ins on first load) and exposes the parse error in `rules["error"]`.
    """
    target_path = os.path.abspath(os.path.expanduser(rules_path or DEFAULT_PROBE_RULES_PATH))
    cached_entry = _RULES_CACHE.get(target_path)

    try:
        st = os.stat(target_path)
    except FileNotFoundError:
        res = {
            **_BUILTIN_COMPILED_RULES,
            "path": target_path,
            "sig": None,
            "error": "",
        }
        _RULES_CACHE[target_path] = (None, res, "")
        return res
    except OSError as exc:
        fallback = cached_entry[1] if cached_entry is not None else _BUILTIN_COMPILED_RULES
        err_msg = f"{type(exc).__name__}: {exc}"
        return {**fallback, "path": target_path, "error": err_msg}

    sig = (int(st.st_mtime_ns), int(st.st_size), int(st.st_ino))
    if not force and cached_entry is not None and cached_entry[0] == sig:
        return cached_entry[1]

    try:
        with open(target_path, encoding="utf-8") as fh:
            loaded = json.load(fh)
        if not isinstance(loaded, dict):
            raise ValueError("probe_rules.json root must be a JSON object")
        compiled_bundle, err_str = _compile_rules_dict(loaded, source="file")
        result = {
            **compiled_bundle,
            "path": target_path,
            "sig": sig,
            "error": err_str,
        }
        _RULES_CACHE[target_path] = (sig, result, err_str)
        return result
    except (OSError, ValueError) as exc:
        err_str = f"{type(exc).__name__}: {exc}"
        prev_good = cached_entry[1] if cached_entry is not None else _BUILTIN_COMPILED_RULES
        kept = {
            **prev_good,
            "path": target_path,
            "sig": sig,
            "error": err_str,
        }
        _RULES_CACHE[target_path] = (sig, kept, err_str)
        return kept


def _match_any(patterns: list[re.Pattern], text: str) -> bool:
    sample = str(text or "")[:400]
    return any(p.search(sample) for p in patterns)
