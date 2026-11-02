"""Effective project-root inference and resume-state classification for a session about to be handed off."""
from __future__ import annotations

import os
import re
from collections.abc import Mapping

from mixhub.i18n import t


PROJECT_ROOT_MARKERS = (
    ".git",
    "CLAUDE.md",
    "AGENTS.md",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
)

RESUME_STATES = (
    "user_pending",
    "mid_task",
    "awaiting_user",
    "completed_idle",
)

class _ResumeStateActions(Mapping):
    """`resume_state` -> the action directive for the next agent, in the interface language (`handoff.resume.*`)."""

    def __getitem__(self, state: str) -> str:
        if state not in RESUME_STATES:
            raise KeyError(state)
        return t(f"handoff.resume.{state}")

    def __iter__(self):
        return iter(RESUME_STATES)

    def __len__(self) -> int:
        return len(RESUME_STATES)


RESUME_STATE_ACTIONS = _ResumeStateActions()

AWAITING_USER_RE = re.compile(
    r"(?:要不要|是否继续|是否需要|请确认|请选择|拍板|你看可以吗|要我现在|还是用|方案\s*[A-Z1-9]\s*还是"
    r"|would you like|should i\b|shall i\b|do you want me to|please confirm|which option)",
    re.I,
)


def infer_effective_root(
    declared_cwd: str,
    tool_paths: list[str] | None = None,
    home_dir: str = "",
) -> str:
    """
    Infer the effective project root (`effective_root`) when a session was started from
    `~` (`$HOME`), `/tmp`, or a generic parent directory, but actual tool operations
    (`Read`/`Edit`/`Write`/`Grep`/`Bash`) concentrate in a specific subproject root
    containing `.git`, `CLAUDE.md`, `AGENTS.md`, `pyproject.toml`, or `package.json`.
    """
    raw_cwd = str(declared_cwd or "").strip()
    expanded_cwd = os.path.abspath(os.path.expanduser(raw_cwd)) if raw_cwd else ""
    real_home = os.path.abspath(os.path.expanduser("~"))
    hub_home = os.path.abspath(os.path.expanduser(str(home_dir))) if home_dir else ""

    generic_roots = {"", "/", "/tmp", "/private/tmp", "/var/tmp", "/Users", real_home}
    if hub_home:
        generic_roots.add(hub_home)

    def _has_project_marker(d: str) -> bool:
        if not d or d in generic_roots or not os.path.isdir(d):
            return False
        return any(os.path.exists(os.path.join(d, m)) for m in PROJECT_ROOT_MARKERS)

    # If declared_cwd is already a non-generic project directory with markers, see if tool paths
    # stay inside it; if declared_cwd is generic (e.g. ~ or /tmp) or lacks markers, resolve from tool_paths.
    cwd_is_generic = expanded_cwd in generic_roots

    if not tool_paths:
        return expanded_cwd or real_home

    marker_counts: dict[str, int] = {}
    existing_dir_counts: dict[str, int] = {}
    subpath_dir_counts: dict[str, int] = {}

    ignore_prefixes = ("/dev/", "/proc/", "/sys/", "/etc/", "/usr/", "/bin/", "/sbin/", "/System/", "/Library/")

    for raw_p in tool_paths:
        p = str(raw_p or "").strip().strip("'\"")
        if not p or p.startswith("-"):
            continue
        # Strip trailing :line or :start-end
        p = re.sub(r":\d+(?:-\d+|\+)?$", "", p)
        if not p:
            continue
        p = os.path.expanduser(p)
        if not os.path.isabs(p):
            if expanded_cwd and expanded_cwd not in ("", "/"):
                p = os.path.join(expanded_cwd, p)
            else:
                continue
        abs_p = os.path.abspath(p)
        if any(abs_p.startswith(pref) for pref in ignore_prefixes):
            continue

        start_dir = abs_p if os.path.isdir(abs_p) else os.path.dirname(abs_p)
        if not start_dir or start_dir in generic_roots:
            continue

        # 1. Walk upward looking for project root markers (.git, CLAUDE.md, AGENTS.md, pyproject.toml, package.json)
        cur = start_dir
        found_marker_dir = ""
        while cur and cur not in generic_roots and cur != os.path.dirname(cur):
            if _has_project_marker(cur):
                found_marker_dir = cur
                break
            parent = os.path.dirname(cur)
            if parent == cur or parent in generic_roots:
                break
            cur = parent

        if found_marker_dir:
            marker_counts[found_marker_dir] = marker_counts.get(found_marker_dir, 0) + 1
            continue

        # 2. If no marker on disk, track existing subdirectories when launched from generic cwd
        if cwd_is_generic:
            cur_d = start_dir
            while cur_d and cur_d not in generic_roots and not os.path.isdir(cur_d):
                parent = os.path.dirname(cur_d)
                if parent == cur_d or parent in generic_roots:
                    cur_d = ""
                    break
                cur_d = parent
            if cur_d and cur_d not in generic_roots and os.path.isdir(cur_d):
                existing_dir_counts[cur_d] = existing_dir_counts.get(cur_d, 0) + 1
            elif (
                expanded_cwd in (real_home, "/tmp", "/private/tmp")
                and abs_p.startswith(expanded_cwd.rstrip("/") + "/")
            ):
                subpath_dir_counts[start_dir] = subpath_dir_counts.get(start_dir, 0) + 1

    if marker_counts:
        best_marker_root = max(marker_counts.items(), key=lambda kv: (kv[1], len(kv[0])))[0]
        if cwd_is_generic or not _has_project_marker(expanded_cwd) or best_marker_root.startswith(expanded_cwd.rstrip("/") + "/"):
            return best_marker_root

    if cwd_is_generic and existing_dir_counts:
        best_existing = max(existing_dir_counts.items(), key=lambda kv: (kv[1], -len(kv[0])))[0]
        return best_existing

    if cwd_is_generic and subpath_dir_counts:
        dirs = list(subpath_dir_counts.keys())
        try:
            common = os.path.commonpath(dirs)
        except ValueError:
            common = dirs[0]
        if common and common not in generic_roots:
            return common
        return max(subpath_dir_counts.items(), key=lambda kv: kv[1])[0]

    return expanded_cwd or real_home


