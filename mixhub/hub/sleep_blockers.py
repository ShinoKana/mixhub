"""Sleep blockers: background tasks, timers, MCP / LSP helpers and child processes that keep a session awake."""
from __future__ import annotations

import json
import os
import re
import time

from mixhub.core.launch import AGY_FILTER_MODULE, command_runs, runs_module
from mixhub.hub.shell import _DEFAULT_RUN, run
from mixhub.hub.text import printable
from mixhub.hub.transcript import _parse_json_lines
from mixhub.hub.util import _file_sig, read_json, stamp
from mixhub.proc.classify import AGENT_CLI_NAMES, classify_proc_args, _is_headless_invocation, _split_argv_safe
from mixhub.proc.tree import parse_ps_snapshot


class SleepBlockersMixin:
    """Detect what must keep a session from auto-hibernating."""

    _TAIL_GUARD_BYTES = 256 * 1024
    _GENERIC_MCP_RUNNERS = frozenset({
        "node", "nodejs", "npm", "npx", "pnpm", "pnpx", "yarn", "bun", "bunx",
        "deno", "uv", "uvx", "python", "python3", "sh", "bash", "zsh", "env",
    })
    _AUX_SUBTREE_SUBSTRINGS = (
        "chrome-devtools-mcp",
        "@modelcontextprotocol/",
        "mcp-server",
        "mcp_server",
        "fastmcp",
        "mcp-proxy",
        "mcp-remote",
        ".cache/agy-chrome",
        "typescript-language-server",
        "tsserver",
        "pyright",
        "basedpyright",
        "gopls",
        "rust-analyzer",
        "clangd",
        "sourcekit-lsp",
        "bash-language-server",
        "vscode-html-language-server",
        "vscode-css-language-server",
        "vscode-json-language-server",
        "yaml-language-server",
        "lua-language-server",
        "jdtls",
        "pylsp",
    )
    _AUX_MCP_TOKEN_RE = re.compile(r"(?:^|[\s/._@-])mcp(?:$|[\s/._@=-])", re.I)
    _AUX_LSP_TOKEN_RE = re.compile(r"\b[\w-]+-(?:lsp|language-server|langserver)\b", re.I)
    _AUX_CODEX_APPSERVER_RE = re.compile(r"\bcodex(?:\.js|-[a-z0-9_-]+)?\s+app-server\b", re.I)
    _AGY_BG_TASK_START_RE = re.compile(r"Tool is running as a background task with task id:\s*([^\s\"'\\]+)")
    _AGY_TIMER_DESC_RE = re.compile(
        r"Task Description:\s*Timer:\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*(?:(\d+(?:\.\d+)?)\s*s)?",
        re.I,
    )
    _AGY_CRON_DESC_RE = re.compile(r"Task Description:\s*Cron:", re.I)
    _AGY_CREATED_AT_LINE_RE = re.compile(r"Created At:\s*(\S+)")
    _AGY_TASK_FINISHED_RE = re.compile(
        r'Task(?: id)?\s+["\']?([^"\'\s:]+)["\']?\s+(?:finished|was\s+cancel(?:l)?ed|cancel(?:l)?ed\.?|killed\.?)',
        re.I,
    )
    _AGY_TASK_STATUS_BLOCK_RE = re.compile(
        r"Task:\s*([^\s\n]+)\s*\nStatus:\s*(DONE|ERROR|FAILED|KILLED|CANCELED|CANCELLED)\b",
        re.I,
    )
    _AGY_MSG_SENDER_RE = re.compile(r"\[Message\].*?\bsender=([^\s]+)", re.S)

    @staticmethod
    def _clean_tool_arg_str(val) -> str:
        if val is None:
            return ""
        s = str(val).strip()
        while len(s) >= 2 and s[0] == s[-1] and s[0] in ('"', "'"):
            s = s[1:-1].strip()
        return s

    @classmethod
    def _clean_tool_arg_bool(cls, val) -> bool:
        if isinstance(val, bool):
            return val
        if isinstance(val, (int, float)):
            return bool(val)
        return cls._clean_tool_arg_str(val).lower() in ("true", "1", "yes")

    @classmethod
    def _clean_tool_arg_float(cls, val) -> float | None:
        s = cls._clean_tool_arg_str(val)
        if not s:
            return None
        try:
            return float(s)
        except (TypeError, ValueError):
            return None

    @classmethod
    def _task_id_matches(cls, candidate: str, known_ids: set[str]) -> bool:
        cand = cls._clean_tool_arg_str(candidate)
        if not cand or not known_ids:
            return False
        short = cand.rsplit("/", 1)[-1]
        if cand in known_ids or short in known_ids:
            return True
        return any(kid.endswith("/" + cand) or kid.endswith("/" + short) for kid in known_ids)

    def _configured_mcp_tokens(self) -> set[str]:
        cfg_paths = (
            self.path(".gemini/antigravity-cli/mcp_config.json"),
            self.path(".gemini/config/mcp_config.json"),
            self.path(".claude.json"),
            self.path(".claude/mcp.json"),
        )
        sigs = tuple(_file_sig(p) for p in cfg_paths)
        cached = getattr(self, "_mcp_tokens_cache", None)
        if cached is not None and cached[0] == sigs:
            return cached[1]
        tokens: set[str] = set()
        for p in cfg_paths:
            if not os.path.isfile(p):
                continue
            data = read_json(p, {})
            if not isinstance(data, dict):
                continue
            servers = data.get("mcpServers")
            if not isinstance(servers, dict):
                continue
            for srv in servers.values():
                if not isinstance(srv, dict):
                    continue
                cmd = str(srv.get("command") or "").strip()
                if cmd:
                    base = os.path.basename(cmd)
                    if base.lower() not in self._GENERIC_MCP_RUNNERS and len(base) >= 3:
                        tokens.add(base.lower())
                for raw_a in (srv.get("args") or ()):
                    a = str(raw_a or "").strip()
                    if not a or a.startswith("-"):
                        continue
                    pkg = a.split("@", 1)[0] if not a.startswith("@") else ("@" + a[1:].split("@", 1)[0])
                    pkg_base = os.path.basename(pkg)
                    if pkg_base.lower() not in self._GENERIC_MCP_RUNNERS and len(pkg_base) >= 4:
                        tokens.add(pkg.lower())
                        tokens.add(pkg_base.lower())
        self._mcp_tokens_cache = (sigs, tokens)
        return tokens

    @classmethod
    def _is_aux_subtree_root(cls, args: str, mcp_tokens: set[str]) -> bool:
        if not args:
            return False
        low = args.lower()
        if any(sub in low for sub in cls._AUX_SUBTREE_SUBSTRINGS):
            return True
        if cls._AUX_MCP_TOKEN_RE.search(low) or cls._AUX_LSP_TOKEN_RE.search(low) or cls._AUX_CODEX_APPSERVER_RE.search(low):
            return True
        if mcp_tokens and any(tok in low for tok in mcp_tokens):
            return True
        return False

    @staticmethod
    def _is_aux_single_process(args: str, is_descendant: bool) -> bool:
        if not args:
            return True
        if "agy_claude_filter.py" in args or "agy-claude" in args or runs_module(args, AGY_FILTER_MODULE):
            return True
        if "statusline.py" in args or "statusline.sh" in args:
            return True
        argv = _split_argv_safe(args)
        if not argv:
            return True
        idx = 0
        if os.path.basename(argv[0]) == "env":
            idx = 1
            while idx < len(argv) and ("=" in argv[idx] or argv[idx].startswith("-")):
                idx += 1
        else:
            while idx < len(argv) and "=" in argv[idx] and not argv[idx].startswith("-") and not argv[idx].startswith("/"):
                idx += 1
        if idx >= len(argv):
            return False
        exe_base = os.path.basename(argv[idx]).lstrip("-")
        tail = argv[idx + 1:]

        # System sleep-assertion helper `caffeinate` without a user utility command
        if exe_base == "caffeinate":
            i = 0
            while i < len(tail):
                tok = tail[i]
                if tok in ("-t", "-w"):
                    i += 2
                    continue
                if tok.startswith("-"):
                    i += 1
                    continue
                return False
            return True

        # Interactive Agent CLI itself (never headless `-p` / `exec` subagents or dispatch scripts)
        fam, kind, tail_argv = classify_proc_args(args)
        if kind == "agent" and fam in AGENT_CLI_NAMES and not _is_headless_invocation(fam, tail_argv):
            return True

        if not is_descendant:
            if command_runs(args, "tui") and any(flag in args for flag in ("--header", "--sidebar", "--SessionHub")):
                return True
            if exe_base == "login":
                return True
            if exe_base in ("zsh", "bash", "sh", "dash", "ksh", "fish"):
                if "trap : INT QUIT" in args:
                    return True
                if argv[idx].startswith("-") and "-c" not in tail and "-lc" not in tail and "-ic" not in tail:
                    return True
                if all(t.startswith("-") and "c" not in t for t in tail):
                    return True
        return False

    @staticmethod
    def _short_proc_cmd(args: str) -> str:
        s = " ".join(str(args or "").strip().split())
        return s[:48] if s else "unknown"

    @classmethod
    def _read_tail_json_rows(cls, path: str) -> list[dict]:
        try:
            with open(path, "rb") as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                if size > cls._TAIL_GUARD_BYTES:
                    f.seek(size - cls._TAIL_GUARD_BYTES, os.SEEK_SET)
                    chunk = f.read()
                    nl = chunk.find(b"\n")
                    if nl != -1:
                        chunk = chunk[nl + 1:]
                else:
                    f.seek(0, os.SEEK_SET)
                    chunk = f.read()
            return _parse_json_lines(chunk)
        except OSError:
            return []

    def _agy_transcript_sleep_blockers(self, path: str, now: float) -> tuple[list[str], bool]:
        """
        Scan the tail of an `agy` `transcript.jsonl` for:
          - Unfinished background tasks (`status == "RUNNING"` or `"Tool is running as a background task with task id: <id>"`
            without a subsequent completion / cancellation / kill record);
          - Active `schedule` cron jobs (`CronExpression` or `IsDaemon=True` not killed via `manage_task`);
          - Active `schedule` one-shot timers (`DurationSeconds` where `now < created_at + DurationSeconds + 60`
            and not yet fired or killed).
        Returns `(blockers, tail_is_benign_closed)`.
        """
        rows = self._read_tail_json_rows(path)
        if not rows:
            return [], False

        blockers: list[str] = []
        claimed_schedule_rows: set[int] = set()
        schedules: list[dict] = []

        for idx, row in enumerate(rows):
            r_created = stamp(row.get("created_at") or row.get("timestamp"))
            if not r_created:
                m_ca = self._AGY_CREATED_AT_LINE_RE.search(str(row.get("content") or ""))
                if m_ca:
                    r_created = stamp(m_ca.group(1))
            for tc in (row.get("tool_calls") or []):
                if not isinstance(tc, dict) or tc.get("name") != "schedule":
                    continue
                raw_args = tc.get("args") or {}
                if isinstance(raw_args, str):
                    try:
                        raw_args = json.loads(raw_args)
                    except Exception:
                        raw_args = {}
                if not isinstance(raw_args, dict):
                    raw_args = {}
                cron_expr = self._clean_tool_arg_str(
                    raw_args.get("CronExpression") or raw_args.get("cron_expression") or raw_args.get("cron")
                )
                is_daemon = self._clean_tool_arg_bool(
                    raw_args.get("IsDaemon") if "IsDaemon" in raw_args else raw_args.get("is_daemon")
                )
                dur_sec = self._clean_tool_arg_float(
                    raw_args.get("DurationSeconds") if "DurationSeconds" in raw_args else raw_args.get("duration_seconds")
                )
                timer_cond = self._clean_tool_arg_str(
                    raw_args.get("TimerCondition") or raw_args.get("timer_condition")
                )
                prompt_str = self._clean_tool_arg_str(raw_args.get("Prompt") or raw_args.get("prompt"))

                sched_ids: set[str] = set()
                for cand_id in (tc.get("id"), raw_args.get("TaskId"), raw_args.get("task_id")):
                    cleaned = self._clean_tool_arg_str(cand_id)
                    if cleaned:
                        sched_ids.add(cleaned)
                        sched_ids.add(cleaned.rsplit("/", 1)[-1])
                step_idx = row.get("step_index")
                if step_idx is not None:
                    sched_ids.add(str(step_idx))
                    sched_ids.add(f"task-{step_idx}")

                imm_cancelled = False
                eff_created = r_created
                for j in range(idx + 1, min(len(rows), idx + 4)):
                    if j in claimed_schedule_rows:
                        continue
                    nrow = rows[j]
                    if nrow.get("type") == "PLANNER_RESPONSE" and any(
                        isinstance( x, dict ) and x.get("name") == "schedule" for x in (nrow.get("tool_calls") or [])
                    ):
                        break
                    if nrow.get("type") != "GENERIC":
                        continue
                    ncontent = str(nrow.get("content") or "")
                    if "Wait cancelled" in ncontent or "already finished" in ncontent:
                        claimed_schedule_rows.add(j)
                        imm_cancelled = True
                        break
                    m_bg = self._AGY_BG_TASK_START_RE.search(ncontent)
                    if m_bg and ("Task Description: Timer:" in ncontent or "Task Description: Cron:" in ncontent or j == idx + 1):
                        claimed_schedule_rows.add(j)
                        bg_id = m_bg.group(1).strip()
                        sched_ids.add(bg_id)
                        sched_ids.add(bg_id.rsplit("/", 1)[-1])
                        g_step = nrow.get("step_index")
                        if g_step is not None:
                            sched_ids.add(str(g_step))
                            sched_ids.add(f"task-{g_step}")
                        if not eff_created:
                            eff_created = stamp(nrow.get("created_at") or nrow.get("timestamp"))
                        break
                    if j == idx + 1 and str(nrow.get("status") or "").upper() == "DONE" and any(
                        w in ncontent.lower() for w in ("cancelled", "canceled", "killed", "finished", "completed")
                    ):
                        claimed_schedule_rows.add(j)
                        imm_cancelled = True
                        break

                schedules.append({
                    "row_idx": idx,
                    "ids": sched_ids,
                    "is_cron": bool(cron_expr or is_daemon),
                    "dur_sec": dur_sec,
                    "created_ts": eff_created,
                    "timer_cond": timer_cond if timer_cond.lower() not in ("", "never") else "",
                    "prompt": prompt_str,
                    "closed": imm_cancelled,
                })

            # Also handle GENERIC schedule rows whose preceding PLANNER_RESPONSE tool_calls was truncated
            if idx not in claimed_schedule_rows and row.get("type") == "GENERIC":
                content_s = str(row.get("content") or "")
                m_bg = self._AGY_BG_TASK_START_RE.search(content_s)
                if m_bg:
                    m_timer = self._AGY_TIMER_DESC_RE.search(content_s)
                    m_cron = self._AGY_CRON_DESC_RE.search(content_s)
                    if m_timer or m_cron:
                        claimed_schedule_rows.add(idx)
                        bg_id = m_bg.group(1).strip()
                        s_ids = {bg_id, bg_id.rsplit("/", 1)[-1]}
                        if row.get("step_index") is not None:
                            s_ids.add(str(row["step_index"]))
                            s_ids.add(f"task-{row['step_index']}")
                        dur_s = None
                        if m_timer:
                            h_v = float(m_timer.group(1) or 0)
                            m_v = float(m_timer.group(2) or 0)
                            s_v = float(m_timer.group(3) or 0)
                            dur_s = h_v * 3600.0 + m_v * 60.0 + s_v
                        schedules.append({
                            "row_idx": idx,
                            "ids": s_ids,
                            "is_cron": bool(m_cron),
                            "dur_sec": dur_s,
                            "created_ts": r_created,
                            "timer_cond": "",
                            "prompt": "",
                            "closed": False,
                        })

        def _row_closes_task(row_k: dict, target_ids: set[str], allow_untargeted_kill: bool = False, timer_cond: str = "", prompt_str: str = "", is_cron: bool = False) -> bool:
            for tc in (row_k.get("tool_calls") or []):
                if not isinstance(tc, dict) or tc.get("name") != "manage_task":
                    continue
                m_args = tc.get("args") or {}
                if isinstance(m_args, str):
                    try:
                        m_args = json.loads(m_args)
                    except Exception:
                        m_args = {}
                if not isinstance(m_args, dict):
                    continue
                act = self._clean_tool_arg_str(m_args.get("Action") or m_args.get("action")).lower()
                tid = self._clean_tool_arg_str(m_args.get("TaskId") or m_args.get("task_id"))
                if act in ("kill", "cancel"):
                    if not tid or self._task_id_matches(tid, target_ids) or allow_untargeted_kill:
                        return True
            k_content = str(row_k.get("content") or "")
            if not k_content:
                return False
            for m_fin in self._AGY_TASK_FINISHED_RE.finditer(k_content):
                fid = m_fin.group(1)
                matched_text = m_fin.group(0).lower()
                if is_cron and "finished" in matched_text and "cancel" not in matched_text and "kill" not in matched_text:
                    continue
                if self._task_id_matches(fid, target_ids) or (timer_cond and self._task_id_matches(fid, {timer_cond, timer_cond.rsplit("/", 1)[-1]})):
                    return True
                if allow_untargeted_kill and any(w in matched_text for w in ("cancel", "kill", "finished")):
                    return True
            for m_blk in self._AGY_TASK_STATUS_BLOCK_RE.finditer(k_content):
                bid, bstat = m_blk.group(1), m_blk.group(2).upper()
                if is_cron and bstat == "DONE":
                    continue
                if self._task_id_matches(bid, target_ids):
                    return True
            if not is_cron and row_k.get("type") == "SYSTEM_MESSAGE":
                m_snd = self._AGY_MSG_SENDER_RE.search(k_content)
                if m_snd:
                    snd = m_snd.group(1).strip()
                    if self._task_id_matches(snd, target_ids) or (timer_cond and self._task_id_matches(snd, {timer_cond, timer_cond.rsplit("/", 1)[-1]})):
                        return True
                if prompt_str and len(prompt_str) >= 4 and prompt_str in k_content:
                    return True
            return False

        for sched in schedules:
            if sched["closed"]:
                continue
            s_idx = sched["row_idx"]
            s_ids = sched["ids"]
            has_explicit_task_id = any("/" in x for x in s_ids)
            allow_untargeted = (not has_explicit_task_id) and len(schedules) == 1
            for k in range(s_idx + 1, len(rows)):
                if _row_closes_task(
                    rows[k],
                    s_ids,
                    allow_untargeted_kill=allow_untargeted,
                    timer_cond=sched["timer_cond"],
                    prompt_str=sched["prompt"],
                    is_cron=sched["is_cron"],
                ):
                    sched["closed"] = True
                    break
            if sched["closed"]:
                continue
            if sched["is_cron"]:
                blockers.append("active_schedule_cron")
            elif sched["dur_sec"] is not None:
                c_ts = sched["created_ts"]
                if c_ts > 0:
                    if now < c_ts + sched["dur_sec"] + 60.0:
                        blockers.append("active_schedule_timer")
                    else:
                        sched["closed"] = True
                elif sched["dur_sec"] > 0:
                    blockers.append("active_schedule_timer")

        # Scan non-schedule background tasks (`run_command` / `status == "RUNNING"`)
        for idx, row in enumerate(rows):
            if idx in claimed_schedule_rows:
                continue
            content_s = str(row.get("content") or "")
            m_bg = self._AGY_BG_TASK_START_RE.search(content_s)
            is_running_status = str(row.get("status") or "").upper() == "RUNNING"
            if not m_bg and not is_running_status:
                continue
            if m_bg:
                bg_id = m_bg.group(1).strip()
            else:
                bg_id = self._clean_tool_arg_str(
                    row.get("task_id") or row.get("id") or (f"task-{row['step_index']}" if row.get("step_index") is not None else f"row-{idx}")
                )
            bg_ids = {bg_id, bg_id.rsplit("/", 1)[-1]}
            if row.get("step_index") is not None:
                bg_ids.add(str(row["step_index"]))
                bg_ids.add(f"task-{row['step_index']}")
            closed = False
            for k in range(idx + 1, len(rows)):
                if _row_closes_task(rows[k], bg_ids, allow_untargeted_kill=(not m_bg)):
                    closed = True
                    break
                if row.get("step_index") is not None and rows[k].get("step_index") == row.get("step_index"):
                    if str(rows[k].get("status") or "").upper() in ("DONE", "ERROR", "FAILED", "CANCELED", "CANCELLED", "KILLED"):
                        closed = True
                        break
            if not closed:
                blockers.append(f"active_bg_task:{bg_id}")

        last_row = rows[-1]
        last_type = last_row.get("type")
        tail_is_benign_closed = False
        if not blockers:
            if last_type in ("GENERIC", "SYSTEM_MESSAGE") and str(last_row.get("status") or "DONE").upper() != "RUNNING":
                tail_is_benign_closed = True
            elif last_type == "PLANNER_RESPONSE" and all(s.get("closed") for s in schedules) and schedules:
                tcalls = last_row.get("tool_calls") or []
                if tcalls and all(isinstance(tc, dict) and tc.get("name") in ("schedule", "manage_task") for tc in tcalls):
                    tail_is_benign_closed = True
        return blockers, tail_is_benign_closed

    def _claude_transcript_sleep_blockers(self, path: str, now: float) -> list[str]:
        rows = self._read_tail_json_rows(path)
        if not rows:
            return []
        bg_bash: dict[str, int] = {}
        monitors: dict[str, int] = {}
        crons: dict[str, int] = {}
        wakeups: list[tuple[float, float]] = []
        active_loop = False

        for idx, row in enumerate(rows):
            rtype = row.get("type")
            raw_str = json.dumps(row, ensure_ascii=False)
            if "<task-notification>" in raw_str:
                m_tid = re.search(r"<task-id>\s*([^<\s]+)\s*</task-id>", raw_str)
                if m_tid:
                    tid = m_tid.group(1).strip()
                    bg_bash.pop(tid, None)
                    monitors.pop(tid, None)
                else:
                    bg_bash.clear()
                    monitors.clear()
            if rtype == "user":
                wakeups.clear()
                msg = row.get("message") if isinstance(row.get("message"), dict) else {}
                content = msg.get("content", [])
                if isinstance(content, str):
                    content = [{"type": "text", "text": content}]
                tur = row.get("toolUseResult") if isinstance(row.get("toolUseResult"), dict) else {}
                for b in (content if isinstance(content, list) else []):
                    if not isinstance(b, dict):
                        continue
                    btype = b.get("type")
                    if btype == "text":
                        txt = str(b.get("text") or "").strip()
                        if txt.startswith("/loop ") or "<command-name>/loop</command-name>" in txt:
                            active_loop = True
                    elif btype == "tool_result":
                        tuid = str(b.get("tool_use_id") or "")
                        b_txt = printable(b.get("content", ""))
                        bg_task_id = str(tur.get("backgroundTaskId") or "")
                        if not bg_task_id:
                            m_bg = re.search(r"running in background with ID:\s*([^\s.,;\"']+)", b_txt, re.I)
                            if m_bg:
                                bg_task_id = m_bg.group(1)
                        if tuid in bg_bash and bg_task_id:
                            bg_bash.pop(tuid, None)
                            bg_bash[bg_task_id] = idx
                        elif tuid in bg_bash and any(w in b_txt.lower() for w in ("completed", "finished", "killed", "stopped", "canceled", "cancelled")) and "running in background" not in b_txt.lower():
                            bg_bash.pop(tuid, None)
            elif rtype == "assistant":
                msg = row.get("message") if isinstance(row.get("message"), dict) else {}
                content = msg.get("content", [])
                if isinstance(content, list):
                    for b in content:
                        if not isinstance(b, dict) or b.get("type") != "tool_use":
                            continue
                        tname = str(b.get("name") or "")
                        tid = str(b.get("id") or f"row-{idx}")
                        tinp = b.get("input") if isinstance(b.get("input"), dict) else {}
                        if tname == "Bash" and self._clean_tool_arg_bool(tinp.get("run_in_background")):
                            bg_bash[tid] = idx
                        elif tname == "Monitor":
                            monitors[tid] = idx
                        elif tname == "CronCreate":
                            crons[tid] = idx
                        elif tname == "CronDelete":
                            crons.clear()
                            active_loop = False
                        elif tname in ("TaskStop", "KillShell"):
                            stop_id = self._clean_tool_arg_str(tinp.get("task_id") or tinp.get("shell_id"))
                            if stop_id and (stop_id in bg_bash or stop_id in monitors):
                                bg_bash.pop(stop_id, None)
                                monitors.pop(stop_id, None)
                            else:
                                bg_bash.clear()
                                monitors.clear()
                        elif tname == "ScheduleWakeup":
                            delay_s = self._clean_tool_arg_float(tinp.get("delaySeconds"))
                            c_ts = stamp(row.get("timestamp") or row.get("created_at"))
                            if delay_s is not None:
                                wakeups.append((c_ts, delay_s))

        blockers: list[str] = []
        if bg_bash:
            blockers.append("claude_bg_bash")
        if monitors:
            blockers.append("claude_monitor")
        if crons:
            blockers.append("claude_cron")
        if active_loop:
            blockers.append("claude_loop")
        for c_ts, delay_s in wakeups:
            if (c_ts > 0 and now < c_ts + delay_s + 60.0) or (c_ts <= 0 and delay_s > 0):
                blockers.append("claude_schedule_wakeup")
                break
        return blockers

    def _proc_tree_sleep_blockers(self, session: dict, proc_table: dict) -> list[str]:
        nodes: dict[int, tuple[int, str, str]] = {}
        children_by_ppid: dict[int, list[int]] = {}
        pids_by_tty: dict[str, list[int]] = {}
        for raw_k, raw_v in proc_table.items():
            if not isinstance(raw_v, dict):
                continue
            try:
                pid = int(raw_v.get("pid") if raw_v.get("pid") is not None else raw_k)
                ppid = int(raw_v.get("ppid") or 0)
            except (TypeError, ValueError):
                continue
            if pid <= 0:
                continue
            args = str(raw_v.get("args") or raw_v.get("command") or "").strip()
            tty = str(raw_v.get("tty") or "").strip().removeprefix("/dev/")
            nodes[pid] = (ppid, tty, args)
            if ppid > 1 and pid != ppid:
                children_by_ppid.setdefault(ppid, []).append(pid)
            if tty and tty not in ("??", "?"):
                pids_by_tty.setdefault(tty, []).append(pid)

        if not nodes:
            return []

        session_pids: set[int] = set()
        session_ttys: set[str] = set()
        for p in (session.get("processes") or []):
            if not isinstance(p, dict):
                continue
            try:
                spid = int(p.get("pid") or 0)
            except (TypeError, ValueError):
                spid = 0
            if spid > 1:
                session_pids.add(spid)
                if spid in nodes and nodes[spid][1] and nodes[spid][1] not in ("??", "?"):
                    session_ttys.add(nodes[spid][1])
            ptty = str(p.get("tty") or "").strip().removeprefix("/dev/")
            if ptty and ptty not in ("??", "?"):
                session_ttys.add(ptty)
            for wtty in (p.get("wrapper_ttys") or ()):
                wtty_c = str(wtty or "").strip().removeprefix("/dev/")
                if wtty_c and wtty_c not in ("??", "?"):
                    session_ttys.add(wtty_c)

        ancestor_pids: set[int] = set(session_pids)
        for spid in session_pids:
            cur = spid
            for _ in range(64):
                info = nodes.get(cur)
                if not info:
                    break
                par = info[0]
                if par <= 1 or par in ancestor_pids:
                    break
                ancestor_pids.add(par)
                par_info = nodes.get(par)
                if par_info and par_info[1] and par_info[1] not in ("??", "?") and (
                    "agy_claude_filter" in par_info[2] or "agy-claude" in par_info[2]
                ):
                    session_ttys.add(par_info[1])
                cur = par

        mcp_tokens = self._configured_mcp_tokens()
        aux_subtree_pids: set[int] = set()
        for pid, (_, _, args) in nodes.items():
            if pid in session_pids:
                continue
            if self._is_aux_subtree_root(args, mcp_tokens):
                stack = [pid]
                while stack:
                    cur_p = stack.pop()
                    if cur_p in aux_subtree_pids:
                        continue
                    aux_subtree_pids.add(cur_p)
                    stack.extend(children_by_ppid.get(cur_p, ()))

        blockers: list[str] = []
        descendant_set: set[int] = set()
        queue: list[int] = []
        for spid in sorted(session_pids):
            queue.extend(sorted(children_by_ppid.get(spid, ())))
        while queue:
            cid = queue.pop(0)
            if cid in descendant_set or cid in session_pids:
                continue
            descendant_set.add(cid)
            if cid in aux_subtree_pids:
                continue
            _, _, c_args = nodes.get(cid, (0, "", ""))
            if not self._is_aux_single_process(c_args, is_descendant=True):
                blockers.append(f"child_proc:{cid}:{self._short_proc_cmd(c_args)}")
            queue.extend(sorted(children_by_ppid.get(cid, ())))

        for tty in sorted(session_ttys):
            for tpid in sorted(pids_by_tty.get(tty, ())):
                if tpid <= 1 or tpid in session_pids or tpid in ancestor_pids or tpid in descendant_set or tpid in aux_subtree_pids:
                    continue
                _, _, t_args = nodes.get(tpid, (0, "", ""))
                if self._is_aux_single_process(t_args, is_descendant=False):
                    continue
                blockers.append(f"child_proc:{tpid}:{self._short_proc_cmd(t_args)}")

        return blockers

    def sleep_blockers(self, session: dict, proc_table: dict | None = None) -> list[str]:
        """
        Return a list of active blockers preventing `session` from being auto-hibernated by `gc_idle_resident`:
          - G0: `agentState` not idle (`working`, `wait_input`, etc.), `activeSubagents > 0`, `notFullyIdle`,
                or unclosed `tailStatus` (`ball_in_model`, `unclosed_tool`).
          - G1: Tail transcript inspection (last 256 KiB) for in-flight background command tasks, active
                `schedule` cron jobs (`active_schedule_cron`), or pending `schedule` timers (`active_schedule_timer`).
          - G2: Process tree descendant + same-TTY process whitelist check against `proc_table`
                (allowing Agent CLI wrappers, MCP servers and their Chrome/helper subtrees, LSP servers,
                `caffeinate`, `codex app-server`, and `statusline.py`, while blocking any user task process
                such as `python`, `bash -c`, `ssh`, `node`, `sleep`, etc.).
        """
        if not isinstance(session, dict):
            return ["invalid_session"]
        now = time.time()
        blockers: list[str] = []

        # G0: State field guards
        agent_state = session.get("agentState")
        if agent_state in ("working", "wait_input") or agent_state not in (None, "", "done_idle"):
            blockers.append(f"agent_state:{agent_state}")
        active_subs = int(session.get("activeSubagents") or 0)
        if active_subs > 0:
            blockers.append(f"active_subagents:{active_subs}")
        if bool(session.get("notFullyIdle")):
            blockers.append("not_fully_idle")

        # G1: Transcript tail pending-task & schedule scan
        tool = str(session.get("tool") or "")
        sid = str(session.get("id") or "")
        tpath = session.get("path")
        if (not tpath or not os.path.isfile(str(tpath))) and sid:
            if tool == "agy":
                cand = self.path(f".gemini/antigravity-cli/brain/{sid}/.system_generated/logs/transcript.jsonl")
                if os.path.isfile(cand):
                    tpath = cand
            elif tool == "claude":
                existing = self.sessions.get(f"claude:{sid}")
                if isinstance(existing, dict) and existing.get("path") and os.path.isfile(str(existing["path"])):
                    tpath = existing["path"]

        tail_benign_closed = False
        if tpath and os.path.isfile(str(tpath)):
            if tool == "agy":
                g1_blockers, tail_benign_closed = self._agy_transcript_sleep_blockers(str(tpath), now)
                blockers.extend(g1_blockers)
            elif tool == "claude":
                blockers.extend(self._claude_transcript_sleep_blockers(str(tpath), now))

        tail_status = session.get("tailStatus")
        if tail_status in ("ball_in_model", "unclosed_tool"):
            if not (agent_state == "done_idle" and tail_benign_closed):
                blockers.append(f"tail_status:{tail_status}")

        # G2: Process tree & same-TTY child/descendant whitelist scan
        if proc_table is None:
            if getattr(self, "_last_proc_table", None):
                proc_table = self._last_proc_table
            elif "processes" not in self.__dict__ or run is not _DEFAULT_RUN:
                try:
                    raw_ps = run(["ps", "-axo", "pid,ppid,tty,rss,etime,lstart,command"])
                    proc_table = parse_ps_snapshot(raw_ps)
                    if not proc_table:
                        blockers.append("proc_table_empty")
                        return list(dict.fromkeys(blockers))
                    self._last_proc_table = proc_table
                except Exception:
                    blockers.append("proc_table_error")
                    return list(dict.fromkeys(blockers))
            else:
                proc_table = getattr(self, "_last_proc_table", None)
                if proc_table is None:
                    blockers.append("proc_table_error")
                    return list(dict.fromkeys(blockers))

        if isinstance(proc_table, dict) and proc_table and session.get("processes"):
            blockers.extend(self._proc_tree_sleep_blockers(session, proc_table))

        return list(dict.fromkeys(blockers))
