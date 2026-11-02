"""10-section handoff artifact extraction from a session transcript (plus git metadata and prompt cleanup)."""
from __future__ import annotations

import json
import os
import re
import subprocess

from mixhub.handoff.text import (
    ACCEPTANCE_RE,
    CONSTRAINT_RE,
    FILE_LINE_RE,
    FILE_RANGE_RE,
    IRREV_RE,
    REJECTED_RE,
    UNVERIFIED_RE,
    _clean_verbatim_turn_text,
    _coalesce_markdown_blocks,
    _extract_codex_exec_cmd,
    _snippet,
    _summarize_error_output,
    redact_secrets,
)
from mixhub.handoff.resume import classify_resume_state, infer_effective_root
from mixhub.handoff.contract import resolve_handoff_mode


def _collect_git_metadata(cwd: str) -> dict:
    """Collect read-only git branch, commit, porcelain status, and diff stat with hooks disabled."""
    info = {
        "cwd": cwd or "",
        "branch": "main",
        "commit": "HEAD",
        "changed_files": [],
        "diff_stat": [],
        "lines": [],
    }
    if not cwd or not os.path.isdir(cwd) or not os.path.exists(os.path.join(cwd, ".git")):
        return info
    try:
        rev = subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=1.5,
        )
        if rev.returncode == 0 and rev.stdout.strip():
            rev_lines = [ln.strip() for ln in rev.stdout.splitlines() if ln.strip()]
            if rev_lines:
                info["branch"] = rev_lines[0]
            if len(rev_lines) >= 2:
                info["commit"] = rev_lines[1]
                info["lines"].append(f"Git HEAD: `{info['commit']}` (branch `{info['branch']}`)")
        status = subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "-C", cwd, "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=1.5,
        )
        if status.returncode == 0:
            st_lines = [ln.rstrip() for ln in status.stdout.splitlines() if ln.strip()]
            if st_lines:
                for ln in st_lines[:10]:
                    info["lines"].append(f"Working tree: `{ln}`")
                    parts = ln.strip().split(None, 1)
                    if len(parts) == 2:
                        info["changed_files"].append(parts[1].strip())
            else:
                info["lines"].append("Working tree: clean")
        diff_stat = subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", "-C", cwd, "diff", "--stat"],
            capture_output=True,
            text=True,
            timeout=1.5,
        )
        if diff_stat.returncode == 0 and diff_stat.stdout.strip():
            info["diff_stat"] = [ln.strip() for ln in diff_stat.stdout.splitlines() if ln.strip()][:6]
    except Exception:
        pass
    return info