def classify_resume_state(
    session: dict,
    turns: list[dict] | None = None,
    last_event_kind: str = "",
    corrupt_tail_line: bool = False,
) -> tuple[str, str]:
    """
    Determine exact handoff continuation state (`resume_state`) and its immediate action directive:
      - `user_pending`: Last event is a user prompt with no substantive reply or tool action yet.
      - `mid_task`: Predecessor stopped during tool execution, unclosed tool call, or interrupted stream.
      - `awaiting_user`: Predecessor proposed a plan/question and is waiting for user confirmation.
      - `completed_idle`: Previous turn completed cleanly and session is idle awaiting new instructions.
    """
    tail_status = str(session.get("tailStatus") or "").strip().lower()
    agent_state = str(session.get("agentState") or "").strip().lower()

    if corrupt_tail_line:
        return "mid_task", RESUME_STATE_ACTIONS["mid_task"]

    if last_event_kind == "user":
        return "user_pending", RESUME_STATE_ACTIONS["user_pending"]

    if last_event_kind == "ask_user_tool":
        return "awaiting_user", RESUME_STATE_ACTIONS["awaiting_user"]

    if last_event_kind in ("tool_call", "tool_result"):
        return "mid_task", RESUME_STATE_ACTIONS["mid_task"]

    if tail_status in ("unclosed_tool", "interrupted") or agent_state in ("working", "interrupted"):
        return "mid_task", RESUME_STATE_ACTIONS["mid_task"]

    if tail_status == "ball_in_model":
        if turns and turns[-1].get("tools") and not turns[-1].get("assistant_msgs"):
            return "mid_task", RESUME_STATE_ACTIONS["mid_task"]
        return "user_pending", RESUME_STATE_ACTIONS["user_pending"]

    # Check the last assistant message if last_event_kind == "assistant" or fallback
    last_asst_text = ""
    if turns:
        for t in reversed(turns):
            if t.get("assistant_msgs"):
                last_asst_text = str(t["assistant_msgs"][-1][1] or "").strip()
                break

    if last_event_kind == "assistant" or last_asst_text:
        tail_chunk = last_asst_text[-400:] if last_asst_text else ""
        non_empty_lines = [ln.strip() for ln in tail_chunk.splitlines() if ln.strip()]
        last_line = non_empty_lines[-1] if non_empty_lines else ""
        if (
            tail_status == "wait_input"
            or agent_state == "wait_input"
            or bool(session.get("asked"))
            or last_line.endswith(("?", "？"))
            or bool(AWAITING_USER_RE.search(tail_chunk))
        ):
            return "awaiting_user", RESUME_STATE_ACTIONS["awaiting_user"]
        return "completed_idle", RESUME_STATE_ACTIONS["completed_idle"]

    if turns and turns[-1].get("user_texts") and not turns[-1].get("assistant_msgs") and not turns[-1].get("tools"):
        return "user_pending", RESUME_STATE_ACTIONS["user_pending"]

    return "completed_idle", RESUME_STATE_ACTIONS["completed_idle"]
