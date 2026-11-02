#!/usr/bin/env python3
"""`mixhub.tui.hubtui.header`: The 2-row `--header` of a `hubpool` window: target session meta and orphan exit."""
from __future__ import annotations

try:
    import curses
except ImportError:  # Windows without windows-curses installed
    curses = None
import os
import subprocess
import sys
import time

from mixhub.hub.schema import codex_state_db_candidates
from mixhub.tmux.headers import HUB_HDR_ROWS
from mixhub.tmux.onexit import handle_cli_on_exit


class HeaderMixin:
    """Keeps a `--header` pane bound to its window's session and hands focus to the CLI below it."""

    def _check_orphan_header_and_exit(self) -> bool:
        """
        In `--header` mode, if the bottom CLI pane has exited and left this `--header` pane as the only
        pane in `pool:w-<tool>-<id>` (expanded to `h > HUB_HDR_ROWS + 2`), execute `handle_cli_on_exit`
        immediately and terminate so a 1-pane orphan `--header` window never remains on screen.
        """
        if not getattr(self, "header_only", False) or not os.environ.get("TMUX"):
            return False
        run_fn = subprocess.run
        if type(run_fn).__name__ in ("MagicMock", "AsyncMock", "NonCallableMagicMock"):
            return False
        if (time.monotonic() - getattr(self, "_start_mono", 0.0)) < 1.5:
            return False
        try:
            cur_h, _ = self.stdscr.getmaxyx()
            if hasattr(sys.stdout, "fileno"):
                try:
                    sz = os.get_terminal_size(sys.stdout.fileno())
                    if sz.lines > 0:
                        cur_h = max(cur_h, sz.lines)
                except OSError:
                    pass
            if cur_h <= HUB_HDR_ROWS + 2:
                return False
            env = dict(os.environ)
            env.pop("TMUX", None)
            pane_id = os.environ.get("TMUX_PANE", "").strip()
            cmd = ["tmux", "-L", "hubpool", "display-message", "-p"]
            if pane_id:
                cmd.extend(["-t", pane_id])
            cmd.append("#{window_name}|#{window_panes}")
            res = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=1.5)
            out = (res.stdout if isinstance(getattr(res, "stdout", None), str) else "").strip()
            parts = out.split("|")
            wname = parts[0].strip() if parts else ""
            wpanes = parts[1].strip() if len(parts) > 1 else ""
            if wname.startswith("w-") and wpanes == "1":
                ht_tool, ht_id = getattr(self, "header_target", None) or ("", "")
                s = self.current_session()
                eff_tool = ht_tool or (s.get("tool") if s else "") or "claude"
                eff_id = ht_id or (s.get("id") if s else "")
                handle_cli_on_exit(str(eff_tool), str(eff_id), wname, 0)
                try:
                    curses.endwin()
                except Exception:
                    pass
                raise SystemExit(0)
        except SystemExit:
            raise
        except Exception:
            pass
        return False

    def _sync_header_session_meta(self) -> bool:
        """Refresh the header title from this session's metadata without full discovery."""
        if not getattr(self, "header_only", False):
            return False
        now_m = time.monotonic()
        if (now_m - getattr(self, "_last_hdr_meta_check", 0.0)) < 2.0:
            return False
        self._last_hdr_meta_check = now_m
        s = self.current_session()
        if not s or not s.get("id") or str(s.get("id")).startswith("new-"):
            return False
        base_home = getattr(self.hub, "home", None) or os.path.expanduser("~")
        watch_files = [getattr(self.hub, "state_path", "")]
        if not getattr(self, "header_target", None):
            watch_files.append(self._active_selection_path())
        if s.get("tool") == "agy":
            watch_files.append(os.path.join(base_home, ".gemini", "antigravity-cli", "conversation_summaries.db"))
            watch_files.append(os.path.join(base_home, ".gemini", "antigravity-cli", "conversation_summaries.db-wal"))
        elif s.get("tool") == "codex":
            watch_files.extend(codex_state_db_candidates(lambda p: os.path.join(base_home, p)))
            watch_files.extend(fp + "-wal" for fp in tuple(watch_files) if fp.endswith(".sqlite"))
            if s.get("path"):
                watch_files.append(str(s["path"]))
        elif s.get("tool") == "claude":
            if s.get("path"):
                watch_files.append(str(s["path"]))
            watch_files.append(os.path.join(base_home, ".claude", "session_labels.json"))
        sig = []
        for fp in watch_files:
            if not fp:
                continue
            try:
                st = os.stat(fp)
                sig.append((fp, st.st_mtime_ns, st.st_size))
            except OSError:
                pass
        sig_t = tuple(sig)
        if sig_t == getattr(self, "_last_hdr_meta_sig", None):
            return False
        self._last_hdr_meta_sig = sig_t
        try:
            if hasattr(self.hub, "get_lightweight_session_meta"):
                fresh_s = self.hub.get_lightweight_session_meta(s["tool"], s["id"])
            else:
                fresh_s = self.hub.get(s["tool"], s["id"], force=False)
            if isinstance(fresh_s, dict) and fresh_s.get("title") != s.get("title"):
                s["title"] = fresh_s.get("title")
                for field in ("shortCwd", "path"):
                    if field in fresh_s:
                        s[field] = fresh_s[field]
                self._dirty = True
                return True
        except Exception:
            pass
        return False

    def _yield_header_focus_to_cli(self) -> None:
        """`--header` never keeps tmux focus: hand it back to the CLI pane below (so `Cmd+V` pastes land there)."""
        if not getattr(self, "header_only", False) or not os.environ.get("TMUX"):
            return
        env = dict(os.environ)
        try:
            subprocess.Popen(
                ["tmux", "-L", "hubpool", "select-pane", "-t", ".{bottom}"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
            )
        except Exception:
            pass
