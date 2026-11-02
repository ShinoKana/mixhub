"""Text primitives for handoff extraction: marker regexes, secret redaction, token estimation and transcript cleanup."""
from __future__ import annotations

import json
import os
import re


SECRET_PATTERNS = (
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"), "sk-[REDACTED]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), "gh_[REDACTED]"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{16,}\b"), "xox-[REDACTED]"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{25,}\b"), "AIza[REDACTED]"),
    (re.compile(r"\b\d{8,12}:[A-Za-z0-9_-]{30,}\b"), "[TG_BOT_TOKEN_REDACTED]"),
    (
        re.compile(
            r"(?i)\b(api[_-]?key|token|secret|password|authorization)\b(\s*[:=]\s*['\"]?)([A-Za-z0-9._/-]{12,})(['\"]?)"
        ),
        r"\1\2[REDACTED]\4",
    ),
)

IRREV_RE = re.compile(
    r"(\[IRREV\]|决策不动点|拍板|决定采用|最终决定|不可逆|Pinned Decision|Architecture Decision"
    r"|(?<!暂)定为|(?<!暂)设为|改用|选定|确定使用|别再动了|最终方案|握手暗号|监听端口|函数签名)",
    re.I,
)
REJECTED_RE = re.compile(
    r"(已否决|否决方案|不采用|放弃方案|放弃\s*[A-Za-z0-9_\u4e00-\u9fa5]|Rejected|Do not use|Deprecated approach|不用.*方案)",
    re.I,
)
CONSTRAINT_RE = re.compile(
    r"(严禁|不许|绝不|必须|硬规矩|硬约束|不要|禁止|never\b|must not\b|do not\b|strictly)",
    re.I,
)
ACCEPTANCE_RE = re.compile(
    r"(验收|完成标准|Acceptance|测试通过|单测|unittest|pytest|verify)",
    re.I,
)
UNVERIFIED_RE = re.compile(
    r"(未验证|尚未验证|待验证|疑似|可能存在|假设|unverified|may remain|race condition|edge case|TODO|FIXME)",
    re.I,
)
FILE_LINE_RE = re.compile(
    r"\b([A-Za-z0-9_./-]+\.(?:py|js|ts|tsx|jsx|rs|go|sql|sh|c|cpp|h|java|rb|toml|yaml|yml|json):(\d+))\b"
)
FILE_RANGE_RE = re.compile(
    r"\b([A-Za-z0-9_./-]+\.(?:py|js|ts|tsx|jsx|rs|go|sql|sh|c|cpp|h|java|rb|toml|yaml|yml|json|md)):(\d+)(?:-(\d+))?\b"
)


def _clean_verbatim_turn_text(text: str, max_chars: int) -> str:
    """Clean and bound a single turn's user or assistant text while preserving head and tail context."""
    s = redact_secrets(str(text or "").strip())
    if not s:
        return ""
    s = re.sub(r"verbose_log_line=[A-Za-z0-9+/=_-]{80,}", "[...dump omitted...]", s)
    s = re.sub(r"(?:NOISE_LOG_LINE_[A-Z0-9_]+\s*){3,}", "[...log noise omitted...]\n", s)
    s = re.sub(r"\b[A-Za-z0-9+/=]{260,}\b", "[...blob omitted...]", s)
    if len(s) <= max_chars:
        return s
    head_len = int(max_chars * 0.65)
    tail_len = max(80, max_chars - head_len - 24)
    return s[:head_len].rstrip() + "\n...[truncated]...\n" + s[-tail_len:].lstrip()



def _extract_codex_exec_cmd(raw_in: str) -> str:
    """Extract shell command from Codex `exec` JS tool literals or JSON arguments."""
    s = str(raw_in or "").strip()
    if not s:
        return ""
    if s.startswith("{"):
        try:
            parsed = json.loads(s)
            if isinstance(parsed, dict):
                cmd_val = parsed.get("cmd") or parsed.get("command")
                if isinstance(cmd_val, list):
                    return " ".join(str(x) for x in cmd_val)
                if isinstance(cmd_val, str) and cmd_val.strip():
                    return cmd_val.strip()
        except ValueError:
            pass
    matches = re.findall(r'\bcmd\s*:\s*"((?:\\.|[^"\\])*)"', s)
    if not matches:
        matches = re.findall(r"\bcmd\s*:\s*'((?:\\.|[^'\\])*)'", s)
    if matches:
        cleaned = [m.replace('\\"', '"').replace("\\'", "'").replace("\\n", " ").strip() for m in matches if m.strip()]
        if cleaned:
            return " ; ".join(cleaned)
    return s