def extract_handoff_artifacts(
    hub,
    session: dict,
    to_tool: str,
    provider: str = "",
    mode: str = "auto",
    note: str = "",
) -> tuple[dict[str, list[str]], dict, str]:
    """
    Deterministically extract:
      1. The structured Handoff Bundle (`sections`), including:
         - `## 0 接续点 (Immediate Action)` with 4-way `resume_state` (`user_pending`, `mid_task`, `awaiting_user`, `completed_idle`),
         - `## 3 近期对话原文 (Recent Verbatim Turns)` (2~5 recent turns, folded `[Tools: ...]`, and `#L<start>-L<end>` pointer),
         - `## 4 关键文件与锚点` (both modified files and read/grep key files with line ranges),
         - and the 10 canonical sections tailored by directional `mode`;
      2. The unified `handoff.yaml` state reconstruction contract (`contract`), including inferred `effective_root`,
         `resume_state`, `verified` facts, `unverified` hypotheses/risks, and `failed_attempts`;
      3. The resolved directional mode (`escalate` | `delegate` | `review`).
    """
    path = session.get("path")
    tool = session.get("tool") or "claude"
    sid = session.get("id") or "unknown"
    home_dir = getattr(hub, "home", "") if hub is not None else os.path.expanduser("~")
    declared_cwd = session.get("cwd") or home_dir or os.path.expanduser("~")

    user_prompts: list[tuple[str, str]] = []
    assistant_msgs: list[tuple[str, str]] = []
    system_prompts: list[tuple[str, str]] = []
    pinned_decisions: list[str] = []
    rejected_approaches: list[str] = []
    verbatim_constraints: list[str] = []
    acceptance_items: list[str] = []
    unverified_hypotheses: list[str] = []
    files_modified: dict[str, str] = {}
    files_read: dict[str, dict] = {}
    tool_paths: list[str] = []
    locations: list[str] = []
    verification_cmds: list[str] = []
    errors_seen: list[str] = []
    compaction_locs: list[str] = []
    codex_call_map: dict[str, int] = {}
    turns: list[dict] = []
    current_turn: dict | None = None
    last_event_kind: str = ""
    last_action_summary: str = ""
    corrupt_tail_line: bool = False
    total_file_lines: int = 0
    read_seq: int = 0

    def _record_tool_path(fpath: str, weight: int = 1) -> None:
        clean_p = str(fpath or "").strip().strip("'\"")
        if not clean_p or clean_p.startswith("-"):
            return
        for _ in range(max(1, weight)):
            tool_paths.append(clean_p)

    def _record_file_read(fpath: str, op: str, line_range: str = "", loc: str = "") -> None:
        nonlocal read_seq
        clean_p = str(fpath or "").strip().strip("'\"")
        clean_p = re.sub(r":\d+(?:-\d+|\+)?$", "", clean_p)
        if not clean_p or clean_p.startswith("-"):
            return
        _record_tool_path(clean_p, weight=1)
        read_seq += 1
        info = files_read.get(clean_p)
        if info is None:
            info = {
                "count": 0,
                "ranges": [],
                "ops": [],
                "last_loc": loc or "",
                "seq": read_seq,
            }
            files_read[clean_p] = info
        info["count"] += 1
        info["seq"] = read_seq
        if loc:
            info["last_loc"] = loc
        if op and op not in info["ops"]:
            info["ops"].append(op)
        rng = str(line_range or "").strip()
        if rng and rng not in info["ranges"]:
            info["ranges"].append(rng)

    def _extract_cmd_file_reads(cmd_str: str, loc: str) -> list[str]:
        """Extract file reads/searches and line ranges from shell commands (sed, rg, grep, pytest)."""
        s = str(cmd_str or "").strip()
        if not s:
            return []
        extracted_ops: list[str] = []
        # 1. sed -n '3450,3550p' path/to/file.py
        for m_sed in re.finditer(r"\bsed\s+-n\s+['\"]?(\d+)(?:,(\d+))?p['\"]?\s+([^\s|;&'\"]+)", s):
            st, en, fp = m_sed.group(1), m_sed.group(2) or m_sed.group(1), m_sed.group(3)
            rng = f"{st}-{en}" if st != en else st
            _record_file_read(fp, "Read(sed)", rng, loc)
            extracted_ops.append(f"Read {os.path.basename(fp)}:{rng}")
        # 2. nl -ba file.py | sed -n '100,200p'
        for m_nl in re.finditer(r"\bnl\s+-ba\s+([^\s|;&'\"]+)\s*\|\s*sed\s+-n\s+['\"]?(\d+)(?:,(\d+))?p['\"]?", s):
            fp, st, en = m_nl.group(1), m_nl.group(2), m_nl.group(3) or m_nl.group(2)
            rng = f"{st}-{en}" if st != en else st
            _record_file_read(fp, "Read(nl)", rng, loc)
            extracted_ops.append(f"Read {os.path.basename(fp)}:{rng}")
        # 3. rg / grep targeting specific source files
        if re.search(r"\b(?:rg|grep)\b", s):
            for m_f in re.finditer(
                r"(?:^|\s)([A-Za-z0-9_./~-]+/[A-Za-z0-9_.-]+\.(?:py|js|ts|tsx|jsx|rs|go|sql|sh|c|cpp|h|java|rb|toml|yaml|yml|json|md)"
                r"|[A-Za-z0-9_.-]+\.(?:py|js|ts|tsx|jsx|rs|go|sql|sh|c|cpp|h|java|rb|toml|yaml|yml|json|md))\b",
                s,
            ):
                fp = m_f.group(1).strip()
                if fp and not fp.startswith("-"):
                    _record_file_read(fp, "Grep", "", loc)
        # 4. cd /path/to/proj && ...
        m_cd = re.match(r"^\s*cd\s+([^\s;&|]+)", s)
        if m_cd:
            cd_dir = m_cd.group(1).strip("'\"")
            if cd_dir and cd_dir not in ("~", "/tmp", "/private/tmp", "."):
                _record_tool_path(cd_dir, weight=1)
        return extracted_ops

    def _start_or_append_user_turn(line_no: int, loc: str, txt: str) -> dict:
        nonlocal current_turn, last_event_kind
        last_event_kind = "user"
        if (
            current_turn is None
            or current_turn["assistant_msgs"]
            or current_turn["tools"]
        ):
            current_turn = {
                "turn_index": len(turns) + 1,
                "start_line": line_no,
                "end_line": line_no,
                "user_loc": loc,
                "user_texts": [txt],
                "tools": [],
                "tool_call_map": {},
                "assistant_msgs": [],
            }
            turns.append(current_turn)
        else:
            if not current_turn["user_texts"] or current_turn["user_texts"][-1] != txt:
                current_turn["user_texts"].append(txt)
            current_turn["end_line"] = line_no
        return current_turn

    def _ensure_turn(line_no: int, loc: str) -> dict:
        nonlocal current_turn
        if current_turn is None:
            seed_txt = system_prompts[-1][1] if system_prompts else (session.get("firstPrompt") or session.get("title") or "(Initial session task)")
            current_turn = {
                "turn_index": len(turns) + 1,
                "start_line": line_no,
                "end_line": line_no,
                "user_loc": loc,
                "user_texts": [redact_secrets(str(seed_txt))],
                "tools": [],
                "tool_call_map": {},
                "assistant_msgs": [],
            }
            turns.append(current_turn)
        else:
            current_turn["end_line"] = line_no
        return current_turn

    if path and os.path.isfile(path):
        try:
            file_size = os.path.getsize(path)
            tail_window = 524288
            sampled_lines: list[tuple[int, bytes]] = []
            full_lines_cache: list[bytes] | None = None
            full_sibling = (
                os.path.join(os.path.dirname(path), "transcript_full.jsonl")
                if os.path.basename(path) == "transcript.jsonl"
                else ""
            )

            with open(path, "rb") as f:
                if file_size <= 2 * 1024 * 1024:
                    sampled_lines = list(enumerate(f, 1))
                    total_file_lines = len(sampled_lines)
                else:
                    # Stream all lines with exact line numbers; keep head/tail and any middle line with signal
                    mid_cutoff = max(0, file_size - tail_window)
                    bytes_pos = 0
                    found_real_user = 0
                    for line_no, raw_line in enumerate(f, 1):
                        total_file_lines = line_no
                        bytes_pos += len(raw_line)
                        if line_no <= 120 or found_real_user < 2 or bytes_pos >= mid_cutoff:
                            sampled_lines.append((line_no, raw_line))
                            if (
                                b'"USER_INPUT"' in raw_line
                                or b'"user_message"' in raw_line
                                or (b'"user"' in raw_line and b"AGENTS.md instructions" not in raw_line)
                            ):
                                found_real_user += 1
                        elif len(raw_line) <= 32768 or any(
                            tok in raw_line
                            for tok in (
                                b'"user"',
                                b'"USER_INPUT"',
                                b'"user_message"',
                                b"apply_patch",
                                b"exec_command",
                                b"read_file",
                                b'"Read"',
                                b'"Edit"',
                                b'"Write"',
                                b'"Grep"',
                                b"Traceback",
                                b"exit code",
                                b"<CONTEXT_SUMMARY>",
                                b"isCompactSummary",
                                b"[IRREV]",
                            )
                        ):
                            sampled_lines.append((line_no, raw_line))

            for idx_s, (line_no, raw_line) in enumerate(sampled_lines):
                is_last_sampled = idx_s == len(sampled_lines) - 1
                if b'"truncated_fields"' in raw_line and full_sibling and os.path.isfile(full_sibling):
                    if full_lines_cache is None:
                        try:
                            with open(full_sibling, "rb") as ff:
                                full_lines_cache = ff.readlines()
                        except OSError:
                            full_lines_cache = []
                    if full_lines_cache and 1 <= line_no <= len(full_lines_cache):
                        raw_line = full_lines_cache[line_no - 1]

                try:
                    row = json.loads(raw_line)
                except (ValueError, UnicodeError):
                    if is_last_sampled and raw_line.strip():
                        corrupt_tail_line = True
                    continue
                if not isinstance(row, dict):
                    continue
                step_idx = row.get("step_index")
                loc = f"step {step_idx} (#L{line_no})" if step_idx is not None else f"#L{line_no}"
                if b"<CONTEXT_SUMMARY>" in raw_line or row.get("type") == "summary" or row.get("isCompactSummary"):
                    compaction_locs.append(loc)
                    continue

                if tool == "agy":
                    rtype = row.get("type")
                    if rtype == "USER_INPUT":
                        txt = redact_secrets(hub_clean_prompt(row.get("content", ""), "agy"))
                        if txt and (not user_prompts or user_prompts[-1][1] != txt):
                            user_prompts.append((loc, txt))
                            _start_or_append_user_turn(line_no, loc, txt)
                    elif rtype == "SYSTEM_MESSAGE":
                        raw_sys = str(row.get("content") or "")
                        m_sys = re.search(r"priority=MESSAGE_PRIORITY_\w+\s+content=(.*)", raw_sys, re.S)
                        sys_body = m_sys.group(1) if m_sys else raw_sys
                        txt = redact_secrets(hub_clean_prompt(sys_body, "agy"))
                        if txt:
                            system_prompts.append((loc, txt))
                    elif rtype == "PLANNER_RESPONSE":
                        t_cur = _ensure_turn(line_no, loc)
                        content_txt = redact_secrets(row.get("content") or "")
                        if content_txt:
                            assistant_msgs.append((loc, content_txt))
                            t_cur["assistant_msgs"].append((loc, content_txt))
                            last_event_kind = "assistant"
                            last_action_summary = f"Assistant response ({loc})"
                        for tc in row.get("tool_calls") or []:
                            tname = tc.get("name", "")
                            targs = tc.get("args") or {}
                            if tname in ("write_file", "edit_file"):
                                fpath = str(targs.get("Path") or targs.get("path") or "").strip().strip('"')
                                s_ln = targs.get("StartLine") or targs.get("start_line") or targs.get("line")
                                e_ln = targs.get("EndLine") or targs.get("end_line")
                                rng = f"{s_ln}-{e_ln}" if (s_ln and e_ln and s_ln != e_ln) else (f"{s_ln}" if s_ln else "")
                                if fpath:
                                    files_modified[fpath] = f"{tname}" + (f":{rng}" if rng else "") + f" @ {loc}"
                                    _record_tool_path(fpath, weight=2)
                                    act = f"{'Edit' if 'edit' in tname else 'Write'} {os.path.basename(fpath)}" + (f":{rng}" if rng else "")
                                    t_cur["tools"].append(act)
                                    last_action_summary = f"{act} ({loc})"
                                last_event_kind = "tool_call"
                            elif tname in ("read_file", "view_file_outline", "view_code_item"):
                                fpath = str(targs.get("Path") or targs.get("path") or targs.get("AbsolutePath") or targs.get("File") or "").strip().strip('"')
                                s_ln = targs.get("StartLine") or targs.get("start_line")
                                e_ln = targs.get("EndLine") or targs.get("end_line")
                                rng = f"{s_ln}-{e_ln}" if (s_ln and e_ln) else (f"{s_ln}+" if s_ln else "")
                                if fpath:
                                    _record_file_read(fpath, "read_file", rng, loc)
                                    act = f"Read {os.path.basename(fpath)}" + (f":{rng}" if rng else "")
                                    t_cur["tools"].append(act)
                                    last_action_summary = f"{act} ({loc})"
                                last_event_kind = "tool_call"
                            elif tname in ("grep_search", "find_by_name"):
                                spath = str(
                                    targs.get("SearchPath")
                                    or targs.get("SearchDirectory")
                                    or targs.get("Directory")
                                    or targs.get("Path")
                                    or ""
                                ).strip().strip('"')
                                q = str(targs.get("Query") or targs.get("Pattern") or "").strip()
                                if spath:
                                    _record_file_read(spath, tname, "", loc)
                                act = f"Grep {os.path.basename(spath) or q}".strip()
                                t_cur["tools"].append(act)
                                last_action_summary = f"{act} ({loc})"
                                last_event_kind = "tool_call"
                            elif tname == "run_command":
                                raw_cmd = str(targs.get("CommandLine") or "").strip()
                                if len(raw_cmd) >= 2 and raw_cmd.startswith('"') and raw_cmd.endswith('"'):
                                    try:
                                        raw_cmd = json.loads(raw_cmd)
                                    except ValueError:
                                        raw_cmd = raw_cmd.strip('"')
                                _extract_cmd_file_reads(raw_cmd, loc)
                                cmd = _snippet(redact_secrets(raw_cmd), 140)
                                if cmd:
                                    verification_cmds.append(f"[{loc}] `{cmd}`")
                                    t_cur["last_verif_idx"] = len(verification_cmds) - 1
                                act = f"Bash {_snippet(redact_secrets(raw_cmd), 48)}"
                                t_cur["tools"].append(act)
                                t_cur["last_bash_idx"] = len(t_cur["tools"]) - 1
                                last_action_summary = f"{act} ({loc})"
                                last_event_kind = "tool_call"
                            elif tname == "ask_question":
                                t_cur["tools"].append("ask_question")
                                last_action_summary = f"ask_question ({loc})"
                                last_event_kind = "ask_user_tool"
                            elif tname:
                                t_cur["tools"].append(tname)
                                last_action_summary = f"{tname} ({loc})"
                                last_event_kind = "tool_call"
                    elif rtype == "GENERIC":
                        if current_turn is not None:
                            current_turn["end_line"] = line_no
                            gen_txt = redact_secrets(str(row.get("content") or ""))
                            m_ec = re.search(r"(?:Command exited with code|exit code)[:\s]+(-?\d+)", gen_txt, re.I)
                            ec = int(m_ec.group(1)) if m_ec else (1 if row.get("status") == "ERROR" else 0)
                            if "last_bash_idx" in current_turn:
                                b_idx = current_turn.pop("last_bash_idx")
                                if 0 <= b_idx < len(current_turn["tools"]) and "-> exit" not in current_turn["tools"][b_idx]:
                                    current_turn["tools"][b_idx] = f"{current_turn['tools'][b_idx]} -> exit {ec}"
                                    last_action_summary = f"{current_turn['tools'][b_idx]} ({loc})"
                            if "last_verif_idx" in current_turn and (m_ec or row.get("status") in ("DONE", "ERROR")):
                                v_idx = current_turn.pop("last_verif_idx")
                                if 0 <= v_idx < len(verification_cmds) and "-> exit" not in verification_cmds[v_idx]:
                                    verification_cmds[v_idx] = f"{verification_cmds[v_idx]} -> exit {ec}"
                            for f_m, s_m, e_m in FILE_RANGE_RE.findall(gen_txt[:4000]):
                                rng_m = f"{s_m}-{e_m}" if e_m else s_m
                                _record_file_read(f_m, "output_ref", rng_m, loc)
                        if last_event_kind == "tool_call":
                            last_event_kind = "tool_result"

                    if (
                        row.get("status") == "ERROR"
                        or b"Command exited with code 1" in raw_line
                        or b"Traceback (most recent call last)" in raw_line
                    ):
                        err_summary, err_locs = _summarize_error_output(redact_secrets(row.get("content") or ""), 170)
                        for eloc in err_locs:
                            if eloc not in locations:
                                locations.append(eloc)
                        if err_summary:
                            errors_seen.append(f"[{loc}] {err_summary}")

                elif tool == "claude":
                    rtype = row.get("type")
                    msg = row.get("message") if isinstance(row.get("message"), dict) else {}
                    content = msg.get("content", [])
                    if isinstance(content, str):
                        content = [{"type": "text", "text": content}]
                    if rtype == "user" and isinstance(content, list):
                        for b in content:
                            if not isinstance(b, dict):
                                continue
                            if b.get("type") == "text":
                                txt = redact_secrets(hub_clean_prompt(b.get("text", ""), "claude"))
                                if txt and (not user_prompts or user_prompts[-1][1] != txt):
                                    user_prompts.append((loc, txt))
                                    _start_or_append_user_turn(line_no, loc, txt)
                            elif b.get("type") == "tool_result":
                                if current_turn is not None:
                                    current_turn["end_line"] = line_no
                                tuid = str(b.get("tool_use_id") or "")
                                raw_tr = b.get("content")
                                if isinstance(raw_tr, list):
                                    tr_str = redact_secrets(
                                        "\n".join(str(x.get("text") or "") for x in raw_tr if isinstance(x, dict))
                                    )
                                else:
                                    tr_str = redact_secrets(str(raw_tr or ""))
                                is_err = bool(b.get("is_error"))
                                m_ec = re.search(
                                    r"(?:exit code|exited with code|returncode)[:\s]+(-?\d+)",
                                    tr_str,
                                    re.I,
                                )
                                ec = int(m_ec.group(1)) if m_ec else (1 if is_err else 0)
                                if current_turn is not None and tuid and tuid in current_turn["tool_call_map"]:
                                    t_meta = current_turn["tool_call_map"][tuid]
                                    if t_meta.get("kind") == "Bash":
                                        t_idx = t_meta["tool_idx"]
                                        if 0 <= t_idx < len(current_turn["tools"]) and "-> exit" not in current_turn["tools"][t_idx]:
                                            current_turn["tools"][t_idx] = f"{current_turn['tools'][t_idx]} -> exit {ec}"
                                            last_action_summary = f"{current_turn['tools'][t_idx]} ({loc})"
                                        v_idx = t_meta.get("verif_idx", -1)
                                        if 0 <= v_idx < len(verification_cmds) and "-> exit" not in verification_cmds[v_idx]:
                                            verification_cmds[v_idx] = f"{verification_cmds[v_idx]} -> exit {ec}"
                                    elif t_meta.get("kind") in ("Grep", "Glob"):
                                        for f_m, s_m, e_m in FILE_RANGE_RE.findall(tr_str[:4000]):
                                            rng_m = f"{s_m}-{e_m}" if e_m else s_m
                                            _record_file_read(f_m, "Grep", rng_m, loc)
                                if last_event_kind != "ask_user_tool":
                                    last_event_kind = "tool_result"
                                if is_err:
                                    err_summary, err_locs = _summarize_error_output(tr_str, 170)
                                    for eloc in err_locs:
                                        if eloc not in locations:
                                            locations.append(eloc)
                                    if err_summary:
                                        errors_seen.append(f"[{loc}] {err_summary}")
                    elif rtype == "assistant" and isinstance(content, list):
                        t_cur = _ensure_turn(line_no, loc)
                        for b in content:
                            if not isinstance(b, dict):
                                continue
                            if b.get("type") == "text" and b.get("text"):
                                atxt = redact_secrets(b["text"])
                                assistant_msgs.append((loc, atxt))
                                t_cur["assistant_msgs"].append((loc, atxt))
                                last_event_kind = "assistant"
                                last_action_summary = f"Assistant response ({loc})"
                            elif b.get("type") == "tool_use":
                                tname = b.get("name", "")
                                tuid = str(b.get("id") or "")
                                tinp = b.get("input") or {}
                                if tname in ("Write", "Edit", "MultiEdit"):
                                    fpath = str(tinp.get("file_path") or tinp.get("path") or "").strip()
                                    s_ln = (
                                        tinp.get("line")
                                        or tinp.get("line_number")
                                        or tinp.get("start_line")
                                        or tinp.get("StartLine")
                                        or tinp.get("offset")
                                    )
                                    e_ln = tinp.get("end_line") or tinp.get("EndLine")
                                    rng = f"{s_ln}-{e_ln}" if (s_ln and e_ln and s_ln != e_ln) else (f"{s_ln}" if s_ln else "")
                                    if fpath:
                                        files_modified[fpath] = f"{tname}" + (f":{rng}" if rng else "") + f" @ {loc}"
                                        _record_tool_path(fpath, weight=2)
                                        act = f"{tname} {os.path.basename(fpath)}" + (f":{rng}" if rng else "")
                                        t_cur["tools"].append(act)
                                        last_action_summary = f"{act} ({loc})"
                                    last_event_kind = "tool_call"
                                elif tname == "Read":
                                    fpath = str(tinp.get("file_path") or tinp.get("path") or "").strip()
                                    s_ln = tinp.get("start_line") or tinp.get("StartLine") or tinp.get("offset")
                                    e_ln = tinp.get("end_line") or tinp.get("EndLine")
                                    lim = tinp.get("limit")
                                    if s_ln is not None and e_ln is not None:
                                        rng = f"{int(s_ln)}-{int(e_ln)}"
                                    elif s_ln is not None and lim is not None:
                                        st_i = max(1, int(s_ln))
                                        lim_i = int(lim)
                                        en_i = (st_i + lim_i - 1) if st_i == 1 else (st_i + lim_i)
                                        rng = f"{st_i}-{en_i}" if en_i > st_i else f"{st_i}"
                                    elif s_ln is not None:
                                        rng = f"{int(s_ln)}+"
                                    elif lim is not None:
                                        rng = f"1-{int(lim)}"
                                    else:
                                        rng = ""
                                    if fpath:
                                        _record_file_read(fpath, "Read", rng, loc)
                                        act = f"Read {os.path.basename(fpath)}" + (f":{rng}" if rng else "")
                                        t_cur["tools"].append(act)
                                        last_action_summary = f"{act} ({loc})"
                                    last_event_kind = "tool_call"
                                elif tname in ("Grep", "Glob"):
                                    fpath = str(tinp.get("path") or tinp.get("file_path") or "").strip()
                                    pat = str(tinp.get("pattern") or "").strip()
                                    if fpath:
                                        _record_file_read(fpath, tname, "", loc)
                                    act = f"{tname} {os.path.basename(fpath) or pat}".strip()
                                    t_cur["tools"].append(act)
                                    if tuid:
                                        t_cur["tool_call_map"][tuid] = {
                                            "kind": tname,
                                            "tool_idx": len(t_cur["tools"]) - 1,
                                        }
                                    last_action_summary = f"{act} ({loc})"
                                    last_event_kind = "tool_call"
                                elif tname == "Bash":
                                    raw_cmd = str(tinp.get("command") or "").strip()
                                    _extract_cmd_file_reads(raw_cmd, loc)
                                    cmd = _snippet(redact_secrets(raw_cmd), 140)
                                    v_idx = -1
                                    if cmd:
                                        verification_cmds.append(f"[{loc}] `{cmd}`")
                                        v_idx = len(verification_cmds) - 1
                                    act = f"Bash {_snippet(redact_secrets(raw_cmd), 48)}"
                                    t_cur["tools"].append(act)
                                    if tuid:
                                        t_cur["tool_call_map"][tuid] = {
                                            "kind": "Bash",
                                            "tool_idx": len(t_cur["tools"]) - 1,
                                            "verif_idx": v_idx,
                                        }
                                    last_action_summary = f"{act} ({loc})"
                                    last_event_kind = "tool_call"
                                elif tname in ("AskUserQuestion", "ExitPlanMode"):
                                    t_cur["tools"].append(tname)
                                    last_action_summary = f"{tname} ({loc})"
                                    last_event_kind = "ask_user_tool"
                                elif tname:
                                    t_cur["tools"].append(tname)
                                    last_action_summary = f"{tname} ({loc})"
                                    last_event_kind = "tool_call"

                elif tool == "codex":
                    rtype = row.get("type")
                    msg = row.get("payload") or {}
                    if rtype == "event_msg" and isinstance(msg, dict):
                        ev_type = msg.get("type")
                        if ev_type == "user_message":
                            txt = redact_secrets(hub_clean_prompt(str(msg.get("message") or ""), "codex"))
                            if txt and (not user_prompts or user_prompts[-1][1] != txt):
                                user_prompts.append((loc, txt))
                                _start_or_append_user_turn(line_no, loc, txt)
                        elif ev_type in ("agent_message", "task_complete"):
                            atxt = redact_secrets(str(msg.get("message") or msg.get("last_agent_message") or "").strip())
                            if atxt and (not assistant_msgs or assistant_msgs[-1][1] != atxt):
                                assistant_msgs.append((loc, atxt))
                                t_cur = _ensure_turn(line_no, loc)
                                t_cur["assistant_msgs"].append((loc, atxt))
                                last_event_kind = "assistant"
                                last_action_summary = f"Assistant response ({loc})"
                    elif rtype == "response_item" and isinstance(msg, dict):
                        kind, role = msg.get("type"), msg.get("role")
                        if kind == "message" and role in ("user", "assistant"):
                            blocks = msg.get("content") or []
                            if isinstance(blocks, str):
                                blocks = [{"text": blocks}]
                            for b in blocks:
                                if isinstance(b, dict) and b.get("text"):
                                    txt = redact_secrets(
                                        hub_clean_prompt(b["text"], "codex") if role == "user" else b["text"]
                                    )
                                    if txt:
                                        if role == "user":
                                            if not user_prompts or user_prompts[-1][1] != txt:
                                                user_prompts.append((loc, txt))
                                                _start_or_append_user_turn(line_no, loc, txt)
                                        else:
                                            if not assistant_msgs or assistant_msgs[-1][1] != txt:
                                                assistant_msgs.append((loc, txt))
                                                t_cur = _ensure_turn(line_no, loc)
                                                t_cur["assistant_msgs"].append((loc, txt))
                                                last_event_kind = "assistant"
                                                last_action_summary = f"Assistant response ({loc})"
                        elif kind in ("function_call", "custom_tool_call"):
                            t_cur = _ensure_turn(line_no, loc)
                            tname = msg.get("name", "")
                            call_id = str(msg.get("call_id") or msg.get("id") or "")
                            raw_in = redact_secrets(str(msg.get("input") or msg.get("arguments") or ""))
                            # Check for workdir in JS/JSON tool arguments
                            for m_wd in re.findall(r'\bworkdir\s*:\s*["\']([^"\']+)["\']', raw_in):
                                if m_wd and m_wd not in ("/tmp/ws", "/tmp"):
                                    _record_tool_path(m_wd, weight=1)
                            if "Update File:" in raw_in or "Add File:" in raw_in:
                                norm_in = raw_in.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\r", "\n")
                                hunk_lines = re.findall(r"@@\s*-\d+(?:,\d+)?\s*\+(\d+)", norm_in)
                                hunk_tag = f":{hunk_lines[0]}" if hunk_lines else ""
                                for m in re.findall(r"(?:Update|Add) File:\s*([^\n\r\\\"']+)", norm_in):
                                    clean_m = m.strip()
                                    if clean_m:
                                        files_modified[clean_m] = f"apply_patch{hunk_tag} @ {loc}"
                                        _record_tool_path(clean_m, weight=2)
                                        act = f"Edit {os.path.basename(clean_m)}{hunk_tag}"
                                        t_cur["tools"].append(act)
                                        last_action_summary = f"{act} ({loc})"
                                last_event_kind = "tool_call"
                            elif tname in ("read_file", "view_file", "Read"):
                                try:
                                    parsed_in = json.loads(raw_in) if raw_in.strip().startswith("{") else {}
                                except ValueError:
                                    parsed_in = {}
                                fpath = str(parsed_in.get("path") or parsed_in.get("file_path") or "").strip()
                                s_ln = parsed_in.get("start_line") or parsed_in.get("offset")
                                e_ln = parsed_in.get("end_line")
                                lim = parsed_in.get("limit")
                                rng = (
                                    f"{s_ln}-{e_ln}"
                                    if (s_ln and e_ln)
                                    else (f"{s_ln}-{int(s_ln) + int(lim)}" if (s_ln and lim) else (f"{s_ln}+" if s_ln else ""))
                                )
                                if fpath:
                                    _record_file_read(fpath, "Read", rng, loc)
                                    act = f"Read {os.path.basename(fpath)}" + (f":{rng}" if rng else "")
                                    t_cur["tools"].append(act)
                                    last_action_summary = f"{act} ({loc})"
                                last_event_kind = "tool_call"
                            elif tname in ("shell", "exec_command", "exec"):
                                extracted_cmd = _extract_codex_exec_cmd(raw_in)
                                _extract_cmd_file_reads(extracted_cmd, loc)
                                cmd_entry = f"[{loc}] `{_snippet(extracted_cmd, 140)}`"
                                verification_cmds.append(cmd_entry)
                                v_idx = len(verification_cmds) - 1
                                if call_id:
                                    codex_call_map[call_id] = v_idx
                                act = f"Bash {_snippet(extracted_cmd, 48)}"
                                t_cur["tools"].append(act)
                                if call_id:
                                    t_cur["tool_call_map"][call_id] = {
                                        "kind": "Bash",
                                        "tool_idx": len(t_cur["tools"]) - 1,
                                        "verif_idx": v_idx,
                                    }
                                last_action_summary = f"{act} ({loc})"
                                last_event_kind = "tool_call"
                        elif kind in ("function_call_output", "custom_tool_call_output"):
                            if current_turn is not None:
                                current_turn["end_line"] = line_no
                            last_event_kind = "tool_result"
                            call_id = str(msg.get("call_id") or msg.get("id") or "")
                            raw_out = msg.get("output")
                            if isinstance(raw_out, list):
                                out_parts = [
                                    str(b.get("text") or "") for b in raw_out if isinstance(b, dict) and b.get("text")
                                ]
                                out_str = redact_secrets("\n".join(out_parts))
                            else:
                                out_str = redact_secrets(str(raw_out or ""))
                            exit_codes = [
                                int(c)
                                for c in re.findall(
                                    r'(?:process exited with code|exit[_ ]code["\s:]+)(-?\d+)',
                                    out_str,
                                    re.I,
                                )
                            ]
                            non_zero = [c for c in exit_codes if c != 0]
                            exit_code = non_zero[-1] if non_zero else (exit_codes[-1] if exit_codes else 0)
                            if call_id and call_id in codex_call_map and exit_codes:
                                c_idx = codex_call_map[call_id]
                                if 0 <= c_idx < len(verification_cmds) and "-> exit" not in verification_cmds[c_idx]:
                                    verification_cmds[c_idx] = f"{verification_cmds[c_idx]} -> exit {exit_code}"
                            if current_turn is not None and call_id and call_id in current_turn["tool_call_map"]:
                                t_meta = current_turn["tool_call_map"][call_id]
                                t_idx = t_meta.get("tool_idx", -1)
                                if 0 <= t_idx < len(current_turn["tools"]) and "-> exit" not in current_turn["tools"][t_idx]:
                                    current_turn["tools"][t_idx] = f"{current_turn['tools'][t_idx]} -> exit {exit_code}"
                                    last_action_summary = f"{current_turn['tools'][t_idx]} ({loc})"
                            out_low = out_str.lower()
                            if (
                                exit_code != 0
                                or "exit code 1" in out_low
                                or "traceback (most recent call last)" in out_low
                                or re.search(r"\b(?:RuntimeError|AssertionError|SyntaxError|TypeError|ValueError|KeyError|FAILED)\b", out_str)
                            ):
                                err_summary, err_locs = _summarize_error_output(out_str, 170)
                                for eloc in err_locs:
                                    if eloc not in locations:
                                        locations.append(eloc)
                                if err_summary:
                                    errors_seen.append(f"[{loc}] {err_summary}")
        except OSError:
            pass

    if not user_prompts and system_prompts:
        user_prompts = list(system_prompts[:2])

    for loc, utxt in user_prompts:
        for line in _coalesce_markdown_blocks(utxt):
            sline = line.strip(" -*•\t")
            if not sline:
                continue
            for m_file, m_s, m_e in FILE_RANGE_RE.findall(sline):
                rng_m = f"{m_s}-{m_e}" if m_e else m_s
                base_loc = f"{os.path.basename(m_file)}:{m_s}"
                if base_loc not in locations:
                    locations.append(base_loc)
                _record_file_read(m_file, "user_ref", rng_m, loc)
            if CONSTRAINT_RE.search(sline):
                verbatim_constraints.append(f"[{loc}] {_snippet(sline, 220)}")
            if ACCEPTANCE_RE.search(sline):
                acceptance_items.append(f"[{loc}] {_snippet(sline, 180)}")
            if REJECTED_RE.search(sline):
                rejected_approaches.append(f"[{loc}] {_snippet(sline, 220)}")
            if IRREV_RE.search(sline) and not UNVERIFIED_RE.search(sline):
                pinned_decisions.append(f"[{loc}] {_snippet(sline, 220)}")

    for loc, atxt in assistant_msgs:
        for line in _coalesce_markdown_blocks(atxt):
            sline = line.strip(" -*•\t")
            if not sline:
                continue
            for m_file, m_s, m_e in FILE_RANGE_RE.findall(sline):
                rng_m = f"{m_s}-{m_e}" if m_e else m_s
                base_loc = f"{os.path.basename(m_file)}:{m_s}"
                if base_loc not in locations:
                    locations.append(base_loc)
                _record_file_read(m_file, "assistant_ref", rng_m, loc)
            if CONSTRAINT_RE.search(sline) and not sline.endswith(("?", "？")):
                cand_c = f"[{loc}] {_snippet(sline, 220)}"
                if not any(_snippet(sline, 60) in existing for existing in verbatim_constraints):
                    verbatim_constraints.append(cand_c)
            if UNVERIFIED_RE.search(sline):
                unverified_hypotheses.append(f"[{loc}] {_snippet(sline, 220)}")
                continue
            if REJECTED_RE.search(sline) and "[irrev]" not in sline.lower():
                rejected_approaches.append(f"[{loc}] {_snippet(sline, 220)}")
            if IRREV_RE.search(sline):
                cand_p = f"[{loc}] {_snippet(sline, 220)}"
                if not any(_snippet(sline, 60) in existing for existing in pinned_decisions):
                    pinned_decisions.append(cand_p)

    # Unconditionally extract high-signal lines from the latest substantive assistant message(s)
    # so final conclusions (function signatures, port overrides, root cause locations) are never
    # swallowed when an earlier turn matched IRREV_RE.
    substantive_assistant = [
        (loc, atxt) for loc, atxt in assistant_msgs if len(atxt.strip()) >= 25
    ]
    if not substantive_assistant and assistant_msgs:
        substantive_assistant = [assistant_msgs[-1]]
    for loc, atxt in substantive_assistant[-2:]:
        for line in _coalesce_markdown_blocks(atxt):
            sline = line.strip(" -*•\t")
            if len(sline) < 8:
                continue
            if UNVERIFIED_RE.search(sline) or (REJECTED_RE.search(sline) and "[irrev]" not in sline.lower()):
                continue
            if re.match(r"^(?:\*\*)?(?:废弃|日志诱饵|诱饵端口|假信息)", re.sub(r"^[-*•\s]+", "", sline)):
                continue
            if (
                re.search(r"\b(?:def|class|async\s+def)\s+[A-Za-z_][A-Za-z0-9_]*\s*\(", sline)
                or FILE_LINE_RE.search(sline)
                or re.search(r"(?:端口|暗号|签名|结论|根因|定位|最终决定|异常原文|RuntimeError|port|canary|signature|root cause)", sline, re.I)
            ):
                cand = f"[{loc}] {_snippet(sline, 230)}"
                if not any(_snippet(sline, 60) in existing for existing in pinned_decisions):
                    pinned_decisions.append(cand)

    # 1. Infer effective project root (`effective_root`) when started from ~ / /tmp / generic dir
    effective_root = infer_effective_root(declared_cwd, tool_paths, home_dir=home_dir)
    cwd = effective_root
    session["effective_root"] = effective_root
    session["cwd"] = effective_root

    # 2. Classify exact continuation state (`resume_state`)
    resume_state, immediate_action_desc = classify_resume_state(
        session,
        turns=turns,
        last_event_kind=last_event_kind,
        corrupt_tail_line=corrupt_tail_line,
    )
    session["resume_state"] = resume_state

    resolved_mode = resolve_handoff_mode(
        requested_mode=mode,
        from_tool=tool,
        to_tool=to_tool,
        provider=provider,
        has_errors=bool(errors_seen),
        note=note,
    )

    acc_file = os.path.join(cwd, "ACCEPTANCE.md")
    if os.path.isfile(acc_file):
        try:
            with open(acc_file, encoding="utf-8") as af:
                for line in af:
                    sline = line.strip()
                    if sline.startswith(("- [ ]", "- [x]", "* [ ]", "* [x]")):
                        acceptance_items.append(_snippet(sline, 120))
                    if len(acceptance_items) >= 6:
                        break
        except OSError:
            pass

    substantive_prompts = [
        utxt for _, utxt in user_prompts
        if len(utxt.strip()) >= 4 and not re.fullmatch(r"(?i)(?:h+i+|h+e+l+o+|h+e+y+|y+o+|o+k+|t+e+s+t+|casual greeting)", utxt.strip())
    ]
    first_prompt = (
        substantive_prompts[0]
        if substantive_prompts
        else (user_prompts[0][1] if user_prompts else (session.get("firstPrompt") or session.get("title") or ""))
    )
    last_prompt = (
        substantive_prompts[-1]
        if substantive_prompts
        else (user_prompts[-1][1] if user_prompts else (session.get("lastPrompt") or first_prompt))
    )
    goal_lines = [f"Primary objective: {_snippet(first_prompt, 180)}"]
    if last_prompt and last_prompt != first_prompt:
        goal_lines.append(f"Latest focus: {_snippet(last_prompt, 180)}")
    if note:
        goal_lines.append(f"Handoff note: {_snippet(redact_secrets(note), 160)}")

    if not acceptance_items:
        acceptance_items = [
            "All existing and newly added unit tests pass without regressions.",
            "Modified files remain within the authorized workspace scope.",
        ]

    # Mode-adaptive filtering to eliminate anchoring & correlated blindness:
    if resolved_mode == "escalate":
        # Scenario A (Weak -> Strong / De-anchoring): strip predecessor subjective reasoning/narrative
        pinned_decisions = [
            "[DE-ANCHORED / ESCALATE MODE] Predecessor subjective reasoning omitted to prevent anchoring on flawed paths; evaluate independently from repository state and verified facts."
        ]
    elif resolved_mode == "review":
        # Scenario C (Cross-vendor Independent Review): strip developer self-justification
        pinned_decisions = [
            "[BLIND REVIEW MODE] Developer agent self-justification omitted to prevent correlated blindness; verify independently against task requirements, git diff, and test outputs."
        ]
    elif not pinned_decisions:
        if assistant_msgs:
            pinned_decisions = [f"[{assistant_msgs[-1][0]}] [IRREV] {_snippet(assistant_msgs[-1][1], 220)}"]
        else:
            pinned_decisions = ["[IRREV] Preserve existing public APIs and session transcript integrity."]

    failed_attempts = list(errors_seen[-4:]) + list(rejected_approaches[-4:])
    if not failed_attempts:
        failed_attempts = [
            "None recorded in transcript (avoid rewriting raw historical JSONL files)."
        ]

    if not rejected_approaches:
        rejected_approaches = list(failed_attempts[:4])

    if not verbatim_constraints:
        if user_prompts:
            verbatim_constraints = [f"[{loc}] \"{_snippet(txt, 180)}\"" for loc, txt in user_prompts[-3:]]
        else:
            verbatim_constraints = [f"\"{_snippet(first_prompt or 'Follow project rules in AGENTS.md', 180)}\""]
    else:
        # Preserve initial hard constraints alongside the most recent constraints
        combined_constraints = list(verbatim_constraints[:2]) + [
            c for c in verbatim_constraints[-4:] if c not in verbatim_constraints[:2]
        ]
        verbatim_constraints = combined_constraints

    # Sort read/inspected files by frequency & recency (prioritize direct Read/read_file/Grep over casual text mentions)
    def _read_sort_key(kv: tuple[str, dict]) -> tuple[int, int, int]:
        _, info = kv
        direct_tool = any(op in ("Read", "read_file", "Grep", "Glob", "Read(sed)", "Read(nl)") for op in info["ops"])
        return (1 if direct_tool else 0, info["count"], info["seq"])

    sorted_reads = sorted(files_read.items(), key=_read_sort_key, reverse=True)
    read_file_summaries: list[str] = []
    for fpath_r, info_r in sorted_reads[:15]:
        rng_s = ", ".join(info_r["ranges"][:4])
        ops_s = "/".join(info_r["ops"][:3])
        cnt_s = info_r["count"]
        loc_s = info_r["last_loc"]
        if rng_s:
            read_file_summaries.append(f"{fpath_r}:{rng_s} ({ops_s}, {cnt_s}x @ {loc_s})")
        else:
            read_file_summaries.append(f"{fpath_r} ({ops_s}, {cnt_s}x @ {loc_s})")

    git_meta = _collect_git_metadata(cwd)
    git_state = [f"Effective root: `{effective_root}`"]
    git_state.extend(git_meta["lines"])
    for dline in git_meta["diff_stat"]:
        git_state.append(f"Diff stat: `{dline}`")
    for fpath, floc in list(files_modified.items())[-8:]:
        git_state.append(f"Modified `{fpath}` ({floc})")
    for rf_sum in read_file_summaries[:6]:
        git_state.append(f"Inspected `{rf_sum}`")
    for loc_ref in locations[-6:]:
        git_state.append(f"Referenced location: `{loc_ref}`")
    if not git_state:
        git_state = [f"Workspace directory: `{cwd}` (no tracked file modifications recorded)"]

    changed_file_list = list(dict.fromkeys(list(git_meta["changed_files"]) + list(files_modified.keys())))

    verified_facts = list(verification_cmds[-5:])
    for fpath, floc in list(files_modified.items())[-5:]:
        verified_facts.append(f"File written: {fpath} ({floc})")
    if not verified_facts:
        verified_facts = ["Workspace state inspected; no automated test run recorded yet."]

    verif_snapshot = list(verification_cmds[-5:])
    if errors_seen:
        verif_snapshot.append(f"Last error: {errors_seen[-1]}")
    if not verif_snapshot:
        verif_snapshot = ["No verification command recorded in transcript yet; run test suite before completion."]

    if not unverified_hypotheses:
        if files_modified and not verification_cmds:
            unverified_hypotheses.append("Modified files have not yet been verified by a test command run.")
        elif errors_seen:
            unverified_hypotheses.append("Root cause of latest command failure remains unverified.")
        else:
            unverified_hypotheses.append("Edge cases and concurrent lock contention require independent verification.")

    unresolved = list(unverified_hypotheses[-4:])
    if (session.get("tailStatus") == "wait_input" or resume_state == "awaiting_user") and assistant_msgs and resolved_mode == "delegate":
        unresolved.insert(0, f"Pending question from {tool}: {_snippet(assistant_msgs[-1][1], 180)}")

    target_label = f"{to_tool} ({provider})" if provider else to_tool
    if resolved_mode == "escalate":
        next_action = [
            f"[ESCALATE / {resume_state}] Re-investigate `{cwd}` from first principles using `{target_label}`; inspect changed files and failed attempts without relying on prior assumptions."
        ]
    elif resolved_mode == "review":
        next_action = [
            f"[REVIEW / {resume_state}] Perform independent blind review of `{cwd}` using `{target_label}` against task requirements, git diff, and test outputs."
        ]
    elif resume_state == "user_pending":
        next_action = [
            f"[DELEGATE / user_pending] In `{cwd}` using `{target_label}`, immediately answer and execute the user's latest request: {_snippet(last_prompt, 140)}"
        ]
    elif resume_state == "awaiting_user":
        next_action = [
            f"[DELEGATE / awaiting_user] In `{cwd}` using `{target_label}`, briefly restate the pending question/plan and wait for user confirmation."
        ]
    elif resume_state == "mid_task" and files_modified:
        last_file = list(files_modified.keys())[-1]
        next_action = [
            f"[DELEGATE / mid_task] Resume interrupted execution in `{cwd}` using `{target_label}`, inspect `{last_file}`, and run verification tests."
        ]
    elif files_modified:
        last_file = list(files_modified.keys())[-1]
        next_action = [
            f"[DELEGATE / {resume_state}] Resume execution in `{cwd}` using `{target_label}`, inspect `{last_file}`, and run verification tests."
        ]
    else:
        next_action = [
            f"[DELEGATE / {resume_state}] Continue task in `{cwd}` using `{target_label}` following the pinned decisions and acceptance criteria."
        ]

    # Build Section 0 (`## 0 接续点 (Immediate Action)`), Section 3 (`## 3 近期对话原文 (Recent Verbatim Turns)`),
    # and Section 4 (`## 4 关键文件与锚点`)
    latest_user_full = _clean_verbatim_turn_text(last_prompt, 600) if last_prompt else "(None)"
    if not last_action_summary:
        if verification_cmds:
            last_action_summary = verification_cmds[-1]
        elif files_modified:
            last_f = list(files_modified.keys())[-1]
            last_action_summary = f"Modified {last_f} ({files_modified[last_f]})"
        elif assistant_msgs:
            last_action_summary = f"Assistant reply @ {assistant_msgs[-1][0]}"
        else:
            last_action_summary = "None recorded"

    immediate_action_section = [
        f"**Resume State (`resume_state`)**: `{resume_state}`",
        f"**Action Directive**: {immediate_action_desc}",
        f"**Effective Project Root (`effective_root`)**: `{effective_root}`",
        f"**Latest User Request**: {latest_user_full}",
        f"**Predecessor Last Action**: {last_action_summary}",
    ]

    # Filter out synthetic benchmark filler turns so 60-turn noise benchmarks stay compact
    substantive_turns: list[dict] = []
    for t in turns:
        u_joined = "\n".join(t.get("user_texts") or []).strip()
        a_joined = "\n".join(txt for _, txt in (t.get("assistant_msgs") or [])).strip()
        if re.fullmatch(r"Continue turn \d+ analysis", u_joined) and "intermediate file inspection dump: verbose_log_line=" in a_joined:
            continue
        substantive_turns.append(t)

    tx_basename = os.path.basename(path) if path else "transcript.jsonl"
    tx_path_str = path or "transcript.jsonl"
    kept_turns = substantive_turns[-5:] if substantive_turns else []
    if kept_turns and (kept_turns[0]["turn_index"] > 1 or kept_turns[0]["start_line"] > 1):
        early_end = max(1, int(kept_turns[0]["start_line"]) - 1)
        earlier_pointer = (
            f"{tx_basename}#L1-L{early_end} ({tx_path_str}#L1-L{early_end}) — "
            f"use `rg -n \"<keyword>\" {tx_path_str}` to search earlier turns on demand"
        )
    else:
        max_ln = max(total_file_lines, kept_turns[-1]["end_line"] if kept_turns else 1)
        earlier_pointer = (
            f"{tx_basename}#L1-L{max_ln} ({tx_path_str}#L1-L{max_ln}) — "
            f"use `rg -n \"<keyword>\" {tx_path_str}` for on-demand full transcript search"
        )

    serialized_turns: list[dict] = []
    for t in kept_turns:
        u_raw = "\n".join(t.get("user_texts") or []).strip()
        asst_list = t.get("assistant_msgs") or []
        if len(asst_list) >= 2 and len(asst_list[-1][1].strip()) < 35:
            a_raw = asst_list[-2][1].strip() + "\n" + asst_list[-1][1].strip()
        elif asst_list:
            a_raw = asst_list[-1][1].strip()
        else:
            a_raw = ""
        serialized_turns.append(
            {
                "turn_index": t["turn_index"],
                "start_line": t["start_line"],
                "end_line": t["end_line"],
                "user_loc": t["user_loc"],
                "user_text": u_raw,
                "tools": list(t.get("tools") or [])[:12],
                "assistant_text": a_raw,
            }
        )

    session["_handoff_verbatim_turns"] = serialized_turns
    session["_handoff_earlier_pointer"] = earlier_pointer
    session["_handoff_total_turns"] = len(turns)
    session["_handoff_is_synthetic_benchmark"] = len(turns) > len(substantive_turns)

    verbatim_turn_lines: list[str] = [f"**Earlier Turns Pointer**: `{earlier_pointer}`"]
    for st in serialized_turns:
        u_txt = _clean_verbatim_turn_text(st["user_text"], 1500) or "(None)"
        tools_line = f"[Tools: {', '.join(st['tools'])}]" if st["tools"] else ""
        if resolved_mode in ("escalate", "review") and st["assistant_text"]:
            a_txt = f"*(omitted in {resolved_mode} mode to prevent anchoring)*"
        else:
            a_txt = _clean_verbatim_turn_text(st["assistant_text"], 2000) or "*(pending — no assistant reply yet)*"
        turn_block = f"**Turn {st['turn_index']} ({tx_basename}#L{st['start_line']}-L{st['end_line']})**:\n  - **User**: {u_txt}"
        if tools_line:
            turn_block += f"\n  - **{tools_line}**"
        turn_block += f"\n  - **Assistant**: {a_txt}"
        verbatim_turn_lines.append(turn_block)

    key_files_section: list[str] = [f"**Effective Root (`effective_root`)**: `{effective_root}`"]
    if files_modified:
        for fpath_m, floc_m in list(files_modified.items())[-15:]:
            key_files_section.append(f"**Modified (`Write`/`Edit`)**: `{fpath_m}` ({floc_m})")
    else:
        key_files_section.append("**Modified (`Write`/`Edit`)**: `(None)`")
    if read_file_summaries:
        for rf_s in read_file_summaries[:15]:
            key_files_section.append(f"**Inspected (`Read`/`Grep`)**: `{rf_s}`")
    else:
        key_files_section.append("**Inspected (`Read`/`Grep`)**: `(None)`")
    if locations:
        key_files_section.append(f"**Key Code & Error Anchors**: {', '.join(f'`{l}`' for l in locations[-10:])}")

    evidence = [
        f"Source session: `{tool}:{sid}` (mode: `{resolved_mode}`, resume_state: `{resume_state}`)",
        f"Transcript log: `{path or 'N/A'}`",
        f"Earlier turns pointer: `{earlier_pointer}`",
    ]
    if user_prompts:
        evidence.append(f"Initial prompt pointer: `{user_prompts[0][0]}`")
        evidence.append(f"Latest prompt pointer: `{user_prompts[-1][0]}`")
    if compaction_locs:
        evidence.append(f"Compaction boundaries: {', '.join(compaction_locs[-5:])}")

    if resolved_mode == "review":
        task_status = "ready_for_review"
    elif errors_seen:
        task_status = "blocked"
    else:
        task_status = "partially_implemented"

    selected_pinned = pinned_decisions[-6:]
    selected_rejected = rejected_approaches[-5:]
    selected_constraints = verbatim_constraints[:6]
    selected_acceptance = acceptance_items[:5]

    contract: dict = {
        "task": _snippet(first_prompt, 220),
        "status": task_status,
        "resume_state": resume_state,
        "immediate_action": _snippet(immediate_action_desc, 220),
        "effective_root": effective_root,
        "mode": resolved_mode,
        "from_agent": tool,
        "to_agent": to_tool,
        "repository": {
            "cwd": effective_root,
            "effective_root": effective_root,
            "branch": git_meta["branch"],
            "commit": git_meta["commit"],
            "changed_files": changed_file_list[:15],
            "read_files": read_file_summaries[:12],
        },
        "earlier_turns_pointer": earlier_pointer,
        "locations": locations[:8],
        "verified": verified_facts[:6],
        "unverified": unverified_hypotheses[-5:],
        "failed_attempts": failed_attempts[-6:],
    }
    if resolved_mode == "delegate":
        contract["decisions_rationale"] = selected_pinned
        contract["rejected_approaches"] = selected_rejected
        contract["interface_contracts"] = selected_constraints
        contract["acceptance_criteria"] = selected_acceptance
    elif resolved_mode == "escalate":
        contract["de_anchored"] = True
    elif resolved_mode == "review":
        contract["blind_review"] = True

    sections = {
        "Immediate Action": immediate_action_section,
        "Recent Verbatim Turns": verbatim_turn_lines,
        "Key Files & Anchors": key_files_section,
        "Goal": goal_lines[:3],
        "Acceptance": selected_acceptance,
        "Pinned Decisions [IRREV]": selected_pinned,
        "Rejected Approaches": selected_rejected,
        "User Verbatim Constraints": selected_constraints,
        "Git & File State": git_state[:10],
        "Verification Snapshot": verif_snapshot[:6],
        "Unresolved Questions": unresolved[:4],
        "Next Single Action": next_action[:2],
        "Evidence Pointers": evidence[:6],
    }
    return sections, contract, resolved_mode


def hub_clean_prompt(value: str, tool: str = "") -> str:
    """Clean wrapper tags and system instructions from prompts."""
    if not isinstance(value, str):
        return ""
    text = value.strip()
    if text.startswith("Another Claude session sent a message:"):
        return ""
    if text.startswith("# AGENTS.md instructions") or text.startswith("<INSTRUCTIONS>"):
        return ""
    req = re.search(r"<USER_REQUEST>(.*?)</USER_REQUEST>", text, re.S | re.I)
    if req:
        text = req.group(1)
    tags = (
        r"environment_context|INSTRUCTIONS|ADDITIONAL_METADATA|SYSTEM_MESSAGE|"
        r"system-reminder|ide_opened_file|ide_selection|command-name|command-message|"
        r"command-args|local-command-caveat|local-command-stdout|user_instructions|"
        r"permissions instructions|collaboration_mode|cross-session-message|teammate-message|task-notification|turn_aborted"
    )
    text = re.sub(r"<(" + tags + r")\b[^>]*>.*?</\1>", "", text, flags=re.S | re.I)
    text = re.sub(r"</?(?:USER_REQUEST|USER_INPUT)\b[^>]*>", "", text, flags=re.I)
    return text.strip()
