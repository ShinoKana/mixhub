"""Pane probes: classify live `hubpool` windows with `mixhub.probe` and fold the result into session state."""
from __future__ import annotations

import os
import subprocess
import threading
import time


class PaneProbeMixin:
    """Cached `mixhub.probe` pane classification for live hubpool windows."""

    def _live_tmux_window_names(self):
        """Session and window names on `tmux -L hubpool` / `tmux -L hub` (e.g. `w-codex-<id[:12]>`, `hub-codex-<id[:12]>`)."""
        now_mono = time.monotonic()
        cached = getattr(self, "_tmux_names_cache", None)
        if cached and now_mono - cached[0] < 4.0:
            return cached[1]
        env = dict(os.environ)
        env.pop("TMUX", None)
        names = set()
        for sock in ("hubpool", "hub"):
            try:
                tp = subprocess.run(["tmux", "-L", sock, "list-windows", "-a", "-F", "#{session_name}\t#{window_name}"],
                                    capture_output=True, text=True, timeout=0.6, env=env)
            except Exception:
                continue
            if tp.returncode == 0:
                for ln in tp.stdout.splitlines():
                    names.update(part.strip() for part in ln.split("\t") if part.strip())
        self._tmux_names_cache = (now_mono, names)
        return names

    @staticmethod
    def _hubpool_reachable() -> bool:
        """Agent windows to probe: inside the workspace, or its `hubpool` server's socket exists (a web daemon started
        from a plain terminal has no `$TMUX`, yet a Codex or Gemini approval prompt only shows on screen)."""
        if os.environ.get("TMUX"):
            return True
        base = os.environ.get("TMUX_TMPDIR") or "/tmp"
        return os.path.exists(os.path.join(base, f"tmux-{os.getuid()}", "hubpool"))

    def set_pane_probe(self, session_key_or_id: str, probe_res: dict | None, mono_ts: float | None = None) -> None:
        """Store or clear a cached `mixhub.probe` classification result for `session_key_or_id`."""
        k = str(session_key_or_id or "").strip()
        if not k:
            return
        with self.lock:
            if probe_res is None:
                self._pane_probe_cache.pop(k, None)
                if ":" in k:
                    self._pane_probe_cache.pop(k.split(":", 1)[1], None)
                return
            ts_m = float(mono_ts) if mono_ts is not None else time.monotonic()
            prev = self._pane_probe_cache.get(k) or {}
            p_state = str(probe_res.get("state") or "")
            idle_streak = (int(prev.get("idle_streak") or 0) + 1) if p_state == "idle" else 0
            entry = {
                "mono": ts_m,
                "result": dict(probe_res),
                "idle_streak": idle_streak,
            }
            self._pane_probe_cache[k] = entry
            if ":" in k:
                self._pane_probe_cache[k.split(":", 1)[1]] = entry

    def _get_fresh_pane_probe(self, key: str, s: dict, now_mono: float, max_age_sec: float = 5.0) -> tuple[dict | None, int]:
        if callable(getattr(self, "pane_probe_fn", None)):
            try:
                res = self.pane_probe_fn(s)
                if isinstance(res, dict) and res.get("ok", True):
                    return res, (2 if res.get("state") == "idle" else 0)
            except Exception:
                pass
        entry = self._pane_probe_cache.get(key) or self._pane_probe_cache.get(str(s.get("id") or ""))
        if not entry:
            return None, 0
        if (now_mono - float(entry.get("mono") or 0.0)) > max_age_sec:
            return None, 0
        res = entry.get("result")
        if not isinstance(res, dict) or not res.get("ok", True):
            return None, 0
        return res, int(entry.get("idle_streak") or 0)

    def _apply_pane_probe_state(self, key: str, s: dict, age: float, now_mono: float) -> None:
        probe_res, idle_streak = self._get_fresh_pane_probe(key, s, now_mono)
        if not probe_res:
            return
        p_state = str(probe_res.get("state") or "")
        if p_state in ("awaiting_permission", "awaiting_trust", "awaiting_question"):
            s["agentState"] = "wait_input"
            s["stateSource"] = "probe"
            s["probeState"] = p_state
            s.pop("interrupted", None)
            for src_k, dst_k in (
                ("question", "question"),
                ("command", "command"),
                ("tool_summary", "toolSummary"),
                ("prompt_snippet", "prompt_snippet"),
            ):
                if probe_res.get(src_k):
                    s[dst_k] = probe_res[src_k]
        elif p_state == "working":
            s["agentState"] = "working"
            s["stateSource"] = "probe"
            s["probeState"] = "working"
            s.pop("interrupted", None)
        elif p_state == "idle":
            if idle_streak >= 2 and age >= 30.0 and s.get("tailStatus") == "unclosed_tool" and s.get("agentState") == "working":
                s["agentState"] = "done_idle"
                s["stateSource"] = "probe"

    def refresh_pane_probes_sync(self, sessions=None, runner=None) -> int:
        """Probe live `hubpool` windows (`w-<tool>-<sid[:12]>`) and update `self._pane_probe_cache`."""
        try:
            from mixhub.probe.capture import _run_cmd, _tmux_bin, capture_panes
            from mixhub.probe.classify import classify_pane_text
            from mixhub.probe.patterns import TAIL_LINES
        except Exception:
            return 0
        target_sessions = sessions if sessions is not None else self.sessions
        live_items = [
            (k, dict(s))
            for k, s in target_sessions.items()
            if s.get("isLive") and not s.get("parentId") and not s.get("isSubagent") and not s.get("automated")
        ]
        if not live_items:
            return 0
        tmux_bin = _tmux_bin()
        rc, out = _run_cmd(
            [tmux_bin, "-L", "hubpool", "list-windows", "-t", "pool", "-F", "#{window_name}"],
            runner=runner,
            timeout=0.5,
        )
        if rc != 0 or not out:
            return 0
        win_set = {w.strip() for w in out.splitlines() if w.strip().startswith("w-")}
        if not win_set:
            return 0
        updated = 0
        changed_state = False
        now_m = time.monotonic()
        probe_targets = []
        for key, s in live_items:
            sid = str(s.get("id") or "")
            tool = str(s.get("tool") or "")
            if not sid:
                continue
            cand_wins = [f"w-{tool}-{sid[:12]}", f"w-{sid}", f"w-{sid[:12]}"]
            wname = next((w for w in cand_wins if w in win_set), None)
            if not wname:
                suffix = f"-{sid[:12]}"
                wname = next((w for w in win_set if w.endswith(suffix)), None)
            if not wname:
                continue
            probe_targets.append((key, f"pool:{wname}.{{bottom}}"))
        captures = capture_panes(
            [target for _, target in probe_targets], lines=TAIL_LINES, runner=runner,
        )
        for key, target in probe_targets:
            cap = captures.get(target) or {}
            if not cap.get("ok"):
                continue
            classified = classify_pane_text(cap.get("lines") or [])
            prev_entry = self._pane_probe_cache.get(key)
            prev_state = ((prev_entry or {}).get("result") or {}).get("state")
            self.set_pane_probe(key, classified, mono_ts=now_m)
            updated += 1
            if prev_state != classified.get("state"):
                changed_state = True
        if changed_state:
            self.scanned = 0
        return updated

    def _maybe_schedule_pane_probes(self, sessions) -> None:
        now_m = time.monotonic()
        if self._pane_probe_running or (now_m - self._pane_probe_last_sched) < 1.5:
            return
        self._pane_probe_last_sched = now_m
        snapshot = {
            k: {"id": s.get("id"), "tool": s.get("tool"), "isLive": bool(s.get("isLive"))}
            for k, s in sessions.items()
            if s.get("isLive") and not s.get("parentId") and not s.get("isSubagent") and not s.get("automated")
        }
        if not snapshot:
            return
        self._pane_probe_running = True

        def _worker():
            try:
                self.refresh_pane_probes_sync(sessions=snapshot)
            except Exception:
                pass
            finally:
                self._pane_probe_done_at = time.monotonic()
                self._pane_probe_running = False

        threading.Thread(target=_worker, name="hub-pane-probe", daemon=True).start()