def _coalesce_markdown_blocks(text: str) -> list[str]:
    """
    Coalesce Markdown header lines ending in ':' / '：' with their immediate short sub-bullets
    so multi-line items (e.g. a heading '5. 硬约束严禁修改的文件：' followed by '- legacy_v1_schema.sql',
    or '4. 被否决方案：' followed by '- 否决对象 / - 否决原因 / - 替代方案') stay together as a single
    semantic unit across multi-hop handoffs instead of fragmenting into orphan sub-bullets.
    """
    raw_lines = str(text or "").splitlines()
    out: list[str] = []
    i = 0
    n = len(raw_lines)
    while i < n:
        line = raw_lines[i].strip()
        if not line:
            i += 1
            continue
        clean_hdr = re.sub(r"[*_`]+", "", line).strip(" -*•\t")
        if clean_hdr.endswith((":", "：")) and i + 1 < n:
            subs: list[str] = []
            j = i + 1
            while j < n and len(subs) < 5:
                nxt_raw = raw_lines[j]
                nxt = nxt_raw.strip()
                if not nxt:
                    j += 1
                    continue
                if re.match(r"^(?:[-*•]|\d+[.)])\s+", nxt):
                    # Stop if next line is another top-level numbered section
                    if re.match(r"^\d+[.)]\s+", nxt) and not nxt_raw.startswith(("  ", "\t")):
                        break
                    sub_clean = nxt.lstrip(" -*•\t").strip()
                    sub_hdr = re.sub(r"[*_`]+", "", sub_clean)
                    if sub_hdr.endswith((":", "：")):
                        break
                    subs.append(sub_clean)
                    j += 1
                else:
                    break
            if subs:
                joined = line.lstrip(" -*•\t") + " " + "；".join(subs)
                out.append(joined)
                # Also keep individual long sub-bullets that carry independent markers (e.g. [IRREV] vs TODO)
                for s_item in subs:
                    if len(s_item) >= 45 or UNVERIFIED_RE.search(s_item) or IRREV_RE.search(s_item):
                        out.append(s_item)
                i = j
                continue
        out.append(line)
        i += 1
    return out


def _summarize_error_output(out_str: str, max_len: int = 180) -> tuple[str, list[str]]:
    """
    Extract high-signal failure details (exit code, traceback File:line, and final exception line)
    from potentially massive tool outputs so tail tracebacks are never lost behind log noise.
    Handles both raw text and JSON-escaped `custom_tool_call_output` blobs from Codex `exec`.
    """
    raw = str(out_str or "").strip()
    if not raw:
        return "", []
    text = raw.replace("\\r\\n", "\n").replace("\\n", "\n").replace('\\"', '"')
    locs: list[str] = []
    for fpath, lineno in re.findall(r'File "([^"]+)", line (\d+)', text):
        base = os.path.basename(fpath.strip())
        loc_tag = f"{base}:{lineno}" if base else f"{fpath}:{lineno}"
        if loc_tag not in locs:
            locs.append(loc_tag)
    for m_loc, _ in FILE_LINE_RE.findall(text[-4000:]):
        base_loc = os.path.basename(m_loc)
        if base_loc not in locs:
            locs.append(base_loc)

    exit_codes = [
        int(c)
        for c in re.findall(
            r'(?:process exited with code|command exited with code|exit[_ ]code["\s:]+)(-?\d+)',
            text,
            re.I,
        )
    ]
    non_zero = [c for c in exit_codes if c != 0]
    exit_tag = f"exit {non_zero[-1]}" if non_zero else (f"exit {exit_codes[-1]}" if exit_codes else "")

    tail_lines = [
        ln.strip().rstrip('"}]')
        for ln in text[-4000:].splitlines()
        if ln.strip() and not ln.strip().startswith(('{"chunk_id"', '["', "Script completed", "Wall time", "Output:"))
    ]
    exc_line = ""
    for ln in reversed(tail_lines):
        if re.search(
            r"\b(?:Error|Exception|RuntimeError|AssertionError|ValueError|TypeError|KeyError|SyntaxError|FAILED)\b",
            ln,
        ):
            exc_line = ln
            break
    if not exc_line and tail_lines:
        exc_line = tail_lines[-1]

    parts: list[str] = []
    if exit_tag:
        parts.append(exit_tag)
    if locs:
        parts.append(f"@ {', '.join(locs[-2:])}")
    if exc_line:
        parts.append(_snippet(exc_line, 120))
    summary = " — ".join(parts) if parts else _snippet(text, max_len)
    return _snippet(summary, max_len), locs


def redact_secrets(text: str) -> str:
    """Scrub API keys, tokens, and credentials from handoff text."""
    out = str(text or "")
    for pattern, repl in SECRET_PATTERNS:
        out = pattern.sub(repl, out)
    return out


def estimate_tokens(text: str) -> int:
    """Conservative token estimator for mixed CJK and English text."""
    if not text:
        return 0
    ascii_chars = sum(1 for ch in text if ord(ch) < 128)
    non_ascii = len(text) - ascii_chars
    return max(1, (ascii_chars // 4) + int(non_ascii * 0.7))


def _snippet(text: str, max_len: int = 200) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1] + "…"
