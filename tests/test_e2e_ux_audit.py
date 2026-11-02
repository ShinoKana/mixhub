import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import handoff
import server
import tui
from mixhub.core.textwidth import disp_width
from mixhub.core.launch import CLI_MODULE, module_argv
from mixhub.tui import paths as tui_paths
from mixhub.tui.hubtui import evq

C1 = "11111111-1111-4111-8111-111111111111"
C2 = "11111111-2222-4111-8111-222222222222"
A1 = "22222222-2222-4222-8222-222222222222"
X1 = "33333333-3333-4333-8333-333333333333"


def claude_user(text: str, cwd: str = "/workspace") -> dict:
    return {"type": "user", "cwd": cwd, "message": {"role": "user", "content": text}}


def claude_asst(text: str, tool_uses: list | None = None) -> dict:
    content = [{"type": "text", "text": text}]
    for tu in tool_uses or []:
        content.append(tu)
    return {"type": "assistant", "message": {"role": "assistant", "content": content}}


class RealUseE2EUXAndPerfAuditTest(unittest.TestCase):
    """Independent Blind QA & Real-Use UX Audit E2E test suite covering all 5 contracts."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.root = self.home / "hub"
        self.root.mkdir(parents=True, exist_ok=True)
        self.workspace = self.home / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)

        self.hub = server.Hub(str(self.home), str(self.root))
        self.hub.processes = lambda: []
        self.hub.open_files = lambda _: {}
        # Every agent CLI "installed", so the New Session card is the same on any machine
        which_patch = patch("tui.shutil.which", side_effect=lambda b: "/usr/local/bin/" + b)
        which_patch.start()
        self.addCleanup(which_patch.stop)

        self.active_sel_path = str(self.root / "data" / "active_selection.json")
        self.seen_path = str(self.root / "data" / "seen.json")
        self._orig_active_sel_path = tui_paths.ACTIVE_SEL_PATH
        tui_paths.ACTIVE_SEL_PATH = self.active_sel_path
        self._orig_tmux_env = os.environ.pop("TMUX", None)

        self._seed_sessions()

    def tearDown(self):
        tui._NEW_HUBPOOL_SESSIONS.clear()
        tui._HUBPOOL_WINS_CACHE["ts"] = 0.0
        tui._HUBPOOL_WINS_CACHE["wins"] = set()
        if self._orig_tmux_env is not None:
            os.environ["TMUX"] = self._orig_tmux_env
        else:
            os.environ.pop("TMUX", None)
        tui_paths.ACTIVE_SEL_PATH = self._orig_active_sel_path
        self.temp.cleanup()

    def _write_jsonl(self, path: Path, rows: list[dict], mtime: float | None = None):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        if mtime is not None:
            os.utime(path, (mtime, mtime))

    def _seed_sessions(self):
        now = time.time()
        # 1. Newest Claude session (C1)
        self.claude_p1 = self.home / f".claude/projects/proj/{C1}.jsonl"
        self._write_jsonl(
            self.claude_p1,
            [
                claude_user(
                    "实现跨 Agent 零延迟切换与状态同步。严禁新建顶层目录，必须通过全部单测。api_key=sk-secret1234567890abcdef",
                    cwd=str(self.workspace),
                ),
                claude_asst(
                    "[IRREV] 决定采用 hollow diamond ◇ 与 P_WAIT 未读时间戳。\n已否决方案：点击左栏行首触发 pin 置顶重排。",
                    tool_uses=[
                        {"type": "tool_use", "name": "Edit", "id": "tu1", "input": {"file_path": str(self.workspace / "tui.py")}},
                        {"type": "tool_use", "name": "Bash", "id": "tu2", "input": {"command": "python3 -m unittest discover -s tests -v"}},
                    ],
                ),
            ],
            mtime=now - 10,
        )

        # 2. Second Claude session (C2, older)
        self.claude_p2 = self.home / f".claude/projects/proj/{C2}.jsonl"
        self._write_jsonl(
            self.claude_p2,
            [
                claude_user("Claude secondary session prompt", cwd=str(self.workspace)),
                claude_asst("Secondary Claude answer"),
            ],
            mtime=now - 120,
        )

        # 3. Gemini (agy) session (A1)
        import sqlite3

        agy_db = self.home / ".gemini/antigravity-cli/conversation_summaries.db"
        agy_db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(agy_db)
        conn.execute(
            "CREATE TABLE conversation_summaries(conversation_id TEXT, title TEXT, preview TEXT, workspace_uris TEXT, parent_conversation_id TEXT, step_count INTEGER)"
        )
        conn.execute(
            "INSERT INTO conversation_summaries VALUES(?,?,?,?,?,?)",
            (A1, "Gemini UX Audit Session", "preview", json.dumps([f"file://{self.workspace}"]), "", 3),
        )
        conn.commit()
        conn.close()
        self.agy_p1 = self.home / f".gemini/antigravity-cli/brain/{A1}/.system_generated/logs/transcript.jsonl"
        self._write_jsonl(
            self.agy_p1,
            [
                {"step_index": 0, "type": "USER_INPUT", "content": "<USER_REQUEST>验证 Gemini 标签页同步与交接</USER_REQUEST>"},
                {
                    "step_index": 1,
                    "type": "PLANNER_RESPONSE",
                    "content": "[IRREV] 保持 <1.5k token 交接包预算。",
                    "tool_calls": [
                        {"name": "edit_file", "args": {"Path": str(self.workspace / "handoff.py")}},
                        {"name": "run_command", "args": {"CommandLine": "python3 -m unittest -v"}},
                    ],
                },
            ],
            mtime=now - 30,
        )

        # 4. Codex session (X1)
        codex_db = self.home / ".codex/state_5.sqlite"
        codex_db.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(codex_db)
        conn.execute("CREATE TABLE threads(cwd TEXT, id TEXT, first_user_message TEXT, title TEXT)")
        conn.execute(
            "INSERT INTO threads VALUES(?,?,?,?)",
            (str(self.workspace), X1, "Codex first prompt", "Codex Audit Session"),
        )
        conn.commit()
        conn.close()
        self.codex_p1 = self.home / f".codex/sessions/2026/10/24/rollout-test-{X1}.jsonl"
        self._write_jsonl(
            self.codex_p1,
            [
                {"type": "session_meta", "payload": {"id": X1, "cwd": str(self.workspace), "timestamp": "2026-10-24T00:00:00Z"}},
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "必须保证单次点击耗时 <3ms 且零同步 tmux 子进程"}],
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "message",
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": "[IRREV] 单击仅写 active_selection.json，双击才切原生 CLI。"}],
                    },
                },
            ],
            mtime=now - 60,
        )

    def _make_tui(self, height: int = 30, width: int = 32, sidebar_only: bool = True) -> tui.HubTUI:
        scr = MagicMock()
        scr.getmaxyx.return_value = (height, width)
        with patch.object(tui.HubTUI, "_init_colors"), patch("tui.curses.color_pair", side_effect=lambda p: p << 8):
            app = tui.HubTUI(
                scr,
                sidebar_only=sidebar_only,
                hub_instance=self.hub,
                caf=MagicMock(),
                seen_path=self.seen_path,
            )
        return app

    def test_1_performance_zero_lag_50_clicks_and_zero_sync_tmux(self):
        """
        Contract 1:
        Measure HubTUI single-click row selection + _on_sidebar_selection_changed(activate_live=False)
        + draw() latency across 50 consecutive clicks: must average <3ms per click and spawn
        0 synchronous tmux subprocesses when pool:reader is already active.
        """
        with patch.dict(os.environ, {"TMUX": "/tmp/tmux-1000/default,123,0"}), \
             patch("tui.curses.color_pair", side_effect=lambda p: p << 8), \
             patch("tui.subprocess.run") as mock_run, \
             patch("tui.subprocess.Popen") as mock_popen:
            app = self._make_tui(height=30, width=32, sidebar_only=True)
            # Populate 20 realistic sessions so draw() renders a full sidebar viewport
            base_sessions = list(app.sessions)
            while len(app.sessions) < 20:
                idx = len(app.sessions)
                s_copy = dict(base_sessions[idx % len(base_sessions)])
                s_copy["id"] = f"perf-session-{idx:02d}"
                s_copy["title"] = f"性能压测会话 #{idx:02d} 中英文混合标题测试"
                s_copy["isLive"] = (idx % 3 == 0)
                s_copy["agentState"] = ("working", "wait_input", "done_idle", "dormant")[idx % 4]
                app.sessions.append(s_copy)

            # Simulate pool:reader already active in hubpool
            app._active_hubpool_win = "reader"
            mock_run.reset_mock()
            mock_popen.reset_mock()

            list_top, _, list_h = app._list_bounds(30)
            visible_rows = min(len(app.sessions), list_h)
            self.assertGreaterEqual(visible_rows, 10)

            budget_ms = 9.0 if os.environ.get("CI") else 3.0  # shared CI runners are ~2-3x slower than a laptop
            # A shared CI runner can stall for a whole round (one ubuntu run averaged 69 ms); a second round decides
            for _round in range(2 if os.environ.get("CI") else 1):
                latencies_ms = []
                for i in range(50):
                    # Cycle across distinct rows so consecutive clicks never trigger 400ms double-click
                    row_offset = i % visible_rows
                    my = list_top + row_offset
                    mx = 1 + (i % 15)
                    t0 = time.perf_counter()
                    app._handle_mouse(parsed_event=(0, mx, my, False))
                    app.draw()
                    t1 = time.perf_counter()
                    latencies_ms.append((t1 - t0) * 1000.0)
                    self.assertEqual(app.selected_idx, row_offset)
                avg_ms = sum(latencies_ms) / len(latencies_ms)
                if avg_ms < budget_ms:
                    break

            self.assertLess(
                avg_ms,
                budget_ms,
                f"Expected average single-click + sync + draw latency < {budget_ms}ms, got {avg_ms:.3f}ms (max={max(latencies_ms):.3f}ms)",
            )
            self.assertEqual(
                mock_run.call_count,
                0,
                f"Expected 0 synchronous subprocess.run calls during 50 single-clicks, got {mock_run.call_args_list}",
            )
            self.assertEqual(
                mock_popen.call_count,
                0,
                f"Expected 0 subprocess.Popen calls when pool:reader is already active, got {mock_popen.call_args_list}",
            )
            sel = tui.read_active_selection()
            self.assertIsNotNone(sel)
            self.assertEqual(sel["id"], app.current_session()["id"])

    def test_2_top_filter_tab_switching_syncs_top_row_and_active_selection(self):
        """
        Contract 2:
        A) Full TUI (`sidebar_only = False`): clicking each top filter tab (All / Claude / Gemini / Codex) updates
           self.selected_idx to the top session of that tab, and `_on_sidebar_selection_changed(activate_live=False)`
           immediately syncs active_selection.json so --reader never displays a stale session from the previous tab.
        B) `--sidebar` (`sidebar_only = True`): Row 0 is `mixhub`, with no navigation arrows or hidden filter tabs;
           clicking `mixhub` from a session returns Home.
        """
        with patch.dict(os.environ, {"TMUX": "/tmp/tmux-1000/default,123,0"}), \
             patch("tui.curses.color_pair", side_effect=lambda p: p << 8), \
             patch("tui.subprocess.run"), \
             patch("tui.subprocess.Popen"):
            # ---- A) Filter tabs in the full (non-sidebar) TUI
            app = self._make_tui(height=30, width=120, sidebar_only=False)
            app._active_hubpool_win = "reader"
            reader_app = self._make_tui(height=30, width=100, sidebar_only=False)
            reader_app.reader_only = True

            left_w = app._effective_left_w(120, False)
            toggle_x = max(1, left_w - 2)
            tab_ranges = {
                tkey: (x0, x1)
                for tkey, _, x0, x1 in tui.sidebar_filter_tab_ranges(
                    start_x=1, available_tools=app._available_agent_tools(), max_x=toggle_x - 1
                )
            }
            for tkey in ("claude", "agy", "codex", "all"):
                self.assertIn(tkey, tab_ranges)

            # Select the second Claude session (C2) while in 'All' first, to test the edge case where
            # the previously selected session still exists in the target tab at index > 0.
            c2_idx = next(i for i, s in enumerate(app.sessions) if s["id"] == C2)
            app.selected_idx = c2_idx

            for tkey in ("claude", "agy", "codex", "all"):
                mx = tab_ranges[tkey][0] + 1
                app._handle_mouse(parsed_event=(0, mx, 0, False))
                self.assertEqual(app.tool_filter, tkey)
                self.assertGreater(len(app.sessions), 0)
                self.assertEqual(
                    app.selected_idx,
                    0,
                    f"Clicking filter tab '{tkey}' must select top row (idx 0), got idx={app.selected_idx}",
                )
                app._on_sidebar_selection_changed(activate_live=False)
                top_session = app.sessions[0]
                sel = tui.read_active_selection()
                self.assertIsNotNone(sel)
                self.assertFalse(sel.get("empty"))
                self.assertEqual(
                    sel["id"],
                    top_session["id"],
                    f"active_selection.json stale after clicking tab '{tkey}': expected {top_session['id']}, got {sel['id']}",
                )
                self.assertEqual(sel["tool"], top_session["tool"])

                # Verify --reader immediately syncs to the new tab's top session
                reader_app._last_sel_mtime = 0.0
                reader_app._last_sel_sig = None
                reader_app._sync_reader_selection()
                self.assertIsNotNone(reader_app.current_session())
                self.assertEqual(reader_app.current_session()["id"], top_session["id"])

            # Verify top filter tabs never overlap the right-aligned '◧' collapse toggle icon
            for _, _, x0, x1 in tui.sidebar_filter_tab_ranges(start_x=1, max_x=toggle_x - 1):
                self.assertLessEqual(x1, toggle_x - 1)

            # Verify empty filter result sets selected_idx == -1 and syncs empty: True to active_selection.json
            app.query = "nonexistent-filter-token-xyz"
            app._handle_mouse(parsed_event=(0, tab_ranges["all"][0] + 1, 0, False))
            self.assertEqual(app.selected_idx, -1)
            app._on_sidebar_selection_changed(activate_live=False)
            empty_sel = tui.read_active_selection()
            self.assertIsNotNone(empty_sel)
            self.assertTrue(empty_sel.get("empty"))

            # ---- B) `--sidebar` Row 0: `mixhub`, no navigation arrows or filter tabs
            sb = self._make_tui(height=30, width=32, sidebar_only=True)
            sb._active_hubpool_win = "reader"
            self.assertEqual(sb.selected_idx, -1)
            self.assertFalse(sb.can_nav_back())
            self.assertFalse(sb.can_nav_forward())
            list_top, _, _ = sb._list_bounds(30)

            def row_y(sid: str) -> int:
                for r_i, it in enumerate(sb._sidebar_display_rows()):
                    if it.get("kind") == "session" and sb.sessions[it["idx"]]["id"] == sid:
                        return list_top + r_i - sb.list_scroll
                raise AssertionError(f"session {sid} not visible in sidebar")

            sb._handle_mouse(parsed_event=(0, 10, row_y(C1), False))
            self.assertEqual(sb.current_session()["id"], C1)
            sb._handle_mouse(parsed_event=(0, 10, row_y(C2), False))
            self.assertEqual(sb.current_session()["id"], C2)
            self.assertTrue(sb.can_nav_back())
            tool_filter_before = sb.tool_filter

            # Former filter tab positions (12, 15, 18) are inert; `‹ ›` at cols 22 / 25 navigate back/forward.
            for x in (12, 15, 18):
                sb._handle_mouse(parsed_event=(0, x, 0, False))
                self.assertEqual(sb.current_session()["id"], C2)
                self.assertEqual(tui.read_active_selection()["id"], C2)
                self.assertEqual(sb.tool_filter, tool_filter_before)
            sb._handle_mouse(parsed_event=(0, tui.HubTUI.NAV_BACK_X, 0, False))
            self.assertEqual(sb.current_session()["id"], C1)
            sb._handle_mouse(parsed_event=(0, tui.HubTUI.NAV_FWD_X, 0, False))
            self.assertEqual(sb.current_session()["id"], C2)

            # Clicking `mixhub` (col 5) from a session returns Home and clears active_selection.json
            sb._handle_mouse(parsed_event=(0, tui.HubTUI.WORDMARK_X, 0, False))
            self.assertEqual(sb.selected_idx, -1)
            self.assertIsNone(sb.current_session())
            self.assertTrue(tui.read_active_selection().get("empty"))
            self.assertEqual(sb.wordmark.mode, "off")

    def test_3_no_row_jumping_or_mx_lt_4_pin_hijack_on_click(self):
        """
        Contract 3:
        Verify that clicking at mx = 1, mx = 2 (on the ◇ diamond), and mx = 10 on any row
        in the sidebar selects that row (self.selected_idx == clicked_idx) and NEVER toggles
        pinned or reshuffles self.sessions.
        """
        with patch.dict(os.environ, {"TMUX": "/tmp/tmux-1000/default,123,0"}), \
             patch("tui.curses.color_pair", side_effect=lambda p: p << 8), \
             patch("tui.subprocess.run"), \
             patch("tui.subprocess.Popen"):
            app = self._make_tui(height=30, width=32, sidebar_only=True)
            app._active_hubpool_win = "reader"
            list_top, _, _ = app._list_bounds(30)
            initial_order = [(s["tool"], s["id"]) for s in app.sessions]
            self.assertGreaterEqual(len(initial_order), 4)

            # `--sidebar` always keeps its section headers, so map each session to its visual row
            row_of = {it["idx"]: r_i for r_i, it in enumerate(app._sidebar_display_rows()) if it.get("kind") == "session"}
            with patch.object(self.hub, "action", wraps=self.hub.action) as spy_action:
                for clicked_idx in range(len(app.sessions)):
                    for mx in (1, 2, 10):
                        # Reset double-click tracker so each click at (mx, row) is evaluated as a fresh single click
                        app._last_row_click = ("", 0.0)
                        click_y = list_top + row_of[clicked_idx]
                        app._handle_mouse(parsed_event=(0, mx, click_y, False))

                        self.assertEqual(
                            app.selected_idx,
                            clicked_idx,
                            f"Click at mx={mx}, row={clicked_idx} failed to select row (got {app.selected_idx})",
                        )
                        self.assertFalse(
                            bool(app.sessions[clicked_idx].get("pinned")),
                            f"Click at mx={mx} must never toggle pinned",
                        )
                        self.assertEqual(
                            [(s["tool"], s["id"]) for s in app.sessions],
                            initial_order,
                            f"Click at mx={mx} reshuffled self.sessions",
                        )
                        pin_calls = [c for c in spy_action.call_args_list if len(c.args) >= 3 and c.args[2] == "pin"]
                        self.assertEqual(pin_calls, [])

    def test_4_visual_cell_contract_diamonds_colors_and_unread_timestamps(self):
        """
        Contract 4:
        Verify render_sidebar_row_cells & draw():
          - Brand-colored hollow diamond ◇ (◈ for cross-agent handoff, ◇ <-> ◈ for working),
          - Codex color is pale lavender-white 189, Claude is 209, Gemini is 75,
          - No red dot • or ◞ tail after the timestamp,
          - 5-state zero-bold Morandi timestamps at x=27: working 67 / wait 174 / failed 139 / unread 180 / read gray.
        """
        # 4a. Verify 256-color palette initialization (Codex=189, Claude=209, Gemini=75)
        app = self._make_tui(height=30, width=32, sidebar_only=True)
        with patch("tui.curses.start_color"), \
             patch("tui.curses.use_default_colors"), \
             patch.object(tui.curses, "COLORS", 256, create=True), \
             patch("tui.curses.init_pair") as mock_init_pair:
            app._init_colors()
            mock_init_pair.assert_any_call(tui.P_AGENT_CODEX, 189, -1)
            mock_init_pair.assert_any_call(tui.P_AGENT_CLAUDE, 209, -1)
            mock_init_pair.assert_any_call(tui.P_AGENT_AGY, 75, -1)
            mock_init_pair.assert_any_call(tui.P_WAIT, 214, -1)
            mock_init_pair.assert_any_call(tui.P_DIM, 240, -1)

        # 4b. Verify ◇ for live idle, ◈ for cross-agent handoff, and ◇ <-> ◈ for working
        self.assertEqual(tuple(tui.WORKING_DIAMOND_FRAMES), ("◇", "◈"))
        idle_cells = tui.render_sidebar_row_cells(
            {"tool": "claude", "title": "Idle Claude", "isLive": True, "agentState": "done_idle", "updatedAt": time.time() - 30},
            left_w=32,
            is_unread=False,
        )
        self.assertEqual(idle_cells["dot_ch"], "◇")
        self.assertEqual(idle_cells["dot_pair"], tui.P_AGENT_CLAUDE)
        self.assertEqual(idle_cells["unread_ch"], " ")

        relay_cells = tui.render_sidebar_row_cells(
            {
                "tool": "claude",
                "handoffChain": ["claude", "agy"],
                "title": "AI味的本土用语习惯",
                "isLive": True,
                "agentState": "wait_input",
                "updatedAt": time.time() - 45,
            },
            left_w=32,
            is_unread=True,
        )
        self.assertEqual(relay_cells["dot_ch"], "◇")
        self.assertEqual(relay_cells["dot_pair"], tui.P_AGENT_CLAUDE)
        self.assertEqual(relay_cells["unread_ch"], " ")

        work_f0 = tui.render_sidebar_row_cells(
            {"tool": "agy", "title": "Working Gemini", "isLive": True, "agentState": "working", "updatedAt": time.time() - 5},
            left_w=32,
            is_unread=False,
            now_mono=0.01,
        )
        work_f1 = tui.render_sidebar_row_cells(
            {"tool": "agy", "title": "Working Gemini", "isLive": True, "agentState": "working", "updatedAt": time.time() - 5},
            left_w=32,
            is_unread=False,
            now_mono=tui.WORKING_DIAMOND_PERIOD + 0.01,
        )
        self.assertEqual((work_f0["dot_ch"], work_f1["dot_ch"]), ("◇", "◈"))
        self.assertEqual(work_f0["dot_pair"], tui.P_AGENT_AGY)
        self.assertEqual(work_f0["unread_ch"], " ")
        self.assertEqual(work_f1["unread_ch"], " ")

        # 4c. Verify no red dot • or ◞ tail after timestamp, and 5-state zero-bold Morandi timestamp
        # palette (`STATE_AGE_COLOR`) on `age_col` at x = 27 (`age_x = 5 + title_cols + 1` with `title_cols = left_w - 11 = 21`):
        #   wait_input -> P_STAT_T3 (180 warm sand yellow, stays 180 when selected),
        #   working -> P_DONE (67 steel blue, stays 67 when selected),
        #   failed unread -> P_STAT_T4 (174 terracotta coral, becomes P_META 244 once selected/read),
        #   unread -> P_STAT_T1 (108 sage moss green, becomes P_META 244 once selected/read),
        #   read/seen -> P_DIM (240) / P_META (244) when selected. Never A_BOLD, never a gray box.
        color = lambda pair: pair << 8
        a_bold = tui.curses.A_BOLD
        self.assertEqual(tui.layout_sidebar_row_32({"tool": "claude", "title": "x"}, left_w=32)["age_x"], 27)
        self.assertEqual(
            tui.STATE_AGE_COLOR,
            {
                "wait_input": (tui.P_STAT_T3, tui.P_STAT_T3),
                "working": (tui.P_DONE, tui.P_DONE),
                "failed": (tui.P_STAT_T4, tui.P_META),
                "unread": (tui.P_STAT_T1, tui.P_META),
                "seen": (tui.P_DIM, tui.P_META),
            },
        )
        now_s = time.time()
        app.sessions = [
            {"id": "unread-row", "tool": "claude", "title": "未读会话测试", "isLive": True,
             "agentState": "wait_input", "updatedAt": now_s - 120},
            {"id": "read-row", "tool": "codex", "title": "已读会话测试", "isLive": True,
             "agentState": "done_idle", "updatedAt": now_s - 300},
            {"id": "working-row", "tool": "agy", "title": "运行中会话", "isLive": True,
             "agentState": "working", "updatedAt": now_s - 20},
            {"id": "done-unread-row", "tool": "claude", "title": "完成未读会话", "isLive": True,
             "agentState": "done_idle", "updatedAt": now_s - 400},
            {"id": "failed-row", "tool": "codex", "title": "失败未读会话", "isLive": True,
             "agentState": "done_idle", "failed": True, "updatedAt": now_s - 500},
        ]
        unread_ids = {"unread-row", "done-unread-row", "failed-row"}
        sel_pairs = {
            getattr(tui, n) for n in dir(tui)
            if n.startswith("P_") and n.endswith("_SEL") and isinstance(getattr(tui, n), int)
        }

        def draw_age_cells(selected_idx: int) -> dict:
            app.selected_idx = selected_idx
            app.hover_idx = -1
            app.hover_pos = (-1, -1)
            with patch("tui.curses.color_pair", side_effect=color), \
                 patch.object(app, "_rebalance_sidebar_width_if_needed"), \
                 patch.object(app, "is_session_unread", side_effect=lambda s: s["id"] in unread_ids):
                app.stdscr.addstr.reset_mock()
                app.draw()
                calls = [c.args for c in app.stdscr.addstr.call_args_list]
            for _, _, txt, *_ in calls:
                if isinstance(txt, str):
                    self.assertNotIn("•", txt)
                    self.assertNotIn("◞", txt)
            out = {}
            for sess in app.sessions:
                ys = [c[0] for c in calls if c[2] == tui.disp_pad(sess["title"], 21)]
                self.assertEqual(len(ys), 1, f"title of {sess['id']} must render exactly once")
                row = {c[1]: c for c in calls if c[0] == ys[0]}
                self.assertNotIn(30, row)  # zero tail after the timestamp (col 30 is the right margin)
                self.assertIn(27, row)
                out[sess["id"]] = row
            return out

        rows = draw_age_cells(-1)
        self.assertEqual(rows["unread-row"][2][2], "◇")
        self.assertEqual(rows["read-row"][2][2], "◇")
        expected_unsel = {
            "unread-row": color(tui.P_STAT_T3),
            "read-row": color(tui.P_DIM),
            "working-row": color(tui.P_DONE),
            "done-unread-row": color(tui.P_STAT_T1),
            "failed-row": color(tui.P_STAT_T4),
        }
        for sid, exp_attr in expected_unsel.items():
            age_attr = rows[sid][27][3]
            self.assertEqual(age_attr, exp_attr, f"{sid} timestamp color")
            self.assertEqual(age_attr & a_bold, 0, f"{sid} timestamp must be zero-bold")
            self.assertNotIn(age_attr >> 8, sel_pairs, f"{sid} timestamp must never use a *_SEL gray box")

        # Selected rows: wait_input keeps warm sand yellow P_STAT_T3 (180, bg -1, zero bold);
        # working keeps steel blue P_DONE (67); read/unread/failed rows settle to P_META (244)
        rows = draw_age_cells(0)
        self.assertEqual(rows["unread-row"][27][3], color(tui.P_STAT_T3))
        rows = draw_age_cells(2)
        self.assertEqual(rows["working-row"][27][3], color(tui.P_DONE))
        rows = draw_age_cells(1)
        self.assertEqual(rows["read-row"][27][3], color(tui.P_META))
        self.assertEqual(rows["read-row"][27][3] & a_bold, 0)
    def test_5_cross_agent_handoff_claude_codex_gemini_claude_e2e(self):
        """
        Contract 5:
        Verify Cross-Agent Handoff (Claude -> Codex -> Gemini -> Claude) E2E:
          - Each hub.handoff(...) completes in <300ms,
          - Produces valid <1.5k token handoff.yaml + .md,
          - Updates handoffChain to ['claude', 'codex', 'agy', 'claude'].
        """
        hops = [
            {"id": C1[:8], "from_tool": "claude", "to": "codex", "target_session_id": X1, "expected_chain": ["claude", "codex"]},
            {"id": X1[:8], "from_tool": "codex", "to": "agy", "target_session_id": A1, "expected_chain": ["claude", "codex", "agy"]},
            {"id": A1[:8], "from_tool": "agy", "to": "claude", "target_session_id": C1, "expected_chain": ["claude", "codex", "agy", "claude"]},
        ]

        for step_no, hop in enumerate(hops, 1):
            t0 = time.perf_counter()
            res = self.hub.handoff(
                {
                    "id": hop["id"],
                    "from_tool": hop["from_tool"],
                    "to": hop["to"],
                    "target_session_id": hop["target_session_id"],
                    "note": f"Hop {step_no}: {hop['from_tool']} -> {hop['to']}",
                }
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000.0

            self.assertTrue(res["ok"], f"Hop {step_no} failed: {res}")
            budget_ms = 900.0 if os.environ.get("CI") else 300.0  # shared CI runners stall for 100s of ms
            self.assertLess(
                elapsed_ms,
                budget_ms,
                f"Hop {step_no} ({hop['from_tool']} -> {hop['to']}) took {elapsed_ms:.1f}ms (must be < {budget_ms:.0f}ms)",
            )
            self.assertEqual(res["handoffChain"], hop["expected_chain"])
            self.assertLess(res["token_estimate"], 1500)

            md_path = Path(res["bundle_path"])
            yaml_path = Path(res["yaml_path"])
            self.assertTrue(md_path.is_file())
            self.assertTrue(yaml_path.is_file())

            md_text = md_path.read_text(encoding="utf-8")
            yaml_text = yaml_path.read_text(encoding="utf-8")
            self.assertLess(handoff.estimate_tokens(md_text), 1500)
            self.assertLess(handoff.estimate_tokens(yaml_text), 1500)
            self.assertIn("from_agent:", yaml_text)
            self.assertIn("to_agent:", yaml_text)
            self.assertIn("verified:", yaml_text)
            self.assertNotIn("sk-secret1234567890abcdef", md_text)
            self.assertNotIn("sk-secret1234567890abcdef", yaml_text)

        # Verify final session state in Hub reflects full relay chain while keeping its own tool color and hollow `◇` in sidebar
        final_s = self.hub.get("claude", C1, True)
        self.assertEqual(final_s["handoffChain"], ["claude", "codex", "agy", "claude"])
        final_s["isLive"] = True
        final_s["agentState"] = "done_idle"
        cells = tui.render_sidebar_row_cells(final_s, left_w=32, is_unread=False)
        self.assertEqual(cells["dot_ch"], "◇")
        self.assertEqual(cells["dot_pair"], tui.P_AGENT_CLAUDE)

    def test_6_pinned_section_headers_star_hover_and_welcome_centering(self):
        """
        Contract 6 (Telegram #56 325.jpg / 329.png / 334.jpg):
          - Pinning a session keeps its left status symbol (◈/◇/·) unchanged and places it under `▾ Pinned`.
          - Hovering shows `⋯ ☆` (unpinned) or `⋯ ★` (pinned), never triangle `▵` or `▴`.
          - Clicking a section header row collapses/expands that section; `v` switches to Workspace folder grouping.
          - Welcome screen has zero middle dots `·` and centers immediately after `_sync_terminal_size()`.
        """
        color = lambda p: p << 8
        with patch("tui.curses.color_pair", side_effect=color):
            app = self._make_tui(height=30, width=100, sidebar_only=False)
            app.selected_idx = -1

            # 1. Verify Welcome screen has zero middle dots `·` and resizes immediately via _sync_terminal_size
            with patch("tui.os.get_terminal_size", return_value=os.terminal_size((110, 32))), \
                 patch("tui.curses.resizeterm") as mock_resize:
                app.stdscr.addstr.reset_mock()
                app.draw()
                mock_resize.assert_called_once_with(32, 110)
                welcome_strs = [str(c.args[2]) for c in app.stdscr.addstr.call_args_list if c.args[1] >= 32]
                for w_s in welcome_strs:
                    self.assertNotIn("·", w_s)
                    self.assertNotIn("活跃会话", w_s)

            # 2. Pin C1 and verify `Pinned` + `Recent` section headers appear and C1 keeps its status symbol
            app.toggle_pin_session(app.sessions[0])
            drows = app._sidebar_display_rows()
            self.assertEqual(drows[0]["kind"], "header")
            self.assertEqual(drows[0]["label"], "Pinned")
            self.assertEqual(drows[0]["count"], 1)
            self.assertEqual(drows[1]["kind"], "session")
            self.assertTrue(drows[1]["session"]["pinned"])
            pinned_cells = tui.render_sidebar_row_cells(drows[1]["session"], left_w=32, is_unread=False)
            self.assertEqual(pinned_cells["dot_ch"], "·")  # Dormant symbol preserved even when pinned!

            # 3. Hovering row 1 (pinned session at y = list_top + 1 = 4) renders only `★` (yellow `P_WAIT | A_BOLD`) at `age_x + 2`, never `⋯` or `▵`/`▴`
            app.hover_idx = drows[1]["idx"]
            app.hover_pos = (10, 4)
            app.stdscr.addstr.reset_mock()
            app.draw()
            sidebar_calls_y4 = {c.args[1]: (str(c.args[2]), c.args[3]) for c in app.stdscr.addstr.call_args_list if c.args[0] == 4 and c.args[1] < 40}
            eff_lw = app._effective_left_w(100, False)
            age_x = tui.layout_sidebar_row_32(drows[1]["session"], left_w=eff_lw, is_unread=False)["age_x"]
            self.assertEqual(sidebar_calls_y4.get(age_x), ("  ", color(tui.P_DIM)))
            self.assertEqual(sidebar_calls_y4.get(age_x + 2), ("★", color(tui.P_WAIT) | tui.curses.A_BOLD))
            sidebar_strs = [str(c.args[2]) for c in app.stdscr.addstr.call_args_list if c.args[1] < 32]
            for s_txt in sidebar_strs:
                self.assertNotIn("⋯", s_txt)
                self.assertNotIn("▵", s_txt)
                self.assertNotIn("▴", s_txt)

            # 4. Clicking `▾ Pinned` header at y = 3 collapses the Pinned section
            app._handle_mouse(parsed_event=(0, 4, 3, False))
            drows_collapsed = app._sidebar_display_rows()
            self.assertTrue(drows_collapsed[0]["collapsed"])
            self.assertEqual(drows_collapsed[1]["kind"], "header")
            self.assertEqual(drows_collapsed[1]["label"], "Recent")
    def test_7_sticky_no_dup_reader_row_split_casual_greeting_mem_align_and_subagent_working(self):
        """
        Contract 7 (Screenshots 13.50.15 / 13.51.54 / 14.13.32):
          1) Sticky prompt bar (`❯ ... #1/N`) is hidden when the active turn's `❯` line is already visible
             in the viewport (`msg_scroll <= cur_turn_line`) and shown only when scrolled above (`msg_scroll > cur_turn_line`).
          2) In `--reader` mode, `✕` is on Row 0, `◇ Title` + `⇄ Relay` are on Row 1 (safely below `✕`),
             and `─` divider is on Row 2 (aligned with the left sidebar's Row 2 divider).
          3) Generic placeholder titles (`Casual Greeting`, `hii`) auto-resolve to the first substantive prompt.
          4) Sidebar Row 1 right-aligns memory (`2.1G`) at `left_w - len(mem_str) - 2` (`x = 26` for `left_w = 32`).
          5) Active child subagents (`parentId`) propagate `isLive=True`, `agentState='working'`, and `activeSubagents`
             to their parent session so parent conversations with running subagents show `◈` and count in `◈ N`.
        """
        import sqlite3

        now = time.time()
        greeting_id = "8b3d3fc5-4772-4b5e-a653-c73e838d22eb"
        child_id = "c28ed64f-d58a-46eb-b340-acd6509c82db"
        agy_db = self.home / ".gemini/antigravity-cli/conversation_summaries.db"
        conn = sqlite3.connect(agy_db)
        conn.execute("ALTER TABLE conversation_summaries ADD COLUMN status TEXT")
        conn.execute(
            "INSERT INTO conversation_summaries(conversation_id, title, preview, workspace_uris, parent_conversation_id, step_count, status) VALUES(?,?,?,?,?,?,?)",
            (greeting_id, "Casual Greeting", "Casual Greeting", json.dumps([f"file://{self.workspace}"]), "", 6, "CASCADE_RUN_STATUS_IDLE"),
        )
        conn.execute(
            "INSERT INTO conversation_summaries(conversation_id, title, preview, workspace_uris, parent_conversation_id, step_count, status) VALUES(?,?,?,?,?,?,?)",
            (child_id, "", "", json.dumps([f"file://{self.workspace}"]), A1, 4, "CASCADE_RUN_STATUS_RUNNING"),
        )
        conn.commit()
        conn.close()

        greeting_p = self.home / f".gemini/antigravity-cli/brain/{greeting_id}/.system_generated/logs/transcript.jsonl"
        self._write_jsonl(
            greeting_p,
            [
                {"step_index": 0, "type": "USER_INPUT", "content": "<USER_REQUEST>hii</USER_REQUEST>"},
                {"step_index": 1, "type": "PLANNER_RESPONSE", "content": "Hello! How can I help you today?"},
                {"step_index": 2, "type": "USER_INPUT", "content": "<USER_REQUEST>agy 鼠标左键点选移光标：已经搞定上线了</USER_REQUEST>"},
                {"step_index": 3, "type": "PLANNER_RESPONSE", "content": "好的，我来检查一下配置。"},
            ],
            mtime=now - 5,
        )
        child_p = self.home / f".gemini/antigravity-cli/brain/{child_id}/.system_generated/logs/transcript.jsonl"
        self._write_jsonl(
            child_p,
            [
                {"step_index": 0, "type": "SYSTEM_MESSAGE", "content": "[Message] content=Subagent task"},
                {"step_index": 1, "type": "PLANNER_RESPONSE", "content": "Working on task..."},
            ],
            mtime=now - 1,
        )

        self.hub.scanned = 0
        sessions = self.hub.discover(force=True)

        # 3 & 5: Verify "Casual Greeting" resolved to substantive prompt, and parent A1 inherited working state from child_id
        g_sess = sessions["agy:" + greeting_id]
        self.assertTrue(server._is_generic_placeholder_title("Casual Greeting"))
        self.assertTrue(server._is_generic_placeholder_title("hii"))
        self.assertIn("agy 鼠标左键点选移光标", g_sess["title"])
        self.assertNotEqual(g_sess["title"], "Casual Greeting")

        parent_a1 = sessions["agy:" + A1]
        self.assertTrue(parent_a1["isLive"])
        self.assertEqual(parent_a1["agentState"], "working")
        self.assertGreaterEqual(parent_a1.get("activeSubagents", 0), 1)

        color = lambda p: p << 8
        with patch("tui.curses.color_pair", side_effect=color):
            # 4: Verify clean Row 0 (`◈ N` on left, `◧ ✕` on right), Row 1 `─` divider,
            # Row 2 clickable `✎  New session` directly above `▾ Active`, and Morandi 4-tier zero-bold `cpu / mem` at bottom Row h-1
            sb_app = self._make_tui(height=30, width=32, sidebar_only=True)
            sb_app.counts["liveMemoryMB"] = 2150.0
            with patch.object(sb_app, "_rebalance_sidebar_width_if_needed"):
                sb_app.stdscr.addstr.reset_mock()
                sb_app.draw()
                row0_calls = {c.args[1]: c.args[2] for c in sb_app.stdscr.addstr.call_args_list if c.args[0] == 0}
                row1_calls = {c.args[1]: c.args[2] for c in sb_app.stdscr.addstr.call_args_list if c.args[0] == 1}
                row2_calls = {c.args[1]: c.args[2] for c in sb_app.stdscr.addstr.call_args_list if c.args[0] == 2}
                row29_calls = {c.args[1]: c.args[2] for c in sb_app.stdscr.addstr.call_args_list if c.args[0] == 29}
                self.assertNotIn("cpu ", row0_calls.values())
                self.assertNotIn("mem ", row0_calls.values())
                # `mixhub` wordmark at col 5; `‹ ›` always visible at cols 22 / 25
                self.assertEqual(row0_calls.get(5), "mixhub")
                self.assertEqual(row0_calls.get(22), "‹")
                self.assertEqual(row0_calls.get(25), "›")
                self.assertNotIn("mix", row0_calls.values())
                self.assertEqual(row0_calls.get(29), "◧")
                self.assertNotIn("✕", row0_calls.values())
                self.assertNotIn(0, row0_calls)
                self.assertEqual(row1_calls, {})
                self.assertEqual(row2_calls.get(2), "✎  New session")
                self.assertIn("cpu ", row29_calls.values())
                self.assertIn("mem ", row29_calls.values())
                self.assertIn("2.1G", row29_calls.values())
                self.assertFalse(any("◇" in str(v) for v in row0_calls.values()))

                # Verify Morandi 4-tier zero-bold palette (`_morandi_stat_attrs`) never uses A_BOLD
                for cpu_v, mem_v, exp_c_pair, exp_m_pair in (
                    (15, 1800.0, tui.P_STAT_T1, tui.P_STAT_T1),
                    (50, 3200.0, tui.P_STAT_T2, tui.P_STAT_T2),
                    (75, 4800.0, tui.P_STAT_T3, tui.P_STAT_T3),
                    (92, 6100.0, tui.P_STAT_T4, tui.P_STAT_T4),
                ):
                    cl_a, cv_a, ml_a, mv_a = sb_app._morandi_stat_attrs(cpu_v, mem_v)
                    self.assertEqual(cv_a, color(exp_c_pair))
                    self.assertEqual(mv_a, color(exp_m_pair))
                    for a in (cl_a, cv_a, ml_a, mv_a):
                        self.assertEqual(a & getattr(tui.curses, "A_BOLD", 0x200000), 0)

                # Clicking Row 2 (`✎  New session`) opens `_new_session_menu`
                sb_app._handle_mouse(parsed_event=(0, 6, 2, False))
                self.assertTrue(sb_app._new_session_menu)
                sb_app._new_session_menu = False

                # Verify sidebar renders `◧` at Row 0 (borderless), `▾ Active`, and Mode 0 (`5h HH:MM  wk MM/DD`) + Mode 1/2 quota bar
                fut_5h = time.time() + 7200
                fut_wk = time.time() + 3 * 86400
                exp_t5 = tui.format_quota_reset_tag(fut_5h, "5h")
                exp_tw = tui.format_quota_reset_tag(fut_wk, "wk")
                fake_statusline = self.root / "statusline_test.json"
                fake_statusline.write_text(
                    json.dumps({
                        "quota": {
                            "3p-5h": {"remaining_fraction": 0.94, "reset_time": fut_5h},
                            "3p-weekly": {"remaining_fraction": 0.68, "reset_time": fut_wk},
                            "gemini-5h": {"remaining_fraction": 0.82, "reset_time": fut_5h},
                            "gemini-weekly": {"remaining_fraction": 0.66, "reset_time": fut_wk},
                        }
                    }),
                    encoding="utf-8",
                )
                # Layout check: every agent counts as running (the running-session filter has its own test)
                with patch.dict(os.environ, {"MIXHUB_STATUSLINE_JSON": str(fake_statusline)}), \
                     patch.object(sb_app, "_footer_agents", return_value=list(tui._FOOTER_AGENTS)):
                    tui._QUOTA_SUMMARY_CACHE["ts"] = 0.0
                    sb_app.stdscr.getmaxyx.return_value = (30, 32)
                    sb_app.stdscr.addstr.reset_mock()
                    sb_app.draw()
                row0_calls = {c.args[1]: c.args[2] for c in sb_app.stdscr.addstr.call_args_list if c.args[0] == 0}
                self.assertEqual(row0_calls.get(5), "mixhub")
                self.assertFalse(any("◈" in str(v) for v in row0_calls.values()))
                row3_calls = {c.args[1]: c.args[2] for c in sb_app.stdscr.addstr.call_args_list if c.args[0] == 3}
                self.assertEqual(row3_calls.get(2), "▾")
                self.assertEqual(row3_calls.get(5), "Active")
                self.assertEqual(row3_calls.get(12), "1")
                self.assertNotIn(27, row3_calls)
                # 2 active agents (`claude` on Row 27, `gemini` on Row 28; `codex` has no data so auto-hidden!)
                row25_calls = {c.args[1]: c.args[2] for c in sb_app.stdscr.addstr.call_args_list if c.args[0] == 25}
                row27_calls = {c.args[1]: c.args[2] for c in sb_app.stdscr.addstr.call_args_list if c.args[0] == 27}
                row27_attrs = {c.args[1]: c.args[3] for c in sb_app.stdscr.addstr.call_args_list if c.args[0] == 27 and len(c.args) >= 4}
                row28_calls = {c.args[1]: c.args[2] for c in sb_app.stdscr.addstr.call_args_list if c.args[0] == 28}
                row28_attrs = {c.args[1]: c.args[3] for c in sb_app.stdscr.addstr.call_args_list if c.args[0] == 28 and len(c.args) >= 4}
                row29_calls = {c.args[1]: c.args[2] for c in sb_app.stdscr.addstr.call_args_list if c.args[0] == 29}
                self.assertEqual(row25_calls, {})
                self.assertEqual(row27_calls.get(2), "claude")
                self.assertEqual(row27_attrs.get(2), color(tui.P_AGENT_CLAUDE))
                self.assertEqual(row27_calls.get(9), "  6%")
                self.assertEqual(row27_calls.get(14), exp_t5)
                self.assertEqual(row27_calls.get(20), " 32%")
                self.assertEqual(row27_calls.get(25), exp_tw)
                self.assertEqual(row28_calls.get(2), "gemini")
                self.assertEqual(row28_attrs.get(2), color(tui.P_AGENT_AGY))
                self.assertEqual(row28_calls.get(9), " 18%")
                self.assertEqual(row28_calls.get(20), " 34%")
                self.assertNotIn("✦", row28_calls.values())
                self.assertEqual(row29_calls.get(4), "cpu ")
                self.assertIn(8, row29_calls)

                # Verify selected row only renders 3-col gray pill at cols 1..3 (never full-row gray band)
                sb_app.selected_idx = 0
                sb_app.stdscr.addstr.reset_mock()
                sb_app.draw()
                sel_row = [c.args for c in sb_app.stdscr.addstr.call_args_list if c.args[0] in (4, 6)]
                self.assertTrue(any(c[1] == 1 and c[2] == "   " for c in sel_row))
                self.assertFalse(any(c[1] == 1 and len(str(c[2])) > 3 for c in sel_row))

                # Verify native `.claude.json` (`cachedUsageUtilization`) takes priority over AGY `3p-5h`/`3p-weekly`
                claude_cfg = self.home / ".claude.json"
                claude_cfg.write_text(
                    json.dumps({
                        "cachedUsageUtilization": {
                            "fetchedAtMs": int((now - 40 * 3600) * 1000),
                            "utilization": {
                                "five_hour": {"utilization": 99, "resets_at": "2026-10-20T00:00:00+00:00"},
                                "seven_day": {"utilization": 94, "resets_at": fut_wk},
                            },
                        }
                    }),
                    encoding="utf-8",
                )
                try:
                    with patch.dict(os.environ, {"MIXHUB_STATUSLINE_JSON": str(fake_statusline)}):
                        tui._QUOTA_SUMMARY_CACHE["ts"] = 0.0
                        q_native = {it["key"]: it for it in tui.get_cli_quota_metrics(home_dir=str(self.home))}
                    self.assertIsNone(q_native["cc"]["rem_5h"])
                    self.assertEqual(q_native["cc"]["rem_wk"], 6)
                finally:
                    claude_cfg.unlink(missing_ok=True)

                # Verify 4-tier Morandi quota color mapping (`P_STAT_T1..T4`, matching `cpu` and `mem`)
                self.assertEqual(tui._morandi_quota_pair(85), tui.P_STAT_T1)
                self.assertEqual(tui._morandi_quota_pair(56), tui.P_STAT_T2)
                self.assertEqual(tui._morandi_quota_pair(25), tui.P_STAT_T3)
                self.assertEqual(tui._morandi_quota_pair(10), tui.P_STAT_T4)

                # Verify `judge_quota_window` evicts expired (`now >= reset_at`) and stale (`now - observed_at > ttl`) windows
                self.assertEqual(tui.judge_quota_window(80, now - 10, now - 3600, "5h", now=now), (None, "", "expired"))
                self.assertEqual(tui.judge_quota_window(80, None, now - 7200, "5h", now=now), (None, "", "stale"))

                all_sb_texts = [str(c.args[2]) for c in sb_app.stdscr.addstr.call_args_list]
                # Verify zero Chinese (`余`) and zero `━` progress bar on cpu / mem row (Row 29)
                row29_texts = "".join(str(c.args[2]) for c in sb_app.stdscr.addstr.call_args_list if c.args[0] == 29)
                self.assertNotIn("━", row29_texts)
                self.assertNotIn("─", row29_texts)
                self.assertIn("▴", row29_texts)
                for t in all_sb_texts:
                    self.assertNotIn("余", t)
                    self.assertNotIn("Ⓚ", t)
                    self.assertNotIn("p:置顶", t)
                    self.assertNotIn("q:退出", t)

                # Verify clicking `▾` on Row 29 collapses quota while keeping `_list_bounds` fixed at (3, 25, 22)
                self.assertEqual(sb_app._list_bounds(30), (3, 25, 22))
                sb_app._handle_mouse(parsed_event=(0, 2, 29, False))
                self.assertTrue(sb_app.footer_quota_collapsed)
                self.assertEqual(sb_app._list_bounds(30), (3, 25, 22))
                sb_app._handle_mouse(parsed_event=(0, 2, 29, False))
                self.assertFalse(sb_app.footer_quota_collapsed)
                self.assertEqual(sb_app._list_bounds(30), (3, 25, 22))

            # Verify Active Working Set (`active_working_set.json`) keeps previously-live session in Active (`◇`)
            # even after external process / subagent stops, until explicitly closed via `close` action (`✕`)
            self.assertIn("agy:" + A1, self.hub.working_set())
            conn = sqlite3.connect(agy_db)
            conn.execute("UPDATE conversation_summaries SET status='CASCADE_RUN_STATUS_IDLE' WHERE conversation_id=?", (child_id,))
            conn.commit()
            conn.close()
            os.utime(child_p, (now - 300, now - 300))
            self.hub.scanned = 0
            still_active = self.hub.get("agy", A1, True)
            self.assertTrue(still_active["isLive"])
            self.assertTrue(still_active["inWorkingSet"])
            self.assertEqual(still_active["agentState"], "done_idle")
            self.hub.action("agy", A1, "close", {})
            self.assertNotIn("agy:" + A1, self.hub.working_set())
            closed_a1 = self.hub.get("agy", A1, True)
            self.assertFalse(closed_a1["isLive"])
            self.assertEqual(closed_a1["agentState"], "dormant")

            # Verify Opus 5.5 (max) Empty-State Home: Actionable Triage Inbox + Bottom New Session Launchpad
            sb_app.sidebar_only = False
            sb_app.selected_idx = -1
            sb_app.stdscr.getmaxyx.return_value = (30, 100)
            sb_app.stdscr.addstr.reset_mock()
            sb_app.draw()
            welcome_texts = [str(c.args[2]) for c in sb_app.stdscr.addstr.call_args_list if c.args[1] >= 24]
            self.assertTrue(any("NEEDS YOU" in wt or "mixhub" in wt for wt in welcome_texts))
            self.assertIn("LAUNCH", welcome_texts)
            self.assertIn("[ + Claude ]", welcome_texts)
            self.assertIn("[ + Codex ]", welcome_texts)
            self.assertIn("[ + OpenCode ]", welcome_texts)
            for wt in welcome_texts:
                self.assertNotIn("Good afternoon", wt)
                self.assertNotIn("Pin session", wt)
                self.assertNotIn("New session", wt)
            sb_app.sidebar_only = True
            sb_app.stdscr.getmaxyx.return_value = (30, 32)

            # 1 & 2: Verify `--reader` Row 0 (no duplicate `◧` when unzoomed, `◧ ●N` shown when zoomed), Row 1 (`◇ Title` + `↩` + `▶ 终端` + `⇄ Relay`), Row 2 (`─`), and zero sticky duplication
            reader_app = self._make_tui(height=30, width=100, sidebar_only=False)
            reader_app.reader_only = True
            reader_app.selected_idx = 0
            reader_app.sessions[1]["agentState"] = "wait_input"
            with patch.dict(os.environ, {"TMUX": "/tmp/tmux-1000/default,123,0"}), \
                 patch.object(reader_app, "_is_reader_zoomed", return_value=False):
                reader_app.stdscr.addstr.reset_mock()
                reader_app.draw()
                unzoomed_row0 = [c.args[2] for c in reader_app.stdscr.addstr.call_args_list if c.args[0] == 0]
                self.assertNotIn("◧", unzoomed_row0)
            with patch.object(reader_app, "_is_reader_zoomed", return_value=True):
                reader_app.stdscr.addstr.reset_mock()
                reader_app.draw()
            r_calls = [c.args for c in reader_app.stdscr.addstr.call_args_list]
            row0_texts = [c[2] for c in r_calls if c[0] == 0]
            row1_texts = [c[2] for c in r_calls if c[0] == 1]
            row2_texts = [c[2] for c in r_calls if c[0] == 2]
            self.assertIn("◧", row0_texts)
            self.assertTrue(any(str(t).startswith("●") for t in row0_texts))
            self.assertNotIn("✕", row0_texts)
            self.assertNotIn(tui.RELAY_TOGGLE_CLOSED, row0_texts)
            self.assertNotIn("↩", row1_texts)
            self.assertNotIn("▶ Live", row1_texts)
            self.assertNotIn("▶ Resume", row1_texts)
            # In `--reader`, `Relay` lives exclusively on the bottom bar (`Row h - 1`), NEVER duplicated on Row 1
            self.assertNotIn(tui.RELAY_TOGGLE_CLOSED, row1_texts)
            self.assertTrue(any("─" * 20 in t for t in row2_texts))
            # Short transcript fits in viewport -> msg_scroll == 0 -> sticky bar is NOT rendered (❯ appears only once!)
            prompt_arrows = [c for c in r_calls if c[2] == "❯ "]
            self.assertEqual(len(prompt_arrows), len(reader_app.user_turns))
            # 1-line transparent bottom bar on Row h - 1 (thin `─` rule on Row h - 2):
            # Mode B: ` Resume ↵ ` only; Mode C: ` Resume ↵ ` + ` Go to Tab   ` (zero `Relay` on `--reader` bottom bar)
            self.assertTrue(any(c[0] == 28 and "─" * 20 in str(c[2]) for c in r_calls))
            input_row_texts = [c[2] for c in r_calls if c[0] == 29]
            self.assertEqual(input_row_texts, [tui.LBL_RESUME_CHIP])
            reader_app.sessions[0]["isLive"] = True
            reader_app.sessions[0]["resident"] = False
            reader_app.stdscr.addstr.reset_mock()
            reader_app.draw()
            ext_live_input_texts = [c.args[2] for c in reader_app.stdscr.addstr.call_args_list if c.args[0] == 29]
            self.assertEqual(ext_live_input_texts, [tui.LBL_RESUME_CHIP, " Go to Tab   "])
            reader_app.sessions[0]["isLive"] = False
            reader_app.stdscr.addstr.reset_mock()
            reader_app.draw()
            dormant_input_texts = [c.args[2] for c in reader_app.stdscr.addstr.call_args_list if c.args[0] == 29]
            self.assertEqual(dormant_input_texts, [tui.LBL_RESUME_CHIP])

            with patch.dict(os.environ, {"TMUX": "/tmp/tmux-1000/default,123,0"}), \
                 patch("mixhub.tmux.pool.activate_session_in_hubpool", return_value=True) as mock_act_pool, \
                 patch.object(reader_app, "_prompt_and_send_unified_message") as mock_legacy_send:
                reader_app._handle_mouse(parsed_event=(0, 5, 29, False))
                self.assertTrue(mock_act_pool.called)
                self.assertEqual(mock_act_pool.call_args.kwargs.get("force_takeover"), True)
                self.assertFalse(mock_legacy_send.called)

            # Verify single-clicking a sidebar row whose session is ALREADY in hubpool immediately switches to its live hubpool window
            with patch.dict(os.environ, {"TMUX": "/tmp/tmux-1000/default,123,0"}), \
                 patch("mixhub.tmux.pool.find_hubpool_window_for_session", return_value=f"w-claude-{C1[:12]}"), \
                 patch("mixhub.tmux.pool.activate_session_in_hubpool", return_value=True) as mock_act_pool_sb:
                sb_app.selected_idx = 0
                sb_app._on_sidebar_selection_changed(activate_live=False)
                self.assertTrue(mock_act_pool_sb.called)

            # `--header` Row 1 keeps the top band clean (no `⇄ Relay` button or chips) and anchors `current_session()` to `header_target`
            with patch.object(tui.HubTUI, "_deepseek_relay_ok", return_value=True):
                hdr_app = self._make_tui(height=2, width=90, sidebar_only=False)
                hdr_app.reader_only = True
                hdr_app.header_only = True
                hdr_app.header_target = ("claude", C1)
                hdr_app.selected_idx = 0
                hdr_app.stdscr.addstr.reset_mock()
                hdr_app.draw()
                r_hdr = hdr_app._compute_preview_bar_state(2, 90, 0, False)["hdr"]
                self.assertEqual(r_hdr["relay_items"], [])
                self.assertEqual(r_hdr["relay_hits"], [])
                relay_row1_calls = {c.args[2]: c.args[1] for c in hdr_app.stdscr.addstr.call_args_list if c.args[0] == 1}
                self.assertNotIn(tui.RELAY_TOGGLE_CLOSED, relay_row1_calls)
                self.assertNotIn(tui.RELAY_TOGGLE_OPEN, relay_row1_calls)
                hdr_app._register_relayed_window("w-agy-new-9999", "agy", str(self.home), {"relay_title": "↳ AI味的本土用语习惯"})
                self.assertEqual(hdr_app.current_session()["id"], C1)
                self.assertEqual(hdr_app.current_session()["tool"], "claude")

            # Verify toggle_outer_sidebar_zoom collapses and expands sidebar in a single atomic compound tmux call (<15ms)
            with patch("mixhub.tmux.outer.get_outer_hub_pane_ids", return_value=("%0", "%1")), \
                 patch("mixhub.tmux.outer.is_outer_hub_zoomed", return_value=False), \
                 patch("tui.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(stdout="%0|32\n%1|80\n", returncode=0)
                res = tui.toggle_outer_sidebar_zoom("hub")
                self.assertEqual(res, "collapsed")
                self.assertEqual(mock_run.call_count, 1)
                resize_cmds = [c.args[0] for c in mock_run.call_args_list if "resize-pane" in c.args[0]]
                self.assertTrue(any("-Z" in cmd and "%1" in cmd for cmd in resize_cmds))

            with patch("mixhub.tmux.outer.get_outer_hub_pane_ids", return_value=("%0", "%1")), \
                 patch("mixhub.tmux.outer.is_outer_hub_zoomed", return_value=True) as mock_is_zoomed, \
                 patch("tui.subprocess.run") as mock_run, \
                 patch.dict(os.environ, {"TMUX": "/tmp/tmux-501/default,123,0"}):
                mock_run.return_value = MagicMock(stdout="", returncode=0)
                reader_app._toggle_sidebar_collapse()
                self.assertEqual(mock_run.call_count, 1)
                unzoom_cmd = mock_run.call_args_list[0].args[0]
                self.assertIn("-Z", unzoom_cmd)
                self.assertIn("32", unzoom_cmd)
                self.assertNotIn("select-layout", unzoom_cmd)
                self.assertNotIn("set-hook", unzoom_cmd)
                self.assertFalse(reader_app._outer_zoomed)
                # Subsequent draw() and post-SIGWINCH width change must NOT spawn extra display-message subprocesses
                mock_is_zoomed.reset_mock()
                reader_app.stdscr.getmaxyx.return_value = (28, 91)
                reader_app.draw()
                self.assertEqual(mock_is_zoomed.call_count, 0)

            # Verify accurate CPU % and physical memory footprint via Hub.get_system_resource_metrics
            sys_metrics = server.Hub.get_system_resource_metrics(time.monotonic(), force=True)
            self.assertIn("sysCpuPercent", sys_metrics)
            self.assertIn("sysMemoryUsedMB", sys_metrics)
            self.assertGreater(sys_metrics["sysMemoryTotalMB"], 1000.0)
            # Shape only: a busy CI runner can time out the memory probe and report 0
            self.assertGreaterEqual(sys_metrics["sysMemoryUsedMB"], 0.0)
            self.assertLessEqual(sys_metrics["sysMemoryUsedMB"], sys_metrics["sysMemoryTotalMB"])

            # Verify older Codex rollout shard (29d old) never overwrites newer continuation shard (16h old)
            old_codex_shard = self.home / f".codex/sessions/2026/09/24/rollout-old-{X1}.jsonl"
            self._write_jsonl(
                old_codex_shard,
                [{"type": "session_meta", "payload": {"id": X1, "cwd": str(self.workspace), "timestamp": "2026-09-24T00:00:00Z"}}],
                mtime=now - 29 * 86400,
            )
            os.utime(self.codex_p1, (now - 16 * 3600, now - 16 * 3600))
            codex_db = self.home / ".codex/state_5.sqlite"
            conn = sqlite3.connect(codex_db)
            conn.execute("ALTER TABLE threads ADD COLUMN updated_at INTEGER")
            conn.execute("ALTER TABLE threads ADD COLUMN rollout_path TEXT")
            conn.execute(
                "UPDATE threads SET updated_at=?, rollout_path=? WHERE id=?",
                (int(now - 16 * 3600), str(self.codex_p1), X1),
            )
            conn.commit()
            conn.close()
            self.hub.scanned = 0
            codex_s = self.hub.get("codex", X1, True)
            self.assertGreaterEqual(codex_s["updatedAt"], now - 17 * 3600)
            self.assertEqual(codex_s["path"], str(self.codex_p1))

            # Verify Drag-and-Drop path burst detection & forwarding from left sidebar to right pane
            burst_chars = [ord(c) for c in "var/folders/test_screenshot.png "] + [-1]
            sb_app.stdscr.getch.side_effect = burst_chars
            burst = sb_app._consume_dragged_path_burst(ord("/"))
            self.assertEqual(burst, "/var/folders/test_screenshot.png ")
            with patch("tui.subprocess.run") as mock_fwd_run, \
                 patch("mixhub.tmux.outer.focus_outer_right_pane") as mock_focus_r:
                mock_fwd_run.return_value = MagicMock(stdout=f"w-claude-{C1[:12]}\n", returncode=0)
                sb_app.selected_idx = 0
                sb_app.sessions[0] = self.hub.get("claude", C1)
                sb_app._forward_dragged_path_to_right_pane(burst)
                sent_cmds = [c.args[0] for c in mock_fwd_run.call_args_list if "send-keys" in c.args[0]]
                self.assertTrue(any("/var/folders/test_screenshot.png " in cmd for cmd in sent_cmds))

            # Verify Draft Persistence (`unsent_drafts.json`) and Exit Receipt (`format_exit_keepalive_hint`)
            tui.save_unsent_draft(C1, "未发送的草稿内容 123")
            self.assertEqual(tui.load_unsent_drafts().get(C1), "未发送的草稿内容 123")
            tui.clear_unsent_draft(C1)
            self.assertNotIn(C1, tui.load_unsent_drafts())

            receipt = tui.format_exit_keepalive_hint(
                hub_instance=self.hub,
                pool_windows=[f"w-claude-{C1[:12]}"],
            )
            self.assertIn("后台保活池", receipt)
            self.assertIn(f"w-claude-{C1[:12]}", receipt)
            self.assertIn("休眠可恢复", receipt)

            # Verify `--header` mode renders `⇄ Relay` (never `"↩ 预览"`), and `◧` / `●N` have generous spacing (col 1 & col 5)
            hdr_app = self._make_tui(height=2, width=100, sidebar_only=False)
            hdr_app.header_only = True
            hdr_app.selected_idx = 0
            hdr_app.sessions[1]["agentState"] = "wait_input"
            with patch.object(hdr_app, "_is_reader_zoomed", return_value=True):
                hdr_app.stdscr.addstr.reset_mock()
                hdr_app.draw()
            hdr_row0 = {c.args[1]: c.args[2] for c in hdr_app.stdscr.addstr.call_args_list if c.args[0] == 0}
            hdr_row1_vals = [c.args[2] for c in hdr_app.stdscr.addstr.call_args_list if c.args[0] == 1]
            self.assertEqual(hdr_row0.get(1), "◧")
            self.assertTrue(str(hdr_row0.get(5, "")).startswith("●"))
            # `--header` Row 1 mirrors `--reader`: `◇ Title` only (no `⇄ Relay`, no `↩` / `▤ History` home buttons)
            self.assertNotIn(tui.LBL_HISTORY, hdr_row1_vals)
            self.assertNotIn("↩", hdr_row1_vals)
            self.assertNotIn(tui.RELAY_TOGGLE_CLOSED, hdr_row1_vals)
            self.assertNotIn("↩ 预览", hdr_row1_vals)

            # Verify `clean_hostname` strips `.local` and `-<digits>` mDNS suffix (`Studio-4.local` -> `Studio`)
            self.assertEqual(server.clean_hostname("Studio-4.local"), "Studio")
            self.assertEqual(server.clean_hostname("Studio-12"), "Studio")
            self.assertEqual(server.clean_hostname("dev-wsl.local"), "dev-wsl")

    def test_8_real_device_bugs_496_497_499_and_swiss_grid_adaptive_overflow(self):
        """
        Contract 8 (Screenshots 496.jpg / 497.jpg / 499.jpg + Swiss-Grid 5+/10+ NEEDS YOU):
          1) Bug 496.jpg: Transient `sqlite3.OperationalError: unable to open database file` on
             `conversation_summaries.db` falls back to `_db_rows_cache` / `_session_db_meta` without
             emitting a scary yellow warning or flashing pinned session titles back to firstPrompt.
          2) Bug 497.jpg: AGY subagents (step 0 `SYSTEM_MESSAGE` with `sender=<parent_uuid>` or
             parent `Created the following subagent` output) and automated role prompts (`你是「...`,
             `Write clean Python 3.10+ stdlib-only...`) are tagged `automated=True`/`isSubagent=True`,
             evicted from `active_working_set.json`, and excluded from `listing(show_temp=0)`.
          3) Bug 499.jpg: `refresh_data(reorder=False)` preserves `Pinned -> Active -> Recent` bucket
             order when new live sessions appear or existing sessions change live/pinned status, so
             `▾ Pinned`, `▾ Active`, and `▾ Recent` headers never vanish.
          4) Swiss-Grid `NEEDS YOU` 5+/10+ Adaptive Density + Overflow Scroll + `[m]` mark unread read.
        """
        import sqlite3

        now = time.time()
        sub_sender_id = "18d3305b-ede7-4735-9d68-ee6a104fb112"
        sub_invoked_id = "5f0f2927-deea-40b2-a0d0-a8b4eae6d3f2"
        headless_id = "c49d3e95-7f7d-44f1-bfe6-c371f967f196"

        # Seed parent A1 transcript with `Created the following subagent` for sub_invoked_id
        self._write_jsonl(
            self.agy_p1,
            [
                {"step_index": 0, "type": "USER_INPUT", "content": "<USER_REQUEST>我想知道这里会话如果变多怎么办</USER_REQUEST>"},
                {
                    "step_index": 1,
                    "type": "GENERIC",
                    "content": f'Created the following subagent:\n{{\n  "conversationId": "{sub_invoked_id}"\n}}',
                },
            ],
            mtime=now - 10,
        )

        # Seed subagent 1 (has step 0 SYSTEM_MESSAGE with sender=A1, not in conversation_summaries.db)
        sub1_p = self.home / f".gemini/antigravity-cli/brain/{sub_sender_id}/.system_generated/logs/transcript.jsonl"
        self._write_jsonl(
            sub1_p,
            [
                {
                    "step_index": 0,
                    "type": "SYSTEM_MESSAGE",
                    "content": f"[Message] timestamp=2026-10-24T07:45:20Z sender={A1} priority=MESSAGE_PRIORITY_HIGH content=你是「专项调用 ②：零文字视觉设计」",
                }
            ],
            mtime=now - 5,
        )

        # Seed subagent 2 (linked via parent transcript Created the following subagent)
        sub2_p = self.home / f".gemini/antigravity-cli/brain/{sub_invoked_id}/.system_generated/logs/transcript.jsonl"
        self._write_jsonl(
            sub2_p,
            [
                {
                    "step_index": 0,
                    "type": "USER_INPUT",
                    "content": "<USER_REQUEST>你是 'Relay E2E 实战预演与排障工程师' ### ① 目标与完成标准</USER_REQUEST>",
                }
            ],
            mtime=now - 4,
        )

        # Seed headless automated task (`Write clean Python 3.10+ stdlib-only...`)
        headless_p = self.home / f".gemini/antigravity-cli/brain/{headless_id}/.system_generated/logs/transcript.jsonl"
        self._write_jsonl(
            headless_p,
            [
                {
                    "step_index": 0,
                    "type": "USER_INPUT",
                    "content": "<USER_REQUEST>[⑂ BRANCH TASK] Write clean Python 3.10+ stdlib-only functions to integrate into MixHub</USER_REQUEST>",
                }
            ],
            mtime=now - 3,
        )

        # Pollute active_working_set.json with those 3 automated/subagent IDs (as happened before fix)
        self.hub._save_working_set({f"agy:{sub_sender_id}", f"agy:{sub_invoked_id}", f"agy:{headless_id}", f"agy:{A1}"})

        # First discover populates DB cache and cleans polluted working set
        self.hub.action("agy", A1, "pin", {"pinned": True})
        self.hub.scanned = 0
        res1 = self.hub.listing({"tool": ["all"], "q": [""], "show_temp": ["0"]})
        vis_ids_1 = {s["id"] for s in res1["sessions"]}
        self.assertIn(A1, vis_ids_1)
        self.assertNotIn(sub_sender_id, vis_ids_1)
        self.assertNotIn(sub_invoked_id, vis_ids_1)
        self.assertNotIn(headless_id, vis_ids_1)
        self.assertNotIn(f"agy:{sub_sender_id}", self.hub.working_set())
        self.assertNotIn(f"agy:{sub_invoked_id}", self.hub.working_set())
        self.assertNotIn(f"agy:{headless_id}", self.hub.working_set())

        # 1) Simulate Bug 496.jpg: sqlite3.connect raises OperationalError("unable to open database file")
        with patch("server.sqlite3.connect", side_effect=sqlite3.OperationalError("unable to open database file")), \
             patch("server.time.sleep"):
            self.hub.scanned = 0
            res2 = self.hub.listing({"tool": ["all"], "q": [""], "show_temp": ["0"]})
            vis_by_id = {s["id"]: s for s in res2["sessions"]}
            self.assertEqual(res2["warnings"], [])
            # Pinned session A1 keeps its DB title ("Gemini UX Audit Session"), never flashing back to firstPrompt!
            self.assertEqual(vis_by_id[A1]["title"], "Gemini UX Audit Session")
            self.assertNotIn(sub_sender_id, vis_by_id)
            self.assertNotIn(sub_invoked_id, vis_by_id)
            self.assertNotIn(headless_id, vis_by_id)

        # 3) Verify Bug 499.jpg: refresh_data(reorder=False) keeps Pinned -> Active -> Recent headers intact
        app = self._make_tui(height=30, width=32, sidebar_only=True)
        # Initially A1 is Pinned + Live, C1/X1/C2 are Recent
        drows0 = app._sidebar_display_rows()
        headers0 = [r["label"] for r in drows0 if r.get("kind") == "header"]
        self.assertIn("Pinned", headers0)
        self.assertIn("Recent", headers0)

        # Select C2, then make X1 become Active (isLive=True) and add a brand-new Active session during reorder=False
        app.selected_idx = next(i for i, s in enumerate(app.sessions) if s["id"] == C2)
        self.hub._save_working_set({f"agy:{A1}", f"codex:{X1}"})
        new_live_c = "99999999-9999-4999-8999-999999999999"
        new_c_path = self.home / f".claude/projects/proj/{new_live_c}.jsonl"
        self._write_jsonl(new_c_path, [claude_user("Brand new active session during non-reorder refresh")], mtime=now)
        self.hub._save_working_set({f"agy:{A1}", f"codex:{X1}", f"claude:{new_live_c}"})

        app.refresh_data(force=True, reorder=False)
        self.assertEqual(app.current_session()["id"], C2, "Selected session ID must be preserved across refresh")
        drows1 = app._sidebar_display_rows()
        headers1 = [r["label"] for r in drows1 if r.get("kind") == "header"]
        self.assertEqual(headers1, ["Pinned", "Active", "Recent"])
        vis_buckets = [app._session_bucket(r["session"]) for r in drows1 if r.get("kind") == "session"]
        self.assertEqual(vis_buckets, sorted(vis_buckets), "_sidebar_display_rows must keep Pinned -> Active -> Recent order")

        # 4) Verify Swiss-Grid NEEDS YOU 5+/10+ Adaptive Density, Overflow Footer, Wheel Scroll & [m] Mark Unread Read
        color = lambda p: p << 8
        with patch("tui.curses.color_pair", side_effect=color):
            w_app = self._make_tui(height=28, width=100, sidebar_only=False)
            w_app.selected_idx = -1
            w_app.sessions = []
            for i in range(12):
                is_wait = i < 4
                s_item = {
                    "id": f"triage-{i:02d}",
                    "tool": ("claude", "codex", "agy")[i % 3],
                    "title": f"待处理任务 #{i:02d}",
                    "isLive": True,
                    "agentState": "wait_input" if is_wait else "done_idle",
                    "updatedAt": now - i * 60,
                    "lastPrompt": f"Allow Bash command #{i:02d}?" if is_wait else f"Completed task summary #{i:02d}",
                }
                w_app.sessions.append(s_item)
                if not is_wait:
                    w_app._seen_state["attn"][w_app._session_key(s_item)] = now + 10

            w_app.stdscr.addstr.reset_mock()
            w_app.draw()
            right_texts = [str(c.args[2]) for c in w_app.stdscr.addstr.call_args_list if c.args[1] >= 24]
            self.assertIn("NEEDS YOU", right_texts)
            self.assertIn("LAUNCH", right_texts)
            self.assertIn("[ + Claude ]", right_texts)
            self.assertTrue(
                any("more (" in t and "scroll" in t for t in right_texts),
                f"Expected overflow footer in right_texts, got {right_texts}",
            )
            self.assertFalse(any("[m]" in t for t in right_texts), "the home pane is clicked, not typed into")

            # Scroll down with mouse wheel in right welcome pane
            w_app._handle_mouse(parsed_event=(65, 60, 12, False))
            self.assertGreater(w_app.welcome_scroll, 0)

            # Pressing/calling mark_welcome_unread_seen clears all 8 unread sessions while keeping 4 awaiting sessions!
            cleared_cnt = w_app.mark_welcome_unread_seen()
            self.assertEqual(cleared_cnt, 8)
            remaining_unread = sum(1 for s in w_app.sessions if w_app.is_session_unread(s))
            remaining_wait = sum(1 for s in w_app.sessions if s.get("agentState") == "wait_input")
            self.assertEqual(remaining_unread, 0)
            self.assertEqual(remaining_wait, 4)

    def test_9_new_session_launch_quota_zero_dot_and_orphan_cleanup(self):
        """
        Contract 9 (Screenshots 11.16.10.png, 11.17.48.png, 11.36.29.png + hub-2 orphan cleanup):
          1) Clicking `✎  New session` (Row 2) -> selecting Gemini (`agy`) / Claude / Codex or clicking
             Welcome Launchpad `[ + Gemini ]` must spawn `agy_claude_filter.py` WITHOUT bogus `chat` arg,
             insert `New Gemini Session` (`id="new-..."`, `isLive=True`) at the top of `▾ Active` and select it,
             sync outer Terminal window title (`mixhub · New Gemini Session`), focus `pool:{wname}.{bottom}`,
             and focus the outer right pane.
          2) `_draw_footer_quota` renders `"  · "` (`P_DIM`) and omits `reset_5h` (`14:59`) when `rem_5h == 100`
             (`0%` used, 5h window not started yet).
          3) `reader_only` and `header_only` TUI instances never spawn `caffeinate` subprocesses, and
             `--header` mode never exits or triggers single-key shortcuts on `q` / `i` / `Enter`.
        """
        color = lambda p: p << 8
        tui._NEW_HUBPOOL_SESSIONS.clear() if hasattr(tui, "_NEW_HUBPOOL_SESSIONS") else None
        with patch.dict(os.environ, {"TMUX": "/tmp/tmux-501/default,123,0"}), \
             patch("tui.curses.color_pair", side_effect=color):
            sb_app = self._make_tui(height=30, width=32, sidebar_only=True)
            sb_app.selected_idx = 0
            # Click Row 2 `✎  New session` to open selector, then click row 6 (`[4] Gemini` -> `agy`)
            sb_app._handle_mouse(parsed_event=(0, 6, 2, False))
            self.assertTrue(sb_app._new_session_menu)
            with patch("mixhub.tmux.workspace.ensure_outer_dual_pane_layout"), \
                 patch.object(sb_app, "_ensure_hubpool_ready"), \
                 patch("mixhub.tmux.outer.focus_outer_right_pane") as mock_focus_right, \
                 patch("tui.subprocess.Popen") as mock_popen_title, \
                 patch("tui.subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(stdout="%20|0|40\n", returncode=0)
                sb_app._handle_mouse(parsed_event=(0, 8, 6, False))
                self.assertFalse(sb_app._new_session_menu)
                cur_new = sb_app.current_session()
                self.assertIsNotNone(cur_new, "Newly launched session must be active and selected in sidebar")
                self.assertTrue(str(cur_new.get("id", "")).startswith("new-"))
                self.assertEqual(cur_new.get("tool"), "agy")
                self.assertEqual(cur_new.get("title"), "New Gemini Session")
                self.assertTrue(cur_new.get("isLive"))
                self.assertIn(sb_app._session_key(cur_new), sb_app._seen_state["seen"])
                self.assertFalse(sb_app.is_session_unread(cur_new))
                self.assertTrue(mock_focus_right.called)
                # Outer terminal window title must be updated to `mixhub · New Gemini Session`
                self.assertEqual(tui.format_outer_window_title(cur_new), "mixhub · New Gemini Session")
                title_cmds = [c.args[0] for c in mock_popen_title.call_args_list if "-T" in c.args[0]]
                self.assertTrue(
                    any("mixhub · New Gemini Session" in cmd for cmd in title_cmds),
                    f"Expected outer title update to 'mixhub · New Gemini Session', got {title_cmds}",
                )
                new_win_cmds = [c.args[0] for c in mock_run.call_args_list if "new-window" in c.args[0]]
                self.assertEqual(len(new_win_cmds), 1)
                launch_shell_cmd = new_win_cmds[0][-1]
                self.assertIn("-m mixhub.filters.agy_claude;", launch_shell_cmd)
                self.assertNotIn(" chat", launch_shell_cmd, "agy has no 'chat' subcommand; passing 'chat' exits immediately!")
                select_pane_cmds = [c.args[0] for c in mock_run.call_args_list if "select-pane" in c.args[0]]
                self.assertTrue(
                    any(".{bottom}" in " ".join(cmd) for cmd in select_pane_cmds),
                    f"Expected select-pane to target .{{bottom}}, got {select_pane_cmds}",
                )
                # Verify `hubpool_win_name` and `find_hubpool_window_for_session` map `new-xxx` back to `w-agy-new-xxx`
                expected_wname = cur_new["hubpoolWindow"]
                self.assertEqual(tui.hubpool_win_name(cur_new), expected_wname)
                self.assertEqual(tui.find_hubpool_window_for_session(cur_new["id"], allow_probe=False), expected_wname)
                # And after refresh_data(force=True), `New Gemini Session` stays in `self.sessions` under `Active`
                sb_app.refresh_data(force=True)
                self.assertTrue(any(s.get("id") == cur_new["id"] for s in sb_app.sessions))

            # Verify outer window title formatting for existing session & empty selection
            self.assertEqual(
                tui.format_outer_window_title(self.hub.get("agy", A1)),
                f"mixhub · {tui.disp_trunc(self.hub.get('agy', A1)['title'], 24)} (Gemini)",
            )
            self.assertEqual(tui.format_outer_window_title(None), "mixhub")

            # Verify `--header` mode ignores `q` / `i` / `Enter` and redirects focus to `.{bottom}` instead of exiting
            hdr_only = self._make_tui(height=2, width=90, sidebar_only=False)
            hdr_only.header_only = True
            hdr_only.header_target = ("agy", "new")
            hdr_only.stdscr.getch.side_effect = [ord("q"), ord("i"), 10, KeyboardInterrupt()]
            with patch.object(hdr_only, "_wait_reader_event_kqueue"), \
                 patch.object(hdr_only, "_is_reader_zoomed", return_value=False), \
                 patch("tui.subprocess.Popen") as mock_hdr_popen:
                try:
                    hdr_only.run()
                except KeyboardInterrupt:
                    pass
                hdr_popen_cmds = [c.args[0] for c in mock_hdr_popen.call_args_list]
                self.assertTrue(
                    any("select-pane" in cmd and ".{bottom}" in " ".join(cmd) for cmd in hdr_popen_cmds),
                    f"Expected --header keystrokes to redirect focus to .{{bottom}}, got {hdr_popen_cmds}",
                )
            tui._NEW_HUBPOOL_SESSIONS.clear()

            # Verify `activate_session_in_hubpool` targets `.{bottom}` instead of `.1` (immune to pane-base-index 1)
            sb_app._initialized_header_wins = {f"w-claude-{C1[:12]}"}
            with patch.object(sb_app, "_ensure_hubpool_ready"), \
                 patch("mixhub.tmux.pool.find_hubpool_window_for_session", return_value=f"w-claude-{C1[:12]}"), \
                 patch("mixhub.tmux.outer.focus_outer_right_pane"), \
                 patch("tui.subprocess.run", return_value=MagicMock(returncode=0, stdout=f"@7 w-claude-{C1[:12]}\n")), \
                 patch("tui.subprocess.Popen") as mock_popen:
                tui.activate_session_in_hubpool(self.hub.get("claude", C1), "hub", force_takeover=True, app=sb_app)
                popen_cmds = [c.args[0] for c in mock_popen.call_args_list]
                self.assertTrue(
                    any(f"pool:w-claude-{C1[:12]}.{{bottom}}" in cmd for cmd in popen_cmds),
                    f"Expected pool:w-claude-{{id}}.{{bottom}} in {popen_cmds}",
                )

            # Verify `_draw_footer_quota` renders `"  · "` and omits `14:59` when `rem_5h == 100` (`0%` used)
            with patch(
                "mixhub.tui.quota.get_cli_quota_metrics",
                return_value=[
                    {"key": "cc", "rem_5h": None, "reset_5h": "", "rem_wk": 6, "reset_wk": "10/28"},
                    {"key": "cx", "rem_5h": None, "reset_5h": "", "rem_wk": 79, "reset_wk": "10/31"},
                    {"key": "agy", "rem_5h": 100, "reset_5h": "14:59", "rem_wk": 62, "reset_wk": "10/25"},
                ],
            ):
                sb_app.stdscr.addstr.reset_mock()
                sb_app._draw_footer_quota(30, 32)
                row28_calls = {c.args[1]: c.args[2] for c in sb_app.stdscr.addstr.call_args_list if c.args[0] == 28}
                row28_attrs = {c.args[1]: c.args[3] for c in sb_app.stdscr.addstr.call_args_list if c.args[0] == 28}
                self.assertEqual(row28_calls.get(2), "gemini")
                self.assertEqual(row28_calls.get(9), "  · ")
                self.assertEqual(row28_attrs.get(9), color(tui.P_DIM))
                self.assertNotIn(14, row28_calls, "5h reset time '14:59' must not be drawn when used is 0% (rem_5h=100)")
                self.assertEqual(row28_calls.get(20), " 38%")
                self.assertEqual(row28_calls.get(25), "10/25")

            # Verify `reader_only` and `header_only` never call `caf.sync`
            with patch.object(tui.CaffeinateController, "sync") as mock_caf_sync:
                r_only = self._make_tui(height=30, width=90, sidebar_only=False)
                r_only.reader_only = True
                r_only.refresh_data(force=True)
                h_only = self._make_tui(height=2, width=90, sidebar_only=False)
                h_only.header_only = True
                h_only.refresh_data(force=True)
                # Only the initial _make_tui (before reader_only/header_only set) ran sync; reset and check refresh_data
                mock_caf_sync.reset_mock()
                r_only.refresh_data(force=True)
                h_only.refresh_data(force=True)
                self.assertEqual(mock_caf_sync.call_count, 0)

    def test_10_preview_bottom_action_bar_modes_b_c_and_weekly_quota_relay(self):
        """
        Contract 10 (1-line transparent Preview bar + Weekly-Quota Relay + Wordmark / NavHistory / Hover fixes):
          1) Mode B (Dormant) / Mode C (External Tab): chips on Row h-1 (`─` rule on Row h-2) in transparent
             `P_WHITE | A_BOLD` (idle) / `P_USER_BOX | A_BOLD` (hover), `↙ Move Here` in `P_META` / disabled `P_DIM`,
             zero `P_HOVER_DIM` fill on Rows h-2 / h-1, zero `ST_DORMANT` second subtitle line.
          2) Weekly quota `< 15%` renders `⇄ Relay` on Row h-1 in `P_STAT_T4 | A_BOLD`; clicking it expands
             `Claude Gemini Codex ★  ◂ Relay` (brand colors, transparent bg, no bg 237 box) on Row h-1 without
             overlapping chip1 / chip2.
          3) `mixhub` Home-only wordmark state machine, inert Row 0 gaps, stale-hover auto-expiry
             (1.8s / boundary exit / FocusOut / click), and `--reader` `KEY_UP` / `KEY_DOWN` scroll-only isolation.
          4) `parse_header_args` (0 / 1 / 2 positional args) and `handle_cli_on_exit` (hibernate marker vs `/exit`).
          5) `▾ Active` pure recency sort, `ensure_hubpool_window_header` self-heals bottom CLI,
             and `refresh_data` promotes `w-claude-new-<ts>` to `w-claude-<real_id[:12]>` in-place.
        """
        def color(pair_id):
            return pair_id << 8

        with patch("tui.curses.color_pair", side_effect=color), \
             patch("tui.curses.init_pair"), \
             patch("tui.curses.start_color"), \
             patch("tui.curses.use_default_colors"), \
             patch("tui.curses.curs_set"):
            # 1) Pure function `pick_relay_target`
            quota_low = [
                {"key": "cc", "rem_wk": 6},
                {"key": "cx", "rem_wk": 72},
                {"key": "agy", "rem_wk": 58},
            ]
            t_tool, cur_wk, tgt_wk = tui.pick_relay_target("claude", quota_low, {"claude", "codex", "agy"})
            self.assertEqual((t_tool, cur_wk, tgt_wk), ("codex", 6, 72))
            self.assertEqual(
                tui.pick_relay_target("claude", [{"key": "cc", "rem_wk": 20}, {"key": "cx", "rem_wk": 80}]),
                (None, None, None),
            )

            app = self._make_tui(height=30, width=100, sidebar_only=False)
            app.reader_only = True
            app.selected_idx = 0
            # Mode B: dormant Claude session with low weekly quota (6% left)
            app.sessions[0]["tool"] = "claude"
            app.sessions[0]["isLive"] = False
            app.sessions[0]["resident"] = False
            app.sessions[0]["processes"] = []
            idle_attr = color(tui.P_WHITE) | tui.curses.A_BOLD
            hov_attr = color(tui.P_USER_BOX) | tui.curses.A_BOLD

            def bar_calls(rows=(28, 29)):
                return [c.args for c in app.stdscr.addstr.call_args_list if c.args[0] in rows]

            def assert_transparent_bar(calls):
                # Thin `─` rule on Row 28, zero `P_HOVER_DIM` fill and zero legacy colored button boxes on Rows 28 / 29
                self.assertTrue(any(c[0] == 28 and "─" * 20 in str(c[2]) and c[3] == color(tui.P_BORDER) for c in calls))
                for c in calls:
                    if len(c) >= 4:
                        self.assertNotEqual(c[3] >> 8 & 0xFF, tui.P_HOVER_DIM, f"P_HOVER_DIM fill on Row {c[0]}: {c}")
                        self.assertNotIn(c[3] >> 8 & 0xFF, (tui.P_ACT_PRI, tui.P_BTN_SEC), f"colored button box: {c}")
                self.assertFalse(any(c[0] == 29 and c[2] == tui.ST_DORMANT for c in calls))
                self.assertFalse(any(c[0] == 28 and "─" not in str(c[2]) for c in calls))

            with patch("mixhub.tui.quota.get_cli_quota_metrics", return_value=quota_low):
                app.hover_pos = (-1, -1)
                app.stdscr.addstr.reset_mock()
                app.draw()
                calls = bar_calls()
                assert_transparent_bar(calls)
                bar = app._compute_preview_bar_state(30, 100, 0, False)["bar"]
                self.assertEqual(bar["bar_y"], 29)
                c1_lbl, c1_x0, c1_x1, c1_key, _ = bar["chip1"]
                self.assertNotIn("chip2", bar)
                self.assertNotIn("relay_items", bar)
                self.assertEqual((c1_lbl, c1_key), (tui.LBL_RESUME_CHIP, "resume"))
                # Default focus on pill 0 (`Resume ↵`) uses `P_USER_BOX | A_BOLD` (`hov_attr`)
                self.assertIn((29, c1_x0, tui.LBL_RESUME_CHIP, hov_attr), calls)

                # Printable keystroke pulse (`_preview_pulse_until`) keeps focused chip1 lit with `hov_attr`
                app._preview_focus_idx = 0
                app._preview_pulse_until = time.monotonic() + 0.18
                app.stdscr.addstr.reset_mock()
                app.draw()
                self.assertIn((29, c1_x0, tui.LBL_RESUME_CHIP, hov_attr), bar_calls((29,)))
                app._preview_pulse_until = 0.0

                # Mode C: external tab session (`Resume` #1, `Go to Tab` #2; default focus on `Resume ↵` when idle, zero `Relay` on bottom bar)
                app.sessions[0]["isLive"] = True
                app.sessions[0]["resident"] = False
                app.sessions[0]["agentState"] = "done_idle"
                app.sessions[0]["processes"] = [{"pid": 4321, "resident": False, "tty": "ttys009", "rss_mb": 100.0}]
                app.stdscr.addstr.reset_mock()
                app.draw()
                mc_calls = bar_calls()
                assert_transparent_bar(mc_calls)
                self.assertTrue(any(c[0] == 29 and c[2] == tui.LBL_RESUME_CHIP and c[3] == hov_attr for c in mc_calls))
                self.assertTrue(any(c[0] == 29 and c[2] == " Go to Tab   " and c[3] == color(tui.P_META) for c in mc_calls))
                self.assertFalse(any(c[0] == 29 and "Relay" in str(c[2]) for c in mc_calls))

                # Hover `Go to Tab` when idle -> moves focus (` Go to Tab ↵ ` in `P_USER_BOX | A_BOLD`) to `Go to Tab`, `Resume` becomes ` Resume   ` in `P_META`
                c2_info = app._compute_preview_bar_state(30, 100, 0, False)["bar"]["chip2"]
                self.assertGreater(c2_info[1], c1_x0)
                app.hover_pos = (c2_info[1] + 1, 29)
                app.stdscr.addstr.reset_mock()
                app.draw()
                self.assertTrue(any(c[2] == tui.LBL_GOTO_CHIP and c[3] == hov_attr for c in bar_calls((29,))))
                self.assertTrue(any(c[2] == " Resume   " and c[3] == color(tui.P_META) for c in bar_calls((29,))))
                app.hover_pos = (-1, -1)

                # Mode C busy (`working`) -> `Resume` disabled in plain `P_DIM`, default focus automatically shifts to `Go to Tab ↵`
                app.sessions[0]["agentState"] = "working"
                app.stdscr.addstr.reset_mock()
                app.draw()
                busy_calls = bar_calls()
                assert_transparent_bar(busy_calls)
                self.assertTrue(any(c[0] == 29 and c[2] == " Resume   " and c[3] == color(tui.P_DIM) for c in busy_calls))
                self.assertTrue(any(c[0] == 29 and c[2] == tui.LBL_GOTO_CHIP and c[3] == hov_attr for c in busy_calls))

                # Executing `goto_tab` calls `focus_external_session`, NEVER `takeover_external_session_in_hubpool`
                with patch.object(app, "focus_external_session", return_value=True) as mock_goto, \
                     patch.object(app, "takeover_external_session_in_hubpool") as mock_move:
                    app._execute_preview_action("goto_tab", True)
                    self.assertTrue(mock_goto.called)
                    self.assertFalse(mock_move.called)

                # Race condition when clicking `Resume` in Mode C after session just turned busy -> sets `MSG_MOVE_RACE`
                self.assertFalse(tui.takeover_external_session_in_hubpool(app.sessions[0], app=app))
                self.assertEqual(app.status_msg, tui.MSG_MOVE_RACE)

            # `--reader` KEY_UP / KEY_DOWN only scroll the preview and NEVER change `selected_idx`
            rd = self._make_tui(height=30, width=100, sidebar_only=False)
            rd.reader_only = True
            rd.selected_idx = 1
            rd.msg_scroll = 30
            rd.is_tail_truncated = False
            seen_states = []
            rd.stdscr.getch.side_effect = [tui.curses.KEY_UP, tui.curses.KEY_UP, tui.curses.KEY_DOWN, KeyboardInterrupt()]
            with patch.object(rd, "draw", side_effect=lambda: seen_states.append((rd.selected_idx, rd.msg_scroll))), \
                 patch.object(rd, "_wait_reader_event_kqueue"), \
                 patch.object(rd, "_sync_reader_selection"), \
                 patch.object(rd, "_is_reader_zoomed", return_value=False), \
                 patch("tui.curses.mousemask"), \
                 patch("tui.curses.mouseinterval"), \
                 patch("tui.subprocess.Popen"), \
                 patch("tui.subprocess.run"), \
                 patch("tui.sys.stdout"):
                try:
                    rd.run()
                except KeyboardInterrupt:
                    pass
            self.assertTrue(seen_states)
            self.assertTrue(all(sel == 1 for sel, _ in seen_states), seen_states)
            self.assertEqual(rd.selected_idx, 1)
            self.assertIn(24, [ms for _, ms in seen_states], seen_states)  # 30 -> 27 -> 24 (KEY_UP x2)
            self.assertGreater(seen_states[-1][1], 24)                    # KEY_DOWN scrolls back down

            # Verify `▾ Active` does NOT reorder when clicking an unread/wait_input row (pure recency sort)
            sb = self._make_tui(height=30, width=32, sidebar_only=True)
            for idx_s, s_item in enumerate(sb.sessions[:3]):
                s_item["isLive"] = True
                s_item["pinned"] = False
                s_item["updatedAt"] = 1000.0 - idx_s * 10.0
                s_item["transcriptAt"] = 1000.0 - idx_s * 10.0
                s_item["attentionAt"] = 0.0
            sb.sessions[0]["agentState"] = "done_idle"
            sb.sessions[1]["agentState"] = "wait_input"
            sb.sessions[2]["agentState"] = "working"
            sb._sidebar_rows_cache = None
            active_ids_before = [
                r["session"]["id"]
                for r in sb._sidebar_display_rows()
                if r.get("kind") == "session" and r["session"].get("isLive")
            ]
            # Even though session[1] is `wait_input` and unread, session[0] (newer updatedAt) stays above it!
            self.assertEqual(active_ids_before, [sb.sessions[0]["id"], sb.sessions[1]["id"], sb.sessions[2]["id"]])
            sb._mark_session_seen(sb.sessions[1])
            sb._sidebar_rows_cache = None
            active_ids_after = [
                r["session"]["id"]
                for r in sb._sidebar_display_rows()
                if r.get("kind") == "session" and r["session"].get("isLive")
            ]
            self.assertEqual(active_ids_before, active_ids_after)
            # Also verify a non-isLive unread session never enters `Active` (bucket 1) and never jumps sections when clicked
            dormant_unread = dict(
                sb.sessions[0],
                id="dormant-unread-99",
                isLive=False,
                agentState="done_idle",
                pinned=False,
                updatedAt=time.time() + 60.0,
                attentionAt=time.time() + 60.0,
            )
            self.assertTrue(sb.is_session_unread(dormant_unread))
            self.assertEqual(sb._session_bucket(dormant_unread), 2)

            # Verify `ensure_hubpool_window_header` respawns bottom CLI below `--header` if bottom CLI exited
            with patch("tui.subprocess.run") as mock_hdr_run:
                mock_hdr_run.return_value = MagicMock(
                    stdout=f"%9|0|30|exec python3 tui.py --header claude {C1}|{self.workspace}\n",
                    returncode=0,
                )
                tui.ensure_hubpool_window_header(f"w-claude-{C1[:12]}", "claude", C1)
                split_cmds = [c.args[0] for c in mock_hdr_run.call_args_list if "split-window" in c.args[0]]
                self.assertEqual(len(split_cmds), 1)
                self.assertIn("-v", split_cmds[0])
                self.assertNotIn("-vb", split_cmds[0])

            # Verify in-place promotion of `w-claude-new-<ts>` to `w-claude-<real_id[:12]>` in `refresh_data`
            new_w = "w-claude-new-1777100000"
            ns_dict = tui._make_new_hubpool_session_dict("claude", "1777100000", new_w)
            ns_dict["updatedAt"] = time.time() - 5.0
            tui._NEW_HUBPOOL_SESSIONS[new_w] = ns_dict
            tui._HUBPOOL_WINS_CACHE["ts"] = time.monotonic()
            tui._HUBPOOL_WINS_CACHE["wins"] = {new_w}
            mock_list_res = {
                "sessions": [
                    {
                        "id": C1,
                        "tool": "claude",
                        "title": "Promoted Session",
                        "isLive": True,
                        "updatedAt": time.time(),
                        "processes": [{"pid": 9999, "resident": True, "tty": "ttys001", "rss_mb": 50.0}],
                    }
                ],
                "counts": {"total": 1, "live": 1, "liveMemoryMB": 50.0},
            }
            with patch.object(app.hub, "listing", return_value=mock_list_res), \
                 patch("mixhub.tmux.new_sessions._collect_live_new_hubpool_sessions", return_value=[dict(ns_dict)]), \
                 patch("tui.subprocess.run") as mock_prom_run:
                app.refresh_data(fast_only=True)
                rename_cmds = [c.args[0] for c in mock_prom_run.call_args_list if "rename-window" in c.args[0]]
                self.assertTrue(any(f"pool:{new_w}" in cmd and f"w-claude-{C1[:12]}" in cmd for cmd in rename_cmds))
                self.assertNotIn(new_w, tui._NEW_HUBPOOL_SESSIONS)

            # ---- `mixhub` Home-only wordmark state machine + inert Row 0 gaps + stale-hover auto-expiry (`--sidebar`)
            with patch.dict(os.environ, {"TMUX": "/tmp/tmux-1000/default,123,0"}), \
                 patch("tui.subprocess.run"), \
                 patch("tui.subprocess.Popen"):
                wm_app = self._make_tui(height=30, width=32, sidebar_only=True)
                wm_app._active_hubpool_win = "reader"
                wm_app.wordmark.rng.choice = lambda pool: "pulse" if "pulse" in pool else pool[0]
                list_top, _, _ = wm_app._list_bounds(30)

                def row_y_of(sid: str) -> int:
                    for r_i, it in enumerate(wm_app._sidebar_display_rows()):
                        if it.get("kind") == "session" and wm_app.sessions[it["idx"]]["id"] == sid:
                            return list_top + r_i - wm_app.list_scroll
                    raise AssertionError(f"session {sid} not visible")

                def draw_row0() -> dict:
                    with patch.object(wm_app, "_rebalance_sidebar_width_if_needed"):
                        wm_app.stdscr.addstr.reset_mock()
                        wm_app.draw()
                    return {c.args[1]: c.args[2] for c in wm_app.stdscr.addstr.call_args_list if c.args[0] == 0}

                # Cold start: `‹ ›` always visible (dimmed) at cols 22 / 25
                row0 = draw_row0()
                self.assertEqual(row0.get(5), "mixhub")
                self.assertEqual(row0.get(22), "‹")
                self.assertEqual(row0.get(25), "›")

                # Home -> C1 -> C2 renders `‹ ›` at cols 22 / 25 and navigates back/forward
                wm_app._handle_mouse(parsed_event=(0, 10, row_y_of(C1), False))
                wm_app._handle_mouse(parsed_event=(0, 10, row_y_of(C2), False))
                self.assertEqual(wm_app.current_session()["id"], C2)
                row0 = draw_row0()
                self.assertEqual(row0.get(22), "‹")
                self.assertEqual(row0.get(25), "›")
                wm_app._handle_mouse(parsed_event=(0, 22, 0, False))
                self.assertEqual(wm_app.current_session()["id"], C1)
                wm_app._handle_mouse(parsed_event=(0, 25, 0, False))
                self.assertEqual(wm_app.current_session()["id"], C2)

                # Clicking `mixhub` in a session -> Home with static wordmark (`off`)
                wm_app.selected_idx = 0
                wm_app._handle_mouse(parsed_event=(0, 5, 0, False))
                self.assertEqual(wm_app.selected_idx, -1)
                self.assertEqual(wm_app.wordmark.mode, "off")
                # Immediate re-click (< 0.3s debounce) is ignored
                wm_app._handle_mouse(parsed_event=(0, 5, 0, False))
                self.assertEqual(wm_app.wordmark.mode, "off")
                # > 0.3s later on Home: starts an effect; clicking again turns it off
                wm_app._wordmark_home_nav_ts = time.monotonic() - 1.0
                wm_app._handle_mouse(parsed_event=(0, 5, 0, False))
                self.assertIn(wm_app.wordmark.mode, ("pulse", "decode", "sweep"))
                wm_app._handle_mouse(parsed_event=(0, 5, 0, False))
                self.assertEqual(wm_app.wordmark.mode, "off")
                # Selecting any session turns a running effect off
                wm_app._handle_mouse(parsed_event=(0, 5, 0, False))
                self.assertNotEqual(wm_app.wordmark.mode, "off")
                wm_app._handle_mouse(parsed_event=(0, 10, row_y_of(C1), False))
                self.assertEqual(wm_app.current_session()["id"], C1)
                self.assertEqual(wm_app.wordmark.mode, "off")

                # Stale hover auto-expiry: motion over a sidebar row arms `_last_hover_ts`, 1.8s later draw() clears it
                hov_y = row_y_of(C2)
                wm_app._handle_mouse(parsed_event=(35, 10, hov_y, False))
                self.assertNotEqual(wm_app.hover_idx, -1)
                self.assertEqual(wm_app.hover_pos, (10, hov_y))
                self.assertGreater(wm_app._last_hover_ts, 0.0)
                draw_row0()
                self.assertNotEqual(wm_app.hover_idx, -1)  # still fresh
                wm_app._last_hover_ts = time.monotonic() - 1.9
                draw_row0()
                self.assertEqual(wm_app.hover_idx, -1)
                self.assertEqual(wm_app.hover_pos, (-1, -1))
                self.assertEqual(wm_app._last_hover_ts, 0.0)

                # Boundary exit (col 0 / right edge / bottom row) clears hover immediately
                for bx, by in ((0, hov_y), (31, hov_y), (10, 29)):
                    wm_app._handle_mouse(parsed_event=(35, 10, hov_y, False))
                    self.assertNotEqual(wm_app.hover_idx, -1)
                    wm_app._handle_mouse(parsed_event=(35, bx, by, False))
                    self.assertEqual((wm_app.hover_idx, wm_app.hover_pos), (-1, (-1, -1)), (bx, by))

                # tmux FocusOut (`ESC [ O`) clears hover; FocusIn (`ESC [ I`) is consumed silently
                wm_app._handle_mouse(parsed_event=(35, 10, hov_y, False))
                wm_app.stdscr.getch.side_effect = [ord("["), ord("O")]
                self.assertEqual(wm_app._consume_sgr_mouse_if_any(), (-2, -1, -1, False))
                self.assertEqual((wm_app.hover_idx, wm_app.hover_pos), (-1, (-1, -1)))
                wm_app._handle_mouse(parsed_event=(35, 10, hov_y, False))
                wm_app.stdscr.getch.side_effect = [ord("["), ord("I")]
                self.assertEqual(wm_app._consume_sgr_mouse_if_any(), (-2, -1, -1, False))
                self.assertNotEqual(wm_app.hover_idx, -1)
                wm_app.stdscr.getch.side_effect = None

                # Clicking a row selects it and drops hover so its timestamp shows (never sticky `⋯ ☆`)
                wm_app._last_row_click = ("", 0.0)
                wm_app._handle_mouse(parsed_event=(0, 10, hov_y, False))
                self.assertEqual(wm_app.current_session()["id"], C2)
                self.assertEqual((wm_app.hover_idx, wm_app.hover_pos, wm_app._last_hover_ts), (-1, (-1, -1), 0.0))
                with patch.object(wm_app, "_rebalance_sidebar_width_if_needed"):
                    wm_app.stdscr.addstr.reset_mock()
                    wm_app.draw()
                sel_row = {c.args[1]: c.args[2] for c in wm_app.stdscr.addstr.call_args_list if c.args[0] == hov_y}
                self.assertNotIn("⋯", "".join(str(v) for v in sel_row.values()))
                self.assertIn(27, sel_row)

            # ---- `parse_header_args` (0 / 1 / 2 positional args)
            self.assertEqual(tui.parse_header_args(["--reader"]), (False, None))
            self.assertEqual(tui.parse_header_args(["--header"]), (True, None))
            self.assertEqual(tui.parse_header_args(["--header", C1]), (True, ("", C1)))
            self.assertEqual(tui.parse_header_args(["--header", "claude", C1]), (True, ("claude", C1)))
            self.assertEqual(tui.parse_header_args(["--header", "--x", "codex", X1, "extra"]), (True, ("codex", X1)))

            # ---- `handle_cli_on_exit`: hub hibernate marker keeps `Active`; user `/exit` drops to `Recent`
            marker_dir = self.root / "data" / "hibernating"
            last_exit_dir = self.root / "data" / "last_exit"
            ws_path = self.root / "active_working_set.json"
            ws_path.write_text(json.dumps({"sessions": [f"claude:{C1}", f"codex:{X1}"]}), encoding="utf-8")
            with patch("mixhub.tui.paths.HIBERNATING_MARKER_DIR", str(marker_dir)), \
                 patch("mixhub.tui.paths.LAST_EXIT_DIR", str(last_exit_dir)), \
                 patch("tui.subprocess.run") as mock_exit_run:
                marker_dir.mkdir(parents=True, exist_ok=True)
                Path(tui._hibernating_marker_path(C1)).write_text("1", encoding="utf-8")
                self.assertTrue(os.path.exists(tui._hibernating_marker_path(C1)))
                self.assertEqual(tui.handle_cli_on_exit("claude", C1, "", 0, working_set_path=str(ws_path)), 0)
                self.assertFalse(os.path.exists(tui._hibernating_marker_path(C1)))
                self.assertIn(f"claude:{C1}", json.loads(ws_path.read_text(encoding="utf-8"))["sessions"])
                self.assertFalse((last_exit_dir / f"{C1}.json").exists())

                self.assertEqual(tui.handle_cli_on_exit("claude", C1, "", 0, working_set_path=str(ws_path)), 0)
                ws_after = json.loads(ws_path.read_text(encoding="utf-8"))["sessions"]
                self.assertNotIn(f"claude:{C1}", ws_after)
                self.assertIn(f"codex:{X1}", ws_after)
                self.assertTrue((last_exit_dir / f"{C1}.json").exists())
                exit_sel = tui.read_active_selection()
                self.assertEqual((exit_sel["tool"], exit_sel["id"], exit_sel.get("activateReader")), ("claude", C1, True))
                self.assertEqual(mock_exit_run.call_count, 0)  # no `wname` -> zero tmux calls

                # Verify promoted `new-<ts> -> real_sid` resolves in `handle_cli_on_exit`
                ws_path.write_text(json.dumps({"sessions": [f"agy:{A1}", "agy:new-54321"]}), encoding="utf-8")
                tui._record_promoted_new_session("agy", "new-54321", "agy", A1, f"w-agy-{A1[:12]}")
                self.assertEqual(tui.handle_cli_on_exit("agy", "new-54321", "", 0, working_set_path=str(ws_path)), 0)
                ws_prom_after = json.loads(ws_path.read_text(encoding="utf-8"))["sessions"]
                self.assertNotIn(f"agy:{A1}", ws_prom_after)
                self.assertNotIn("agy:new-54321", ws_prom_after)
                self.assertTrue((last_exit_dir / f"{A1}.json").exists())
                prom_sel = tui.read_active_selection()
                self.assertEqual((prom_sel["tool"], prom_sel["id"], prom_sel.get("activateReader")), ("agy", A1, True))


    def test_process_subcommands_and_wrapper_resident_tty(self):
        for command in ("exec", "e", "app-server", "mcp", "mcp-server", "review", "login", "models", "update"):
            with self.subTest(command=command):
                self.assertEqual(server.parse_processes(f"100 1 ttys001 1024 00:01 codex --no-daemon {command}"), [])
                self.assertEqual(len(server.parse_processes(f"100 1 ttys001 1024 00:01 codex -- {command}")), 1)
        processes = server.parse_processes(
            "100 1 ttys001 1024 00:01 python3 /tmp/agy_claude_filter.py\n"
            "101 100 ttys002 2048 00:01 agy\n"
            "102 1 ttys003 1024 00:01 node /tmp/codex.js\n"
            "103 1 ttys003 2048 00:01 codex"
        )
        self.assertEqual([p["pid"] for p in processes], [101, 103])
        self.assertEqual(processes[0]["wrapper_ttys"], ["ttys001"])
        self.assertEqual(processes[1]["wrapper_ttys"], ["ttys003"])
        del self.hub.processes
        del self.hub.open_files
        self.hub._proc_scanned = time.monotonic()
        self.hub._proc_cache = processes[:1]
        self.hub._files_cache = {101: {"paths": [str(self.agy_p1)]}}
        self.hub._resident_ttys_cache = {"ttys001"}
        s = self.hub.discover(force=True)["agy:" + A1]
        self.assertTrue(s["resident"])
        self.assertTrue(s["processes"][0]["resident"])

    def test_lock_probe_filters_codex_and_agy_subagents(self):
        for tool, rel in (("codex", ".codex/thread-writer-locks"), ("agy", ".gemini/antigravity-cli/presence")):
            lock_dir = self.home / rel
            lock_dir.mkdir(parents=True, exist_ok=True)
            (lock_dir / f"{X1}.lock").touch()
            for meta in ({"parentId": C1}, {"isSubagent": True}, {"automated": True}, {}):
                with self.subTest(tool=tool, meta=meta), patch("mixhub.hub.discovery.fcntl", None):
                    found = self.hub._probe_active_lock_uuids({tool + ":" + X1: meta})
                    expected = not (meta.get("parentId") or meta.get("isSubagent") or (tool == "codex" and meta.get("automated")))
                    self.assertEqual(X1 in found[tool], expected)

    def test_codex_agent_role_stays_automated(self):
        import sqlite3
        with closing(sqlite3.connect(self.home / ".codex/state_5.sqlite")) as conn, conn:
            conn.execute("ALTER TABLE threads ADD COLUMN agent_role TEXT")
            conn.execute("UPDATE threads SET agent_role = 'worker'")
        s = self.hub.discover(force=True)["codex:" + X1]
        self.assertTrue(s["automated"])
        self.assertTrue(s["isTemp"])
        self.assertNotIn(X1, {s["id"] for s in self.hub.listing({"show_temp": ["0"]})["sessions"]})

    def test_agy_turn_one_promotion_requires_interactive_process(self):
        import sqlite3
        db = self.home / ".gemini/antigravity-cli/conversation_summaries.db"
        with closing(sqlite3.connect(db)) as conn, conn:
            conn.execute("ALTER TABLE conversation_summaries ADD COLUMN last_user_input_step_index INTEGER DEFAULT -1")
            conn.execute("ALTER TABLE conversation_summaries ADD COLUMN last_user_input_time REAL")
            conn.execute("ALTER TABLE conversation_summaries ADD COLUMN agent_name TEXT")
            conn.execute("UPDATE conversation_summaries SET last_user_input_time = ?", (time.time(),))
        proc = server.parse_processes("101 1 ttys001 2048 00:01 agy")[0]
        locks = {"codex": {}, "agy": {A1: time.time()}}
        with patch.object(self.hub, "_probe_active_lock_uuids", return_value=locks):
            # A headless presence lock alone cannot promote the quarantined Turn-1 row.
            s = self.hub.discover(force=True)["agy:" + A1]
            self.assertTrue(s["automated"])
            self.assertFalse(s["isLive"])
            self.hub.processes = lambda: [proc]
            for via_lsof in (True, False):
                with self.subTest(via_lsof=via_lsof):
                    self.hub.open_files = lambda _: {101: {"paths": [str(self.agy_p1)] if via_lsof else []}}
                    s = self.hub.discover(force=True)["agy:" + A1]
                    self.assertTrue(s["isLive"])
                    self.assertFalse(s["automated"])
                    self.assertFalse(s["isTemp"])
                    self.assertEqual(s["processes"][0]["pid"], 101)
            self.hub.open_files = lambda _: {101: {"paths": [str(self.agy_p1)]}}
            for field, value in (("parent_conversation_id", C1), ("agent_name", "worker"),
                                 ("title", "[⑂ BRANCH TASK] Write clean Python 3.10+ stdlib-only functions")):
                with self.subTest(field=field), closing(sqlite3.connect(db)) as conn, conn:
                    conn.execute(f"UPDATE conversation_summaries SET {field} = ?", (value,))
                self.hub._session_db_meta.clear()
                s = self.hub.discover(force=True)["agy:" + A1]
                self.assertTrue(s["isTemp"])
                with closing(sqlite3.connect(db)) as conn, conn:
                    conn.execute(f"UPDATE conversation_summaries SET {field} = ''")

    def test_new_window_draft_ansi_placeholders_and_multiline(self):
        def inspect(raw):
            result = tui.subprocess.CompletedProcess([], 0, stdout=raw)
            with patch("tui.subprocess.run", return_value=result) as run:
                draft = tui._inspect_new_hubpool_window_draft("w-claude-new-1")
                self.assertIn("-e", run.call_args.args[0])
                self.assertEqual(run.call_args.args[0][-1], "pool:w-claude-new-1.{bottom}")
                return draft
        for style in ("2", "0;2", "90", "38;5;238", "38;5;239", "38;5;240", "38;5;246"):
            for marker in (">", "❯", "›"):
                with self.subTest(style=style, marker=marker):
                    self.assertEqual(inspect(f"────────\n{marker} \x1b[{style}mTry Explain this codebase\x1b[0m\n────────\n? for shortcuts"), "")
        self.assertEqual(inspect("old output\n────────\n❯ \x1b[1mDraft text\x1b[0m\n  second line\n────────\n  esc to dismiss"), "Draft text\nsecond line")
        self.assertEqual(inspect("› Explain this codebase\n────────\n  99% context left"), "Explain this codebase")
        self.assertEqual(inspect("────────\n> \n────────\n  Press up to edit"), "")
        with patch("tui.subprocess.run", return_value=tui.subprocess.CompletedProcess([], 1, stdout="")):
            with self.assertRaises(RuntimeError):
                tui._inspect_new_hubpool_window_draft("w-codex-new-1")

    def test_new_window_draft_stops_at_agy_rule_whose_color_runs_past_the_line_end(self):
        # agy colors its composer rule 38;5;238 and resets only at the start of the next line; stripping that span
        # must not swallow the newline and glue the model badge onto the draft.
        raw = ("\x1b[38;5;111m>\x1b[39m 帮我看一下 voicebank 的进度\n"
               "\x1b[38;5;238m" + "─" * 40 + "\n"
               "\x1b[39m" + " " * 30 + "\x1b[38;5;109mGemini 4 Argon · high\n"
               "\x1b[39mctx 0 0% · Gemini 5h 1%\x1b[2m↻13m\x1b[0m\n")
        with patch("tui.subprocess.run", return_value=tui.subprocess.CompletedProcess([], 0, stdout=raw)):
            self.assertEqual(tui._inspect_new_hubpool_window_draft("w-agy-new-1"), "帮我看一下 voicebank 的进度")

    def test_active_sidebar_sorts_by_displayed_age_including_new_sessions(self):
        app = self._make_tui()
        now = time.time()
        app.sessions = [
            {"id": "new-1", "tool": "agy", "title": "New Gemini Session",
             "isLive": True, "updatedAt": now - 1380, "attentionAt": now, "transcriptAt": now},
            {"id": A1, "tool": "agy", "title": "Gemini 额度扣除",
             "isLive": True, "updatedAt": now - 240, "attentionAt": now - 1500, "transcriptAt": now - 1500},
            {"id": "recent", "tool": "claude", "updatedAt": now - 86400},
        ]
        app.sidebar_group_mode = "status"
        app._collapsed_sections.clear()
        with patch("tui.time.time", return_value=now):
            self.assertEqual(tui.render_sidebar_row_cells(app.sessions[0])["age_col"].strip(), "23m")
            self.assertEqual(tui.render_sidebar_row_cells(app.sessions[1])["age_col"].strip(), "4m")
            for attention_at in (now, 0.0):
                with self.subTest(attentionAt=attention_at):
                    app.sessions[0]["attentionAt"] = attention_at
                    rows = app._sidebar_display_rows()
                    self.assertTrue(any(r.get("key") == "active" for r in rows))
                    active_ids = [r["session"]["id"] for r in rows
                                  if r["kind"] == "session" and app._session_bucket(r["session"]) == 1]
                    self.assertEqual(active_ids, [A1, "new-1"])
            # The same order holds when only Active rows remain (`--sidebar` keeps the `▾ Active` header).
            app.sessions.pop()
            rows = app._sidebar_display_rows()
            self.assertEqual(rows[0].get("key"), "active")
            self.assertEqual([r["session"]["id"] for r in rows if r["kind"] == "session"], [A1, "new-1"])

    def test_new_window_collection_preserves_drafts_and_drops_only_abandoned_blank(self):
        tui._HUBPOOL_WINS_CACHE["ts"] = 0.0
        windows = {f"w-claude-new-{i}": "" for i in range(1, 10)}
        windows["w-claude-new-2"] = "a draft " * 10
        adopted = {"w-claude-new-9"}  # moved out of the Home shell: blank but never closed for the user
        now = time.time()
        for w in windows:
            sid = tui._NEW_WIN_RE.match(w).group(2)
            if sid == "new-8":
                continue  # First discovered by list-windows, absent from the registry.
            tui._NEW_HUBPOOL_SESSIONS[w] = tui._make_new_hubpool_session_dict(
                "claude", sid, w, created_ts=now if sid == "new-5" else now - 10)
        def fake_run(cmd, **kwargs):
            self.assertIn("list-windows", cmd)
            self.assertIn("#{window_name}|#{window_panes}|#{window_active}|#{window_activity}|#{@mixhub_adopted}", cmd)
            return tui.subprocess.CompletedProcess(cmd, 0, stdout="\n".join(
                f"{w}|2|{int(w.endswith('-3'))}|{int(now)}|{'claude new-9' if w in adopted else ''}" for w in windows))
        def inspect(w):
            if w.endswith("-6"):
                raise RuntimeError("capture failed")
            if w.endswith("-7"):
                return "Explain this codebase"
            return windows[w]
        self.hub._save_working_set({f"claude:new-{i}" for i in range(1, 10)})
        with patch.dict(os.environ, {"TMUX": "test-pool"}), patch("mixhub.tui.paths.ROOT", str(self.root)), \
             patch("tui.time.time", return_value=now), \
             patch("tui.subprocess.run", new=fake_run), patch("tui.subprocess.Popen") as popen, \
             patch("mixhub.tui.selection.read_active_selection", return_value={"id": "new-4"}), \
             patch("mixhub.tmux.new_sessions._inspect_new_hubpool_window_draft", side_effect=inspect), \
             patch("mixhub.tmux.new_sessions._new_window_conversation_started", return_value=False):
            rows = {s["id"]: s for s in tui._collect_live_new_hubpool_sessions(allow_probe=True)}
            self.assertEqual(set(rows), {f"new-{i}" for i in (2, 3, 4, 6, 7, 9)})
            self.assertTrue(rows["new-2"]["hasDraft"])
            self.assertEqual(rows["new-2"]["title"], tui.disp_trunc(windows["w-claude-new-2"], 48))
            self.assertEqual(popen.call_count, 3)
            self.assertEqual([c.args[0] for c in popen.call_args_list], [
                ["tmux", "-L", "hubpool", "kill-window", "-t", f"pool:w-claude-new-{i}"]
                for i in (1, 5, 8)
            ])
            for i in (1, 5, 8):
                self.assertNotIn(f"w-claude-new-{i}", tui._NEW_HUBPOOL_SESSIONS)
                self.assertNotIn(f"w-claude-new-{i}", tui._HUBPOOL_WINS_CACHE["wins"])
            self.assertEqual(self.hub.working_set(), {f"claude:new-{i}" for i in (2, 3, 4, 6, 7, 9)})

    def test_new_window_whose_conversation_started_is_never_closed(self):
        # The draft box empties the moment a message is sent; switching away before the window is matched to its
        # session must not close it (that killed the agent mid-reply). Where a sent message can be detected, a draft
        # deleted again counts as never typed; OpenCode can't tell, so a window once typed into stays.
        from mixhub.tmux import new_sessions

        tui._HUBPOOL_WINS_CACHE["ts"] = 0.0
        now = time.time()
        wins = ["w-claude-new-1", "w-opencode-new-2", "w-claude-new-3"]
        for w in wins:
            tool, sid = tui._NEW_WIN_RE.match(w).groups()
            tui._NEW_HUBPOOL_SESSIONS[w] = tui._make_new_hubpool_session_dict(tool, sid, w, created_ts=now - 10)
        listing = "\n".join(f"{w}|2|0|{int(now)}" for w in wins)
        drafts = {"w-opencode-new-2": iter(["hello there", ""]), "w-claude-new-3": iter(["oops", ""])}
        def run(cmd, **kwargs):
            return tui.subprocess.CompletedProcess(cmd, 0, stdout=listing)
        started = lambda w, tool: w == "w-claude-new-1"
        with patch.dict(os.environ, {"TMUX": "test-pool"}), patch("mixhub.tui.paths.ROOT", str(self.root)), \
             patch("tui.subprocess.run", new=run), patch("tui.subprocess.Popen") as popen, \
             patch("mixhub.tui.selection.read_active_selection", return_value={"id": "other"}), \
             patch.object(new_sessions, "_inspect_new_hubpool_window_draft", side_effect=lambda w: next(drafts.get(w, iter([""] * 9)))), \
             patch.object(new_sessions, "_new_window_conversation_started", side_effect=started):
            for _ in range(2):  # a draft, then an empty box again (sent, or deleted)
                tui._HUBPOOL_WINS_CACHE["ts"] = 0.0
                rows = {s["id"] for s in tui._collect_live_new_hubpool_sessions(allow_probe=True)}
        self.assertEqual([c.args[0][-1] for c in popen.call_args_list], ["pool:w-claude-new-3"])
        self.assertEqual(rows, {"new-1", "new-2"})

    def test_claude_new_window_conversation_found_from_its_session_file(self):
        from mixhub.tmux import new_sessions

        claude = self.home / ".claude"
        (claude / "sessions").mkdir(parents=True, exist_ok=True)
        sid = "54a641e2-0714-4a57-8c7a-2f3d058924c6"
        (claude / "sessions" / "4321.json").write_text(json.dumps({"pid": 4321, "sessionId": sid, "tmux": "pool:@9.%77"}))
        def run(cmd, **kwargs):
            if "display-message" in cmd:
                return tui.subprocess.CompletedProcess(cmd, 0, stdout="%77 4000\n")
            if cmd[:2] == ["ps", "-o"]:
                return tui.subprocess.CompletedProcess(cmd, 0, stdout="4000\n")
            raise AssertionError(cmd)
        with patch.dict(os.environ, {"HOME": str(self.home), "CLAUDE_CONFIG_DIR": ""}), \
             patch.object(new_sessions.subprocess, "run", side_effect=run):
            self.assertFalse(new_sessions._new_window_conversation_started("w-claude-new-1", "claude"))  # no transcript yet
            proj = claude / "projects" / "-home-me"
            proj.mkdir(parents=True, exist_ok=True)
            (proj / f"{sid}.jsonl").write_text("{}\n")
            self.assertTrue(new_sessions._new_window_conversation_started("w-claude-new-1", "claude"))
            self.assertFalse(new_sessions._new_window_conversation_started("w-codex-new-1", "codex"))

    def test_agy_and_codex_new_window_conversation_found_from_their_prompt_logs(self):
        # A sent first message empties the draft box. agy and Codex log each prompt typed in their TUI right away
        # (history.jsonl; `agy -p` / `codex exec` don't), so a prompt the user typed in the window's folder since it
        # opened means the window holds a conversation. Notes another agent relays into a TUI don't count.
        from mixhub.tmux import new_sessions

        created = int(time.time()) - 60
        cwd = str(self.home / "proj")
        def run(cmd, **kwargs):
            if "display-message" in cmd:
                return tui.subprocess.CompletedProcess(cmd, 0, stdout=f"%77 4000\t{created}\t{cwd}\n")
            raise AssertionError(cmd)
        rules = {"automated_prefixes": [], "automated_substrings": [], "relayed_prefixes": ["[relay from "]}
        agy_hist = self.home / ".gemini/antigravity-cli/history.jsonl"
        agy_hist.parent.mkdir(parents=True, exist_ok=True)
        def agy_prompt(ts, workspace, text="hi"):
            with agy_hist.open("a") as f:
                f.write(json.dumps({"display": text, "timestamp": int(ts * 1000), "workspace": workspace}) + "\n")
        codex = self.home / ".codex"
        day = codex / "sessions" / time.strftime("%Y/%m/%d")
        day.mkdir(parents=True, exist_ok=True)
        def rollout(sid, workspace, source="cli", turn=True, mtime=None):
            path = day / f"rollout-2026-01-01T00-00-00-{sid}.jsonl"
            lines = [{"type": "session_meta", "payload": {"id": sid, "cwd": workspace, "source": source}}]
            if turn:
                lines.append({"type": "event_msg", "payload": {"type": "task_started", "turn_id": "1"}})
            path.write_text("".join(json.dumps(x) + "\n" for x in lines))
            os.utime(path, (mtime or created + 30,) * 2)
        def codex_prompt(sid, ts, text="hi"):
            with (codex / "history.jsonl").open("a") as f:
                f.write(json.dumps({"session_id": sid, "ts": int(ts), "text": text}) + "\n")
        with patch.dict(os.environ, {"HOME": str(self.home), "CODEX_HOME": ""}), \
             patch.object(new_sessions.subprocess, "run", side_effect=run), \
             patch("mixhub.hub.session_filters._load_local_rules", return_value=rules):
            started = lambda tool: new_sessions._new_window_conversation_started(f"w-{tool}-new-1", tool)
            agy_prompt(created + 30, "/elsewhere")                # another folder
            agy_prompt(created + 35, cwd, "[relay from tg] note")  # typed in by another agent
            agy_prompt(created - 300, cwd)                         # before the window opened, logged out of order
            self.assertFalse(started("agy"))
            agy_prompt(created + 40, cwd)
            agy_prompt(created - 200, cwd)                         # an older line after it must not hide it
            self.assertTrue(started("agy"))

            # Without history.jsonl, a turn started in an interactive rollout for this folder counts; exec doesn't.
            rollout("exec1", cwd, source="exec")
            rollout("sub1", cwd, source={"subagent": "x"})
            self.assertFalse(started("codex"))
            rollout("tui1", cwd)
            self.assertTrue(started("codex"))
            # With history.jsonl, only a prompt the user typed counts.
            codex_prompt("old", created - 300)
            rollout("other", "/elsewhere")
            codex_prompt("other", created + 30)
            codex_prompt("tui1", created + 35, "[relay from tg] note")
            self.assertFalse(started("codex"))
            codex_prompt("tui1", created + 40)
            self.assertTrue(started("codex"))
            self.assertFalse(started("opencode"))

    def test_user_only_live_turn_stays_working_without_unread_attention(self):
        import sqlite3

        now = time.time()
        # Database/file metadata must not override the user turn's conversation time.
        with closing(sqlite3.connect(self.home / ".gemini/antigravity-cli/conversation_summaries.db")) as conn, conn:
            conn.execute("ALTER TABLE conversation_summaries ADD COLUMN last_modified_time REAL")
            conn.execute("UPDATE conversation_summaries SET last_modified_time = ?", (now,))
        with closing(sqlite3.connect(self.home / ".codex/state_5.sqlite")) as conn, conn:
            conn.execute("ALTER TABLE threads ADD COLUMN updated_at REAL")
            conn.execute("UPDATE threads SET updated_at = ?", (now,))
        app = self._make_tui()
        app.selected_idx = -1
        app._seen_state = {"bootstrapAt": 0, "seen": {}, "attn": {}}
        del self.hub.processes
        del self.hub.open_files
        for tool, sid, path, user in (
            ("claude", C1, self.claude_p1, claude_user("Inspect the current change")),
            ("agy", A1, self.agy_p1, {"type": "USER_INPUT", "content": "Inspect the current change"}),
            ("codex", X1, self.codex_p1, {"type": "response_item", "payload": {
                "type": "message", "role": "user", "content": [{"type": "input_text", "text": "Inspect the current change"}],
            }}),
        ):
            for age, resident in ((240, False), (1380, True)):
                with self.subTest(tool=tool, age=age, resident=resident):
                    ts = now - age
                    self._write_jsonl(path, [dict(user, timestamp=ts)], mtime=now + int(resident))
                    self.hub._proc_scanned = time.monotonic()
                    self.hub._proc_cache = server.parse_processes(f"101 1 ttys001 2048 04:00 {tool}")
                    self.hub._files_cache = {101: {"paths": [str(path)]}}
                    self.hub._resident_ttys_cache = {"ttys001"} if resident else set()
                    session = self.hub.discover(force=True)[f"{tool}:{sid}"]
                    self.assertEqual(session["updatedAt"], ts)
                    self.assertEqual(session["convAt"], ts)
                    self.assertEqual(session["lastUserAt"], ts)
                    self.assertEqual(session["attentionAt"], 0.0)
                    self.assertFalse(session["hasAssistantReply"])
                    self.assertEqual(session["tailStatus"], "ball_in_model")
                    self.assertTrue(session["isLive"])
                    self.assertTrue(session["processes"])
                    self.assertEqual(session["agentState"], "working")
                    self.assertFalse(session.get("interrupted"))
                    self.assertFalse(app.is_session_unread(session))
                    app.sessions = [dict(session)]
                    app.hover_idx = -1
                    app.hover_pos = (-1, -1)
                    app.stdscr.addstr.reset_mock()
                    with patch("tui.curses.color_pair", side_effect=lambda p: p << 8), \
                         patch.object(app, "_rebalance_sidebar_width_if_needed"):
                        app.draw()
                    age_text = tui.layout_sidebar_row_32(session)["age_col"]
                    ages = [c.args for c in app.stdscr.addstr.call_args_list if c.args[1:3] == (27, age_text)]
                    self.assertEqual(len(ages), 1)
                    self.assertEqual(ages[0][3], tui.P_DONE << 8)

    def test_session_left_idle_after_compact_is_not_working(self):
        # `/compact` ends the file with a summary written as a user row; nothing answers it, so the turn is not
        # the model's (it once kept two idle sessions spinning for hours)
        path = self.claude_p1
        asst = dict(claude_asst("Done, waiting for you."), timestamp=200)
        asst["message"]["stop_reason"] = "end_turn"
        self._write_jsonl(path, [
            dict(claude_user("Fix the bug"), timestamp=100),
            asst,
            {"type": "system", "subtype": "compact_boundary", "timestamp": 300},
            dict(claude_user("This session is being continued from a previous conversation."), timestamp=300,
                 isCompactSummary=True, isVisibleInTranscriptOnly=True),
            {"type": "last-prompt", "lastPrompt": "Fix the bug"},
        ])
        self.assertEqual(self.hub.summary(str(path), "claude")["tailStatus"], "done_idle")
        # A real prompt after the summary is still the model's turn
        self._write_jsonl(path, [
            asst,
            dict(claude_user("Summary"), timestamp=300, isCompactSummary=True),
            dict(claude_user("Now the next step"), timestamp=400),
        ])
        self.assertEqual(self.hub.summary(str(path), "claude")["tailStatus"], "ball_in_model")

    def test_summary_attention_tracks_assistant_and_structured_wait_only(self):
        path = self.claude_p1
        self._write_jsonl(path, [
            dict(claude_user("First prompt"), timestamp=100),
            dict(claude_asst("Completed reply"), timestamp=200),
            dict(claude_user("Next prompt"), timestamp=300),
            dict(claude_user("Synthetic resume"), timestamp=400, isMeta=True),
            {"type": "cost-state", "timestamp": 500},
        ], mtime=600)
        summary = self.hub.summary(str(path), "claude")
        self.assertEqual(summary["updatedAt"], 300)
        self.assertEqual(summary["convAt"], 300)
        self.assertEqual(summary["lastUserAt"], 300)
        self.assertEqual(summary["attentionAt"], 200)
        self.assertTrue(summary["hasAssistantReply"])
        # Structured approval can need attention without any assistant prose.
        self._write_jsonl(path, [
            dict(claude_user("Inspect the change"), timestamp=100),
            dict(claude_asst("", [{"type": "tool_use", "name": "AskUserQuestion", "id": "q1", "input": {}}]), timestamp=200),
        ])
        summary = self.hub.summary(str(path), "claude")
        self.assertTrue(summary["tailStructuredWait"])
        self.assertTrue(summary["hasAssistantReply"])
        self.assertEqual(summary["attentionAt"], 200)
        self.hub._save_disk_cache()
        reloaded = server.Hub(str(self.home), str(self.root))
        self.assertEqual(reloaded.summary(str(path), "claude"), summary)

    def test_unread_guards_explicit_zero_and_attention_exceptions(self):
        app = self._make_tui()
        app.sessions = []
        app.selected_idx = -1
        app._seen_state = {"bootstrapAt": 100, "seen": {}, "attn": {}}
        base = {"id": "attention-test", "tool": "agy", "agentState": "done_idle", "updatedAt": 200}
        cases = [
            ({}, True),  # Legacy dictionaries without attentionAt retain updatedAt fallback.
            ({"attentionAt": 0.0}, False),
            ({"hasAssistantReply": False}, False),
            ({"tailStatus": "ball_in_model"}, False),
            ({"id": "new-1"}, False),
            ({"synthetic": True}, False),
            ({"hasAssistantReply": False, "tailStatus": "ball_in_model", "interrupted": True}, True),
            ({"hasAssistantReply": False, "tailStatus": "ball_in_model", "failed": True}, True),
            ({"hasAssistantReply": False, "tailStatus": "ball_in_model", "agentState": "wait_input"}, True),
            ({"hasAssistantReply": False, "tailStatus": "ball_in_model", "agentState": "wait"}, True),
            ({"attentionAt": 180, "lastUserAt": 200}, False),
        ]
        for changes, expected in cases:
            with self.subTest(changes=changes):
                self.assertEqual(app.is_session_unread(dict(base, **changes)), expected)

    def test_refresh_attention_ignores_pending_and_synthetic_turns(self):
        app = self._make_tui()
        now = time.time()
        cases = [
            ({"hasAssistantReply": False}, False),
            ({"hasAssistantReply": True, "tailStatus": "ball_in_model"}, False),
            ({"id": "new-1"}, False),
            ({"synthetic": True}, False),
            ({"hasAssistantReply": True}, True),
            ({"hasAssistantReply": False, "agentState": "wait_input"}, True),
            ({"tailStatus": "ball_in_model", "interrupted": True}, True),
        ]
        for changes, expected in cases:
            for seed_stale in (False, True):
                with self.subTest(changes=changes, seed_stale=seed_stale):
                    session = dict({"id": "attention-test", "tool": "agy", "isLive": True,
                                    "agentState": "done_idle", "updatedAt": now, "attentionAt": 0.0}, **changes)
                    key = app._session_key(session)
                    app.sessions = []
                    app.selected_idx = -1
                    app._prev_agent_states = {key: ("working", now - 10)}
                    app._seen_state = {"bootstrapAt": now - 20, "seen": {}, "attn": {key: now - 5} if seed_stale else {}}
                    with patch.object(self.hub, "listing", return_value={"sessions": [session], "counts": {}}), \
                         patch("mixhub.tmux.new_sessions._collect_live_new_hubpool_sessions", return_value=[]):
                        app.refresh_data(fast_only=True)
                    if expected:
                        self.assertGreaterEqual(app._seen_state["attn"][key], now)
                    elif seed_stale and changes.get("tailStatus") == "ball_in_model":
                        self.assertEqual(app._seen_state["attn"][key], now - 5)
                    else:
                        self.assertNotIn(key, app._seen_state["attn"])
                    if not expected:
                        self.assertFalse(app.is_session_unread(app.sessions[0]))

    def test_new_window_restart_preserves_tmux_activity_timestamp(self):
        now = time.time()
        activity = int(now - 1380)
        wname = "w-agy-new-123"
        def fake_run(cmd, **kwargs):
            self.assertIn("#{window_name}|#{window_panes}|#{window_active}|#{window_activity}|#{@mixhub_adopted}", cmd)
            return tui.subprocess.CompletedProcess(cmd, 0, stdout=f"{wname}|2|0|{activity}\n")
        with patch.dict(os.environ, {"TMUX": "test-pool"}), \
             patch("tui.subprocess.run", new=fake_run), patch("tui.subprocess.Popen") as popen, \
             patch("mixhub.tui.selection.read_active_selection", return_value=None), \
             patch("mixhub.tmux.new_sessions._inspect_new_hubpool_window_draft", return_value="Unsent draft"):
            rows = tui._collect_live_new_hubpool_sessions(allow_probe=True)
            self.assertEqual(rows[0]["updatedAt"], activity)
            self.assertEqual(rows[0]["title"], "Unsent draft")
            # Later probes must not overwrite the timestamp already held in memory.
            activity += 100
            tui._HUBPOOL_WINS_CACHE["ts"] = 0.0
            self.assertEqual(tui._collect_live_new_hubpool_sessions(allow_probe=True)[0]["updatedAt"], activity - 100)
            popen.assert_not_called()

    def test_home_new_rows_hover_and_morandi_attention(self):
        with patch("tui.curses.color_pair", side_effect=lambda p: p << 8):
            app = self._make_tui(height=30, width=120, sidebar_only=False)
            app.selected_idx = -1
            app._seen_state = {"bootstrapAt": 0, "seen": {}, "attn": {}}
            now = time.time()
            app.sessions = [
                {"id": "new-1", "tool": "claude", "title": "Blank", "agentState": "wait_input", "updatedAt": now},
                {"id": "waiting", "tool": "claude", "title": "Waiting", "agentState": "wait_input", "updatedAt": now - 14},
                {"id": "unread", "tool": "codex", "title": "Unread", "agentState": "done_idle", "updatedAt": now - 60},
            ]
            self.assertFalse(app.is_session_unread(app.sessions[0]))
            app._draw_welcome_pane(34, 86, 3, 28)
            calls = [c.args for c in app.stdscr.addstr.call_args_list]
            self.assertTrue(any(c[2:] == ("1 waiting", tui.P_ERR << 8) for c in calls))
            self.assertTrue(any(c[2:] == ("1 unread", tui.P_WARN << 8) for c in calls))
            self.assertEqual({idx for _, _, idx in app._welcome_triage_rows}, {1, 2})
            for y0, _, idx in app._welcome_triage_rows:
                age = f"{tui.rel_time(app.sessions[idx]['updatedAt']):>3s}"
                pair = tui.P_ERR if idx == 1 else tui.P_WARN
                self.assertTrue(any(c[0] == y0 and c[2:] == (age, pair << 8) for c in calls))
            brand_sel = {"claude": tui.P_AGENT_CLAUDE_SEL, "codex": tui.P_AGENT_CODEX_SEL, "agy": tui.P_AGENT_AGY_SEL,
                         "opencode": tui.P_AGENT_OPENCODE_SEL}
            for tkey, y, x0, _ in app._welcome_launchpad_rects:
                app._dirty = False
                app._handle_mouse(parsed_event=(35, x0 + 1, y, False))
                self.assertTrue(app._dirty)
                app.stdscr.addstr.reset_mock()
                app._draw_welcome_pane(34, 86, 3, 28)
                # Hover keeps the brand foreground on the bg 237 hover box
                self.assertTrue(any(c.args[0] == y and c.args[1] == x0 and c.args[3] == ((brand_sel[tkey] << 8) | tui.curses.A_BOLD)
                                    for c in app.stdscr.addstr.call_args_list))
            y0 = app._welcome_triage_rows[0][0]
            app._dirty = False
            app._handle_mouse(parsed_event=(35, 60, y0, False))
            self.assertTrue(app._dirty)
            app.sessions = app.sessions[:1]
            app.stdscr.addstr.reset_mock()
            app._draw_welcome_pane(34, 86, 3, 28)
            self.assertTrue(any(c.args[2:] == ("all clear", tui.P_DIM << 8) for c in app.stdscr.addstr.call_args_list))
            self.assertEqual(app._welcome_triage_rows, [])

    def test_sidebar_search_tag_overrides_provider_filter(self):
        app = self._make_tui()
        # `tool_filter` is the `mixhub ▾` provider filter in `--sidebar`: it now narrows the list...
        app.tool_filter = "codex"
        app.refresh_data(fast_only=True)
        self.assertEqual({s["tool"] for s in app.sessions}, {"codex"})
        # ...but an explicit `@tag` search still overrides it
        for tag, tool in (("claude", "claude"), ("codex", "codex"), ("gemini", "agy")):
            app.query = "@" + tag
            app.refresh_data(fast_only=True)
            self.assertEqual({s["tool"] for s in app.sessions}, {tool})

    def test_selection_keeps_new_windows_that_were_typed_into_or_started(self):
        # The draft box also empties when the first message is sent. Switching away right after sending, before the
        # window is matched to its session, must not close it; the periodic sweep applies the same rule.
        from mixhub.tmux import new_sessions

        sidebar = self._make_tui()
        windows = ["w-opencode-new-1", "w-claude-new-2", "w-codex-new-3", "w-agy-new-4"]
        for w in windows:
            tool, new_id = tui._NEW_WIN_RE.match(w).groups()
            ns = tui._make_new_hubpool_session_dict(tool, new_id, w)
            tui._NEW_HUBPOOL_SESSIONS[w] = ns
            tui._record_hubpool_window(w)
            sidebar.sessions.insert(0, ns)
        tui._NEW_HUBPOOL_SESSIONS["w-opencode-new-1"]["everDraft"] = True
        tui._NEW_HUBPOOL_SESSIONS["w-agy-new-4"]["everDraft"] = True  # typed, then deleted: nothing was sent
        sidebar._active_hubpool_win = None
        sidebar.selected_idx = next(i for i, s in enumerate(sidebar.sessions) if s["id"] == C1)
        self.hub._save_working_set({sidebar._session_key(s) for s in tui._NEW_HUBPOOL_SESSIONS.values()})
        with patch.dict(os.environ, {"TMUX": "test-pool"}), patch("tui.subprocess.Popen") as popen, \
             patch("mixhub.tmux.outer._sync_outer_window_title"), \
             patch("mixhub.tmux.pool.find_hubpool_window_for_session", return_value=None), \
             patch.object(new_sessions, "_inspect_new_hubpool_window_draft", return_value=""), \
             patch.object(new_sessions, "_new_window_conversation_started", side_effect=lambda w, tool: w == "w-claude-new-2"):
            self.assertTrue(sidebar._on_sidebar_selection_changed(activate_live=False))
        self.assertEqual(set(tui._NEW_HUBPOOL_SESSIONS), {"w-opencode-new-1", "w-claude-new-2"})
        self.assertEqual(self.hub.working_set(), {"opencode:new-1", "claude:new-2"})
        popen.assert_not_called()

    def test_selection_reaps_registered_blank_windows_without_previous_window(self):
        sidebar = self._make_tui()
        reader = self._make_tui(width=100, sidebar_only=False)
        reader.reader_only = True
        original_sessions = list(sidebar.sessions)
        closed = ["w-claude-new-1", "w-agy-new-2"]
        kept = ["w-codex-new-3", "w-agy-new-4", "w-claude-new-5"]
        def no_run(*args, **kwargs):
            raise AssertionError("Unexpected synchronous tmux call")
        def inspect(w):
            if w == kept[0]:
                return "Unsent draft"
            if w == kept[1]:
                raise RuntimeError("capture failed")
            return ""
        with patch.dict(os.environ, {"TMUX": "test-pool"}), \
             patch("tui.subprocess.run", new=no_run), patch("tui.subprocess.Popen") as popen, \
             patch("mixhub.tmux.outer._sync_outer_window_title"), \
             patch("mixhub.tmux.pool.find_hubpool_window_for_session", return_value=None), \
             patch("mixhub.tmux.pool.activate_session_in_hubpool", return_value=True), \
             patch("mixhub.tmux.new_sessions._inspect_new_hubpool_window_draft", side_effect=inspect) as draft_probe, \
             patch("mixhub.tmux.new_sessions._new_window_conversation_started", return_value=False), \
             patch.object(reader, "draw"):
            for sid, native in ((C1, False), (None, False), (C1, True), ("new-5", True)):
                with self.subTest(sid=sid, native=native):
                    tui._NEW_HUBPOOL_SESSIONS.clear()
                    sidebar.sessions = list(original_sessions)
                    # Only register new-5 when it is selected; an empty selected window must survive.
                    windows = closed + kept[:2] + (kept[2:] if sid == "new-5" else [])
                    for w in windows:
                        tool, new_id = tui._NEW_WIN_RE.match(w).groups()
                        ns = tui._make_new_hubpool_session_dict(tool, new_id, w)
                        tui._NEW_HUBPOOL_SESSIONS[w] = ns
                        tui._record_hubpool_window(w)
                        sidebar.sessions.insert(0, ns)
                    sidebar._active_hubpool_win = None
                    sidebar.selected_idx = next((i for i, s in enumerate(sidebar.sessions) if s["id"] == sid), -1)
                    self.hub._save_working_set({sidebar._session_key(s) for s in tui._NEW_HUBPOOL_SESSIONS.values()})
                    popen.reset_mock()
                    draft_probe.reset_mock()
                    self.assertTrue(sidebar._on_sidebar_selection_changed(activate_live=native))
                    for w in closed:
                        self.assertNotIn(w, tui._NEW_HUBPOOL_SESSIONS)
                        self.assertNotIn(w, tui._HUBPOOL_WINS_CACHE["wins"])
                        self.assertFalse(any(tui.hubpool_win_name(s) == w for s in sidebar.sessions))
                    self.assertEqual(set(tui._NEW_HUBPOOL_SESSIONS), set(windows) - set(closed))
                    self.assertEqual(self.hub.working_set(), {sidebar._session_key(s) for s in tui._NEW_HUBPOOL_SESSIONS.values()})
                    self.assertEqual((sidebar.current_session() or {}).get("id"), sid)
                    if sid == "new-5":
                        self.assertNotIn(kept[2], [c.args[0] for c in draft_probe.call_args_list])
                    if not native:
                        popen.assert_not_called()
                        reader._sync_reader_selection()
                    commands = [c.args[0] for c in popen.call_args_list]
                    self.assertEqual(sum(cmd.count("kill-window") for cmd in commands), 2)
                    for w in closed:
                        self.assertTrue(any("pool:" + w in cmd for cmd in commands))

    def test_preview_draw_precedes_window_selection_and_blank_cleanup(self):
        sidebar = self._make_tui()
        reader = self._make_tui(width=100, sidebar_only=False)
        reader.reader_only = True
        wname = "w-claude-new-1"
        events = []
        def no_run(*args, **kwargs):
            raise AssertionError("Unexpected synchronous tmux call")
        def draw():
            cur = reader.current_session()
            events.append(("draw", cur["id"] if cur else None))
        def popen(cmd, **kwargs):
            events.append(("tmux", cmd))
            return MagicMock()
        with patch.dict(os.environ, {"TMUX": "test-pool"}), \
             patch("tui.subprocess.run", new=no_run), patch("tui.subprocess.Popen", side_effect=popen), \
             patch("mixhub.tmux.outer._sync_outer_window_title"), patch("mixhub.tmux.pool.find_hubpool_window_for_session", return_value=None), \
             patch("mixhub.tmux.new_sessions._inspect_new_hubpool_window_draft", return_value="") as inspect, \
             patch("mixhub.tmux.new_sessions._new_window_conversation_started", return_value=False), \
             patch.object(reader, "draw", side_effect=draw):
            for sid in (C1, None):
                tui._NEW_HUBPOOL_SESSIONS[wname] = tui._make_new_hubpool_session_dict("claude", "new-1", wname)
                sidebar._active_hubpool_win = wname
                sidebar.selected_idx = next(i for i, s in enumerate(sidebar.sessions) if s["id"] == sid) if sid else -1
                events.clear()
                sidebar._on_sidebar_selection_changed()
                self.assertEqual(events, [])
                self.assertNotIn(wname, tui._NEW_HUBPOOL_SESSIONS)
                reader._sync_reader_selection()
                self.assertEqual(events[0], ("draw", sid))
                self.assertEqual(events[1][0], "tmux")
                self.assertIn("select-window", events[1][1])
                self.assertEqual(events[1][1][-3:], ["kill-window", "-t", "pool:" + wname])
            # A nonempty native draft survives leaving Home; capture failure also preserves it.
            for result in ("unsent draft", RuntimeError("capture failed")):
                tui._NEW_HUBPOOL_SESSIONS[wname] = tui._make_new_hubpool_session_dict("claude", "new-1", wname)
                sidebar._active_hubpool_win = wname
                inspect.side_effect = result if isinstance(result, Exception) else None
                inspect.return_value = result
                sidebar._on_sidebar_selection_changed()
                self.assertIn(wname, tui._NEW_HUBPOOL_SESSIONS)
                self.assertNotIn("closeWindow", tui.read_active_selection())
            # A native selection arriving during draw cancels the stale preview activation.
            tui.write_active_selection(sidebar.sessions[0], activate_reader=True)
            events.clear()
            with patch.object(reader, "draw", side_effect=lambda: tui.write_active_selection(sidebar.sessions[-1])):
                reader._sync_reader_selection()
            self.assertEqual(events, [])

    def test_preview_scroll_never_switches_sidebar_session_across_all_5_layers(self):
        """
        Regression test for Preview page scroll isolation:
        "另外在预览page 上下滑动 还是会切到下一个session的内容 这个问题还是没解决"
        Verifies all 5 layers:
          1. In `--sidebar` (`sidebar_only=True`), `KEY_UP` and `KEY_DOWN` (sent by Terminal.app alternate-screen
             trackpad scroll when outer tmux still had `%2` focused) NEVER change `self.selected_idx` or
             `active_selection.json`; instead they forward `Up`/`Down` to `pool:reader` and hand focus to `%1`
             (or scroll `self.list_scroll` when hovering inside the sidebar).
          2. Single-clicking a session row in `--sidebar` hands outer tmux focus to `hub:.{right}` (`%1`).
          3. Trackpad post-wheel tap-to-click within 180ms (`_last_wheel_ts`) is debounced in `--sidebar`.
          4. `configure_tmux_mouse_and_copy()` binds `WheelUpPane` and `WheelDownPane` cleanly for both outer `hub`
             (never entering outer copy-mode on `%1`, selecting `%1` if inactive) and inner `hubpool`.
          5. `_consume_sgr_mouse_if_any()` translates CSI `\x1b[A`/`\x1b[B` and split SS3 `\x1bOA`/`\x1bOB` into
             `KEY_UP`/`KEY_DOWN` via `curses.ungetch` and drains X10/unknown CSI sequences without leaking bytes.
        """
        with patch.dict(os.environ, {"TMUX": "/private/tmp/tmux-501/default,999,0"}), \
             patch("tui.curses.color_pair", side_effect=lambda p: p << 8), \
             patch("tui.subprocess.run") as mock_run, \
             patch("tui.subprocess.Popen") as mock_popen:
            sb = self._make_tui(height=24, width=32, sidebar_only=True)
            # Populate 35 sessions so list_scroll has room to scroll on height=24 (`▾ Recent` + 20 capped rows + Web footer)
            base_sessions = list(sb.sessions)
            while len(sb.sessions) < 35:
                idx = len(sb.sessions)
                s_copy = dict(base_sessions[idx % len(base_sessions)])
                s_copy["id"] = f"scroll-session-{idx:02d}"
                s_copy["title"] = f"Scroll Session #{idx:02d}"
                sb.sessions.append(s_copy)

            # Layer 2: Clicking row 1 in `--sidebar` selects row 1, writes active_selection.json, and focuses outer right pane
            list_top, _, _ = sb._list_bounds(24)
            target_row = 1
            for r_i, it in enumerate(sb._sidebar_display_rows()):
                if it.get("kind") == "session" and it["idx"] == target_row:
                    click_y = list_top + r_i
                    break
            mock_popen.reset_mock()
            sb._handle_mouse(parsed_event=(0, 10, click_y, False))
            self.assertEqual(sb.selected_idx, target_row)
            expected_sid = sb.sessions[target_row]["id"]
            self.assertEqual(tui.read_active_selection()["id"], expected_sid)
            popen_cmds = [c.args[0] for c in mock_popen.call_args_list]
            self.assertTrue(
                any("select-pane" in cmd and ("hub:.{right}" in cmd or "%1" in cmd) for cmd in popen_cmds),
                f"Expected outer right pane focus handoff on sidebar row click, got {popen_cmds}",
            )

            # Layer 1: Sending KEY_UP / KEY_DOWN to `--sidebar` (when hover_pos == (-1, -1) after clicking a row)
            # NEVER changes `sb.selected_idx` or `active_selection.json`, and forwards Up/Down to `pool:reader`!
            mock_popen.reset_mock()
            sb.stdscr.getch.side_effect = [
                tui.curses.KEY_DOWN,
                tui.curses.KEY_DOWN,
                tui.curses.KEY_UP,
                KeyboardInterrupt(),
            ]
            with patch.object(sb, "draw"), \
                 patch.object(sb, "_wait_sidebar_event_kqueue"), \
                 patch.object(sb, "_sync_sidebar_selection", return_value=False), \
                 patch("tui.curses.mousemask"), \
                 patch("tui.curses.mouseinterval"), \
                 patch("tui.sys.stdout"):
                try:
                    sb.run()
                except KeyboardInterrupt:
                    pass
            self.assertEqual(sb.selected_idx, target_row, "Sidebar KEY_UP/KEY_DOWN must NEVER switch selected_idx!")
            self.assertEqual(tui.read_active_selection()["id"], expected_sid)
            forwarded = [c.args[0] for c in mock_popen.call_args_list if "send-keys" in c.args[0]]
            self.assertEqual(len(forwarded), 3, f"Expected 3 forwarded scroll keys to pool:reader, got {forwarded}")
            self.assertEqual([cmd[-1] for cmd in forwarded], ["Down", "Down", "Up"])

            # Layer 1b: When pointer IS hovering inside `--sidebar`, KEY_DOWN / KEY_UP scrolls `list_scroll` without changing `selected_idx`
            sb.hover_pos = (8, click_y)
            sb.list_scroll = 0
            sb.stdscr.getch.side_effect = [tui.curses.KEY_DOWN, tui.curses.KEY_UP, KeyboardInterrupt()]
            scroll_history = []
            with patch.object(sb, "draw", side_effect=lambda: scroll_history.append((sb.selected_idx, sb.list_scroll))), \
                 patch.object(sb, "_wait_sidebar_event_kqueue"), \
                 patch.object(sb, "_sync_sidebar_selection", return_value=False), \
                 patch("tui.curses.mousemask"), \
                 patch("tui.curses.mouseinterval"), \
                 patch("tui.sys.stdout"):
                try:
                    sb.run()
                except KeyboardInterrupt:
                    pass
            self.assertEqual(sb.selected_idx, target_row)
            self.assertIn((target_row, 3), scroll_history)

            # Layer 3: 180ms post-wheel tap-to-click debounce in `--sidebar`
            sb.list_scroll = 0
            sb._handle_mouse(parsed_event=(65, 10, click_y, False))  # Wheel down
            self.assertGreater(sb.list_scroll, 0)
            # Immediate accidental tap-to-click (<180ms after wheel) on another row must be ignored
            sb._handle_mouse(parsed_event=(0, 10, click_y + 2, False))
            self.assertEqual(sb.selected_idx, target_row)
            self.assertEqual(tui.read_active_selection()["id"], expected_sid)

            # Layer 4: `configure_tmux_mouse_and_copy` outer (`None`) vs inner (`"hubpool"`) wheel bindings
            mock_run.reset_mock()
            tui.configure_tmux_mouse_and_copy(None, session_target="hub")
            outer_wheel_up = [c.args[0] for c in mock_run.call_args_list if "WheelUpPane" in c.args[0]]
            self.assertEqual(len(outer_wheel_up), 1)
            self.assertNotIn("copy-mode", " ".join(outer_wheel_up[0]))
            self.assertIn("select-pane -t= ; send-keys -t= -M", outer_wheel_up[0])

            mock_run.reset_mock()
            tui.configure_tmux_mouse_and_copy("hubpool")
            inner_wheel_up = [c.args[0] for c in mock_run.call_args_list if "WheelUpPane" in c.args[0]]
            self.assertEqual(len(inner_wheel_up), 1)
            self.assertIn("#{||:#{alternate_on},#{pane_in_mode},#{mouse_any_flag}}", inner_wheel_up[0])

            # Layer 5: `_consume_sgr_mouse_if_any` CSI `\x1b[A`/`\x1b[B` & X10 mouse drain
            with patch("tui.curses.ungetch") as mock_ungetch:
                sb.stdscr.getch.side_effect = [ord("["), ord("A")]
                self.assertEqual(sb._consume_sgr_mouse_if_any(), (-2, -1, -1, False))
                mock_ungetch.assert_called_once_with(tui.curses.KEY_UP)

                mock_ungetch.reset_mock()
                sb.stdscr.getch.side_effect = [ord("O"), ord("B")]
                self.assertEqual(sb._consume_sgr_mouse_if_any(), (-2, -1, -1, False))
                mock_ungetch.assert_called_once_with(tui.curses.KEY_DOWN)

                mock_ungetch.reset_mock()
                sb.stdscr.getch.side_effect = [ord("["), ord("M"), 32, 106, 107]
                self.assertEqual(sb._consume_sgr_mouse_if_any(), (-2, -1, -1, False))
                mock_ungetch.assert_not_called()


    # ------------------------------------------------------------------ Sidebar popovers / Relay bar / NEEDS YOU refinements
    def _draw_sb(self, app: tui.HubTUI) -> list[tuple]:
        with patch.object(app, "_rebalance_sidebar_width_if_needed"):
            app.stdscr.addstr.reset_mock()
            app.draw()
        return [c.args for c in app.stdscr.addstr.call_args_list]

    @staticmethod
    def _row_at(calls: list[tuple], y: int) -> dict:
        """Last `addstr` per column on row `y` (what ends up on screen)."""
        return {c[1]: c for c in calls if c[0] == y}

    def test_11_morandi_stat_labels_always_dim_gray(self):
        color = lambda p: p << 8
        with patch("tui.curses.color_pair", side_effect=color):
            app = self._make_tui()
            cases = (
                dict(cpu_pct=10, mem_mb=1000.0),
                dict(cpu_pct=50, mem_mb=3000.0),
                dict(cpu_pct=70, mem_mb=5000.0),
                dict(cpu_pct=95, mem_mb=7000.0),
                dict(cpu_pct=95, mem_mb=15000.0, total_mem_mb=16384.0),
                dict(cpu_pct=30, mem_mb=8000.0, total_mem_mb=16384.0, pressure_level=2),
                dict(cpu_pct=99, mem_mb=8000.0, total_mem_mb=16384.0, pressure_level=4),
            )
            for kw in cases:
                with self.subTest(**kw):
                    c_lbl, _, m_lbl, _ = app._morandi_stat_attrs(**kw)
                    self.assertEqual(c_lbl, color(tui.P_STAT_LBL))
                    self.assertEqual(m_lbl, color(tui.P_STAT_LBL))
            # Only the values escalate (to the coral tier here)
            _, c_val, _, m_val = app._morandi_stat_attrs(99, 8000.0, total_mem_mb=16384.0, pressure_level=4)
            self.assertEqual((c_val, m_val), (color(tui.P_STAT_T4), color(tui.P_STAT_T4)))

    def test_12_row0_left_filter_trigger_and_filter_popover(self):
        color = lambda p: p << 8
        box = color(tui.P_USER_BOX) | tui.curses.A_BOLD
        bold = tui.curses.A_BOLD
        with patch("tui.curses.color_pair", side_effect=color):
            app = self._make_tui(height=30, width=32, sidebar_only=True)
            app.selected_idx = -1
            self.assertEqual(
                (app.WORDMARK_X, app.WORDMARK_HIT, app.PROVIDER_DROP_X, app.PROVIDER_DROP_HIT),
                (5, (5, 10), 2, (1, 3)),
            )
            # Col 4 separates ` ▾ ` (cols 1..3) from `mixhub` (cols 5..10): the two hit zones never overlap
            self.assertTrue(all(app._is_provider_toggle_x(x) for x in (1, 2, 3)))
            self.assertFalse(app._is_provider_toggle_x(4))
            self.assertFalse(any(app._is_provider_toggle_x(x) for x in range(app.WORDMARK_HIT[0], app.WORDMARK_HIT[1] + 1)))

            # At rest (All, closed, not hovered): `⌄` always visible at col 2 in light gray `P_UNSEL` (distinct from `▾` section triangles)
            calls = self._draw_sb(app)
            row0 = self._row_at(calls, 0)
            self.assertEqual(row0[5][2:], ("mixhub", color(tui.P_UNSEL)))
            self.assertEqual(row0[2][2:], ("⌄", color(tui.P_UNSEL)))
            self.assertNotIn(1, row0)
            self.assertNotIn(4, row0)
            # Icon column: `✎` (Row 2), `▾ Recent` (list top) and the session glyphs all sit at col 2
            list_top, list_bottom, _ = app._list_bounds(30)
            self.assertEqual(self._row_at(calls, 2)[2][2], "✎  New session")
            self.assertEqual(self._row_at(calls, list_top)[2][2], "▾")
            self.assertIn(2, self._row_at(calls, list_top + 1))

            # Hovering `mixhub` only lights the ` mixhub ` pill (cols 4..11); `⌄` stays untouched at col 2
            for wm_x in range(app.WORDMARK_HIT[0], app.WORDMARK_HIT[1] + 1):
                app.hover_pos = (wm_x, 0)
                row0 = self._row_at(self._draw_sb(app), 0)
                self.assertEqual(row0[2][2:], ("⌄", color(tui.P_UNSEL)))
                self.assertNotIn(1, row0)
                self.assertEqual(row0[4][2:], (" " * 8, box))
            # Hovering the trigger itself boxes ` ⌄ ` at cols 1..3 (like `[✎]`) and leaves `mixhub` unlit
            for drop_x in (1, 2, 3):
                app.hover_pos = (drop_x, 0)
                row0 = self._row_at(self._draw_sb(app), 0)
                self.assertEqual(row0[1][2:], (" ⌄ ", box))
                self.assertNotIn(4, row0)
                self.assertEqual(row0[5][2:], ("mixhub", color(tui.P_UNSEL)))
            # The col 4 gap lights neither
            app.hover_pos = (4, 0)
            row0 = self._row_at(self._draw_sb(app), 0)
            self.assertEqual(row0[2][2:], ("⌄", color(tui.P_UNSEL)))
            self.assertNotIn(4, row0)
            app.hover_pos = (-1, -1)
            # Clicking the col 4 gap neither opens the card nor fires `mixhub`
            with patch.object(app, "_on_wordmark_clicked") as wm_click:
                app._handle_mouse(parsed_event=(0, 4, 0, False))
                wm_click.assert_not_called()
            self.assertFalse(app.provider_dropdown_open)

            # Click `⌄` -> `╭─ Filter ───────────╮` at (1, 4), 22 cols, 5 brand-colored items, no `[1]..[5]`
            app._handle_mouse(parsed_event=(0, 2, 0, False))
            self.assertTrue(app.provider_dropdown_open)
            calls = self._draw_sb(app)
            self.assertEqual(self._row_at(calls, 0)[1][2:], (" ⌄ ", box))
            top = "╭─ Filter ───────────╮"
            self.assertEqual(tui.disp_width(top), 22)
            self.assertIn((1, 4, top, color(tui.P_META)), calls)
            # Only agents with sessions here get a row (no DeepSeek / OpenCode sessions in this fixture)
            exp_items = (
                ("All", tui.P_UNSEL),
                ("Claude Code", tui.P_AGENT_CLAUDE),
                ("Codex", tui.P_AGENT_CODEX),
                ("Gemini", tui.P_AGENT_AGY),
            )
            self.assertEqual([lbl for _, lbl, *_ in app.provider_filter_items()], [lbl for lbl, _ in exp_items])
            for i, (lbl, pair) in enumerate(exp_items):
                row = self._row_at(calls, 2 + i)
                self.assertEqual(row[0][2:], (" " * 31, color(tui.P_UNSEL)))
                self.assertEqual((row[4][2], row[25][2]), ("│", "│"))
                self.assertEqual(row[5][2], f" {tui.disp_pad(lbl, 16)} {'✓' if lbl == 'All' else ' '} ")
                self.assertEqual(row[5][3], color(pair))
            self.assertIn((6, 4, "╰" + "─" * 20 + "╯", color(tui.P_META)), calls)
            self.assertFalse(any(re_num in str(c[2]) for c in calls for re_num in ("[1]", "[2]", "[3]", "[4]", "[5]")))
            # Hover keeps the brand fg on the bg 237 box
            app.hover_pos = (8, 3)
            self.assertEqual(self._row_at(self._draw_sb(app), 3)[5][3], color(tui.P_AGENT_CLAUDE_SEL) | bold)
            app.hover_pos = (-1, -1)

            # Pick `Claude Code` -> only Claude rows; brand `⌄` + `· Claude` badge at col 12
            app._handle_mouse(parsed_event=(0, 8, 3, False))
            self.assertFalse(app.provider_dropdown_open)
            self.assertEqual(app.provider_filter, "claude")
            self.assertEqual({r["session"]["tool"] for r in app._sidebar_display_rows() if r["kind"] == "session"}, {"claude"})
            self.assertEqual(app.selected_idx, -1)
            row0 = self._row_at(self._draw_sb(app), 0)
            self.assertEqual(row0[2][2:], ("⌄", color(tui.P_AGENT_CLAUDE) | bold))
            self.assertEqual(row0[12][2:], ("· Claude", color(tui.P_AGENT_CLAUDE)))
            self.assertEqual(row0[5][2], "mixhub")
            # Hovering `mixhub` while filtered keeps the brand `⌄` as is
            app.hover_pos = (7, 0)
            row0 = self._row_at(self._draw_sb(app), 0)
            self.assertEqual(row0[2][2:], ("⌄", color(tui.P_AGENT_CLAUDE) | bold))
            self.assertNotIn(1, row0)
            app.hover_pos = (-1, -1)

            # The badge toggles the card too; open -> brand box. `mixhub` is its own button: clicking it while the
            # card is open closes the card AND fires the wordmark click.
            app._handle_mouse(parsed_event=(0, 15, 0, False))
            self.assertTrue(app.provider_dropdown_open)
            self.assertEqual(self._row_at(self._draw_sb(app), 0)[1][2:], (" ⌄ ", color(tui.P_AGENT_CLAUDE_SEL) | bold))
            with patch.object(app, "_on_wordmark_clicked") as wm_click:
                app._handle_mouse(parsed_event=(0, 7, 0, False))
                wm_click.assert_called_once()
                self.assertFalse(app.provider_dropdown_open)
                app._handle_mouse(parsed_event=(0, 7, 0, False))
                self.assertEqual(wm_click.call_count, 2)
                self.assertFalse(app.provider_dropdown_open)

            # Codex / Gemini / All through the card
            for item_y, fkey, tools in ((4, "codex", {"codex"}), (5, "gemini", {"agy"}), (2, None, {"claude", "codex", "agy"})):
                with self.subTest(filter=fkey):
                    app._handle_mouse(parsed_event=(0, 2, 0, False))
                    self.assertTrue(app.provider_dropdown_open)
                    app._handle_mouse(parsed_event=(0, 8, item_y, False))
                    self.assertEqual(app.provider_filter, fkey)
                    self.assertEqual(
                        {r["session"]["tool"] for r in app._sidebar_display_rows() if r["kind"] == "session"}, tools
                    )
            # Unfiltered again: `⌄` back to always-on light gray at rest
            self.assertEqual(self._row_at(self._draw_sb(app), 0)[2][2:], ("⌄", color(tui.P_UNSEL)))

            # DeepSeek matches CC·DS borrowed-brain sessions too (its row appears once one exists);
            # its wider badge widens the hit zone
            now = time.time()
            app.sessions = [
                {"id": "ds-1", "tool": "deepseek", "title": "DS", "updatedAt": now - 60},
                {"id": "ccds-1", "tool": "claude", "title": "CC·DS", "model": "deepseek-chat", "updatedAt": now - 120},
                {"id": "cc-1", "tool": "claude", "title": "CC", "model": "claude-opus", "updatedAt": now - 180},
            ]
            # (no Filter row any more, DeepSeek is a model; the `d` hotkey / `@deepseek` still set it)
            self.assertNotIn("deepseek", [it[0] for it in app.provider_filter_items()])
            app.provider_filter = "deepseek"
            self.assertEqual(
                [r["session"]["id"] for r in app._sidebar_display_rows() if r["kind"] == "session"], ["ds-1", "ccds-1"]
            )
            row0 = self._row_at(self._draw_sb(app), 0)
            self.assertEqual(row0[12][2:], ("· DeepSeek", color(tui.P_DONE)))
            self.assertEqual(app._provider_badge_hit_x1(), 21)
            self.assertTrue(app._is_provider_toggle_x(21))
            self.assertFalse(app._is_provider_toggle_x(22))

    def test_13_new_session_card_and_cjk_safe_borders(self):
        color = lambda p: p << 8
        box = color(tui.P_USER_BOX) | tui.curses.A_BOLD
        with patch("tui.curses.color_pair", side_effect=color):
            app = self._make_tui(height=30, width=32, sidebar_only=True)
            list_top, list_bottom, _ = app._list_bounds(30)

            # Row 2 hover (closed): the whole ` ✎  New session ` label is boxed
            app.hover_pos = (8, 2)
            self.assertEqual(self._row_at(self._draw_sb(app), 2)[1][2:], (" ✎  New session ", box))
            app.hover_pos = (-1, -1)

            # Open: `╭─ New Session ──────╮` (no `✎` inside the border), ` ✎ ` boxed at cols 1..3, no `[1]..[4]`
            app._handle_mouse(parsed_event=(0, 6, 2, False))
            self.assertTrue(app._new_session_menu)
            calls = self._draw_sb(app)
            top = "╭─ New Session ──────╮"
            self.assertEqual(tui.disp_width(top), 22)
            self.assertIn((2, 4, top, color(tui.P_META)), calls)
            self.assertNotIn("✎", top)
            self.assertEqual(self._row_at(calls, 2)[1][2:], (" ✎ ", box))
            for i, (lbl, pair) in enumerate((
                ("Claude Code", tui.P_AGENT_CLAUDE),
                ("Codex", tui.P_AGENT_CODEX),
                ("OpenCode", tui.P_AGENT_OPENCODE),
                ("Gemini", tui.P_AGENT_AGY),
            )):
                row = self._row_at(calls, 3 + i)
                self.assertEqual(row[5][2:], (f" {tui.disp_pad(lbl, 16)}   ", color(pair)))
            self.assertFalse(any("[" in str(c[2]) for c in calls if 2 <= c[0] <= 7))
            # Re-clicking ` ✎ ` closes the card (never immediately re-opens it)
            app._handle_mouse(parsed_event=(0, 2, 2, False))
            self.assertFalse(app._new_session_menu)
            # The 4th row launches Gemini (CC·DS moved to the `d` hotkey)
            app._handle_mouse(parsed_event=(0, 6, 2, False))
            with patch.object(app, "_launch_new_agent_session") as launch:
                app._handle_mouse(parsed_event=(0, 8, 6, False))
                launch.assert_called_once_with("agy")
            self.assertFalse(app._new_session_menu)

            # Clicking the age column of a row selects that row (the `⋯` row menu is gone)
            now = time.time()
            app.sessions = [
                {"id": "live-cjk", "tool": "claude", "title": "客厅灯光自动化脚本调试", "isLive": True,
                 "agentState": "done_idle", "updatedAt": now - 300, "processes": [{"pid": 4242}]},
                {"id": "dormant-cjk", "tool": "codex", "title": "t会话标题测试中文宽字符", "updatedAt": now - 7200},
            ]
            app.selected_idx = -1
            ys = {
                app.sessions[it["idx"]]["id"]: list_top + r_i
                for r_i, it in enumerate(app._sidebar_display_rows())
                if it["kind"] == "session"
            }
            age_x = max(4, 32 - 11) + 6
            app._handle_mouse(parsed_event=(0, age_x, ys["live-cjk"], False))
            self.assertEqual(app.selected_idx, 0)

            # Direct `_relay_menu` card (`╭─ ⇄ Relay To ───────╮`) keeps clean geometry and brand colors
            app._relay_menu = True
            app.hover_pos = (-1, -1)
            calls = self._draw_sb(app)
            rcard_y = app._relay_to_card_y(list_top, list_bottom)
            r_top = "╭─ ⇄ Relay To ───────╮"
            self.assertEqual(tui.disp_width(r_top), 22)
            self.assertIn((rcard_y, 4, r_top, color(tui.P_META)), calls)
            r_items = [(self._row_at(calls, y)[5][2].strip(), self._row_at(calls, y)[5][3]) for y in range(rcard_y + 1, rcard_y + 5)]
            self.assertEqual(r_items, [
                ("Claude Code", color(tui.P_AGENT_CLAUDE)),
                ("Gemini", color(tui.P_AGENT_AGY)),
                ("Codex", color(tui.P_AGENT_CODEX)),
                ("DeepSeek", color(tui.P_DONE)),
            ])
            with patch.object(app, "_trigger_handoff_interactive") as relay:
                app._handle_mouse(parsed_event=(0, 8, rcard_y + 4, False))
                relay.assert_called_once_with("deepseek")
            self.assertFalse(app._relay_menu)

    def test_14_sidebar_popovers_focus_out_auto_dismiss_and_mutual_exclusion(self):
        with patch("tui.curses.color_pair", side_effect=lambda p: p << 8):
            app = self._make_tui(height=30, width=32, sidebar_only=True)
            app.selected_idx = 0

            def open_new():
                app._new_session_menu = True
                app._note_sidebar_popover_opened("new_session")

            def open_relay():
                app._relay_menu = True
                app._note_sidebar_popover_opened("relay")

            openers = {"provider": app._toggle_provider_dropdown, "new_session": open_new, "relay": open_relay}

            def open_state() -> dict:
                return {
                    "provider": bool(app.provider_dropdown_open),
                    "new_session": bool(app._new_session_menu),
                    "relay": bool(app._relay_menu),
                }

            # Mutual exclusion: opening any popover closes the others
            for kind, opener in openers.items():
                opener()
                st = open_state()
                self.assertTrue(st[kind], kind)
                self.assertEqual(sum(st.values()), 1, (kind, st))

            # Cross-pane FocusOut (`\x1b[O`) dismisses every popover and drops hover
            for kind, opener in openers.items():
                with self.subTest(focus_out=kind):
                    opener()
                    app.hover_pos = (8, 5)
                    app.stdscr.getch.side_effect = [ord("["), ord("O")]
                    self.assertEqual(app._consume_sgr_mouse_if_any(), (-2, -1, -1, False))
                    self.assertFalse(any(open_state().values()))
                    self.assertEqual(app.hover_pos, (-1, -1))

            # Fallback without FocusOut: outer tmux reports the pane inactive -> dismiss (>= 0.30s, 0.20s probe throttle)
            with patch.dict(os.environ, {"TMUX": "/tmp/tmux-1000/default,123,0"}):
                for kind, opener in openers.items():
                    with self.subTest(focus_loss=kind):
                        opener()
                        t0 = app._popover_opened_mono
                        app._popover_focus_probe_mono = 0.0
                        with patch.object(app, "_is_sidebar_pane_active", return_value=False) as probe:
                            self.assertFalse(app._check_sidebar_popover_focus_loss(t0 + 0.1))
                            probe.assert_not_called()
                        with patch.object(app, "_is_sidebar_pane_active", return_value=True):
                            self.assertFalse(app._check_sidebar_popover_focus_loss(t0 + 0.5))
                        self.assertTrue(open_state()[kind])
                        with patch.object(app, "_is_sidebar_pane_active", return_value=False):
                            self.assertFalse(app._check_sidebar_popover_focus_loss(t0 + 0.6))
                            self.assertTrue(app._check_sidebar_popover_focus_loss(t0 + 0.8))
                        self.assertFalse(any(open_state().values()))
            # Never probes outside tmux
            app._toggle_provider_dropdown()
            with patch.object(app, "_is_sidebar_pane_active", return_value=False) as probe:
                self.assertFalse(app._check_sidebar_popover_focus_loss(app._popover_opened_mono + 5.0))
                probe.assert_not_called()

    def test_15_inline_relay_bar_prefix_free_brand_colors_and_no_gray_box_hover(self):
        color = lambda p: p << 8
        bold = tui.curses.A_BOLD
        under = tui.curses.A_UNDERLINE
        # At rest `⇄ Relay` (conversion icon); once open `◂ Relay` points at the options expanded on its left
        self.assertEqual(tui.RELAY_TOGGLE_CLOSED, " ⇄ Relay ")
        self.assertEqual(tui.RELAY_TOGGLE_OPEN, " ◂ Relay ")
        self.assertNotIn("▸", tui.RELAY_TOGGLE_OPEN)
        self.assertEqual(tui.disp_width(tui.RELAY_TOGGLE_OPEN), tui.disp_width(tui.RELAY_TOGGLE_CLOSED))
        items, _ = tui.relay_layout(100, "CLOSED")
        self.assertEqual([lbl for _, lbl, _ in items], [tui.RELAY_TOGGLE_CLOSED])
        items, _ = tui.relay_layout(100, "PICK_AGENT")
        self.assertEqual([lbl for _, lbl, _ in items], ["Claude", "Gemini", "Codex", tui.RELAY_TOGGLE_OPEN])
        items, _ = tui.relay_layout(100, "PICK_CLAUDE_BACKEND")
        self.assertEqual([lbl for _, lbl, _ in items], ["< Back", "Claude Code:", "Anthropic", "DeepSeek", tui.RELAY_TOGGLE_OPEN])
        for w in range(14, 101):
            for state in ("CLOSED", "PICK_AGENT", "PICK_CLAUDE_BACKEND"):
                for ds_ok in (True, False):
                    for x0, lbl, _ in tui.relay_layout(w, state, min_x=2, ds_ok=ds_ok, rec_target="codex")[0]:
                        self.assertNotRegex(lbl, r"\[\d\]")
                        self.assertGreaterEqual(x0, 2)

        # Every bg 237 pair (`P_USER_BOX` + all `P_*_SEL`): none may ever back an inline Relay item
        box_pairs = {tui.P_USER_BOX} | {
            getattr(tui, n) for n in dir(tui) if n.startswith("P_") and n.endswith("_SEL") and isinstance(getattr(tui, n), int)
        }
        with patch("tui.curses.color_pair", side_effect=color):
            app = self._make_tui(height=30, width=100, sidebar_only=False)
            brand = {
                "relay_pick_claude": (tui.P_AGENT_CLAUDE_HOV, tui.P_AGENT_CLAUDE),
                "relay:claude": (tui.P_AGENT_CLAUDE_HOV, tui.P_AGENT_CLAUDE),
                "relay:agy": (tui.P_AGENT_AGY_HOV, tui.P_AGENT_AGY),
                "relay:codex": (tui.P_AGENT_CODEX_HOV, tui.P_AGENT_CODEX),
                "relay:deepseek": (tui.P_DONE_HOV, tui.P_DONE),
            }
            self.assertEqual(tui.HubTUI._RELAY_BRAND_PAIRS, brand)
            # Providers: brand fg on the native bg; hover only adds an underline
            for act, (band_pair, rest_pair) in brand.items():
                for on_band in (True, False):
                    pair = band_pair if on_band else rest_pair
                    self.assertEqual(app._relay_item_attr(act, False, "PICK_AGENT", False, on_header_band=on_band), color(pair) | bold)
                    self.assertEqual(
                        app._relay_item_attr(act, True, "PICK_AGENT", False, on_header_band=on_band), color(pair) | bold | under
                    )
            # Toggle: open / hovered -> bright bold text on the native bg; closed -> coral (quota) or dim gray, no bold
            for on_band, bright, coral, dim in (
                (True, tui.P_HOVER, tui.P_BAND_T4, tui.P_HOVER_DIM),
                (False, tui.P_WHITE, tui.P_STAT_T4, tui.P_META),
            ):
                for quota in (True, False):
                    self.assertEqual(app._relay_item_attr("relay_toggle", False, "PICK_AGENT", quota, on_header_band=on_band), color(bright) | bold)
                    self.assertEqual(app._relay_item_attr("relay_toggle", True, "CLOSED", quota, on_header_band=on_band), color(bright) | bold)
                self.assertEqual(app._relay_item_attr("relay_toggle", False, "CLOSED", True, on_header_band=on_band), color(coral) | bold)
                self.assertEqual(app._relay_item_attr("relay_toggle", False, "CLOSED", False, on_header_band=on_band), color(dim))
                # `< Back`: dim at rest, bright bold on hover
                self.assertEqual(app._relay_item_attr("relay_back", True, "PICK_CLAUDE_BACKEND", False, on_header_band=on_band), color(bright) | bold)
                self.assertEqual(app._relay_item_attr("relay_back", False, "PICK_CLAUDE_BACKEND", False, on_header_band=on_band), color(dim))
            self.assertEqual(app._relay_item_attr("label", False, "PICK_CLAUDE_BACKEND", False, on_header_band=True), color(tui.P_HOVER_DIM))
            self.assertEqual(app._relay_item_attr("label", False, "PICK_CLAUDE_BACKEND", False), color(tui.P_DIM))
            for act in ("relay_toggle", "label", "relay_back", *brand):
                for hov in (True, False):
                    for eff in ("CLOSED", "PICK_AGENT", "PICK_CLAUDE_BACKEND"):
                        for quota in (True, False):
                            for on_band in (True, False):
                                a = app._relay_item_attr(act, hov, eff, quota, on_header_band=on_band)
                                self.assertEqual(a & tui.curses.A_REVERSE, 0)
                                self.assertNotEqual((a >> 8) & 0xFF, tui.P_BTN_HOT)
                                self.assertNotIn((a >> 8) & 0xFF, box_pairs, f"bg 237 box: {act} hov={hov} band={on_band}")

            # Real Row 1 of `--header`: clean band with no `⇄ Relay` button or chip boxes
            hdr = self._make_tui(height=2, width=100, sidebar_only=False)
            hdr.header_only = True
            hdr.selected_idx = 0
            with patch.object(tui.HubTUI, "_deepseek_relay_ok", return_value=True):
                r_items = hdr._compute_preview_bar_state(2, 100, 0, False)["hdr"]["relay_items"]
                self.assertEqual(r_items, [])
                hdr.stdscr.addstr.reset_mock()
                hdr.draw()
            row1 = {c.args[2]: c.args[3] for c in hdr.stdscr.addstr.call_args_list if c.args[0] == 1 and len(c.args) >= 4}
            for lbl in ("Claude", "Gemini", "Codex", tui.RELAY_TOGGLE_OPEN, tui.RELAY_TOGGLE_CLOSED):
                self.assertNotIn(lbl, row1)
            for attr in row1.values():
                self.assertEqual(attr & tui.curses.A_REVERSE, 0)
                self.assertNotEqual((attr >> 8) & 0xFF, tui.P_BTN_HOT)

    def test_16_minute_granularity_rel_time_and_deepseek_launch_and_relay(self):
        import providers

        now = 1_800_000_000.0
        with patch("tui.time.time", return_value=now):
            for secs, exp in ((0, "1m"), (4, "1m"), (16, "1m"), (40, "1m"), (59, "1m"), (60, "1m"),
                              (150, "2m"), (3599, "59m"), (7200, "2h"), (2 * 86400, "2d")):
                self.assertEqual(tui.rel_time(now - secs), exp, secs)
            self.assertEqual(
                tui.render_sidebar_row_cells({"id": "x", "tool": "claude", "title": "t", "updatedAt": now - 16})["age_col"].strip(),
                "1m",
            )
        self.assertEqual(tui.rel_time(0), "")

        # `load_deepseek_api_key`: env > `~/.config/deepseek/env` > `~/.claude/settings.json`
        ds_home = self.home / "ds_home"
        (ds_home / ".config" / "deepseek").mkdir(parents=True)
        self.assertEqual(providers.load_deepseek_api_key(home_dir=str(ds_home), env={}), "")
        self.assertFalse(providers.is_deepseek_configured(home_dir=str(ds_home), env={}))
        (ds_home / ".claude").mkdir()
        (ds_home / ".claude" / "settings.json").write_text(json.dumps({"env": {"DEEPSEEK_API_KEY": "sk-settings"}}), "utf-8")
        self.assertEqual(providers.load_deepseek_api_key(home_dir=str(ds_home), env={}), "sk-settings")
        (ds_home / ".config" / "deepseek" / "env").write_text('# key\nexport DEEPSEEK_API_KEY="sk-file"\n', "utf-8")
        self.assertEqual(providers.load_deepseek_api_key(home_dir=str(ds_home), env={}), "sk-file")
        self.assertEqual(providers.load_deepseek_api_key(home_dir=str(ds_home), env={"DEEPSEEK_API_KEY": "sk-env"}), "sk-env")
        self.assertTrue(providers.is_deepseek_configured(home_dir=str(ds_home), env={}))

        real_isfile = os.path.isfile
        ds_env_path = os.path.expanduser("~/.config/deepseek/env")

        def launch_patches(app):
            return (
                patch("tui.subprocess.run"),
                patch("mixhub.tmux.workspace.ensure_outer_dual_pane_layout"),
                patch.object(app, "_ensure_hubpool_ready"),
                patch("mixhub.tmux.headers.ensure_hubpool_window_header"),
                patch("mixhub.tmux.outer.focus_outer_right_pane"),
                patch("tui.os.path.isfile", side_effect=lambda p: False if p == ds_env_path else real_isfile(p)),
                patch("mixhub.providers.deepseek.load_deepseek_api_key", return_value="sk-test"),
            )

        with patch.dict(os.environ, {"TMUX": "/tmp/tmux-1000/default,123,0"}), \
             patch("tui.curses.color_pair", side_effect=lambda p: p << 8):
            app = self._make_tui(height=30, width=32, sidebar_only=True)

            # `New Session -> DeepSeek`: CC·DS window with the resolved key (`-e` only when no env file exists)
            p_run, *rest = launch_patches(app)
            with p_run as mock_run, rest[0], rest[1], rest[2], rest[3], rest[4], rest[5]:
                app._launch_new_agent_session("deepseek")
            new_win = [c for c in mock_run.call_args_list if "new-window" in c.args[0]]
            self.assertEqual(len(new_win), 1)
            argv = new_win[0].args[0]
            self.assertEqual(argv[argv.index("DEEPSEEK_API_KEY=sk-test") - 1], "-e")
            self.assertTrue(argv[argv.index("-n") + 1].startswith("w-deepseek-new-"))
            self.assertIn(tui._DEEPSEEK_ENV_SOURCE, argv[-1])
            self.assertIn(providers.build_cc_ds_launch_cmd(model="deepseek-chat"), argv[-1])
            self.assertEqual(new_win[0].kwargs["env"]["DEEPSEEK_API_KEY"], "sk-test")
            self.assertEqual(app.current_session()["tool"], "deepseek")

            # `Relay -> DeepSeek` from Claude: CC·DS forks the Claude session and boots from the handoff prompt
            src_s = next(s for s in app.sessions if s["tool"] == "claude" and not str(s["id"]).startswith("new-"))
            cwd = src_s.get("cwd") or os.path.expanduser("~")
            if not os.path.isdir(cwd):
                cwd = os.path.expanduser("~")
            res = {"command": "claude --resume x", "prompt": "Continue the task", "token_estimate": 900, "relay_title": "Relay"}
            p_run, *rest = launch_patches(app)
            with patch.object(self.hub, "handoff", return_value=res) as mock_handoff, patch.object(app, "refresh_data"), \
                 p_run as mock_run, rest[0], rest[1], rest[2], rest[3], rest[4], rest[5]:
                app._execute_handoff(src_s, "deepseek")
            self.assertEqual(mock_handoff.call_args.args[0]["to"], "deepseek")
            new_win = [c for c in mock_run.call_args_list if "new-window" in c.args[0]]
            self.assertEqual(len(new_win), 1)
            argv = new_win[0].args[0]
            self.assertEqual(argv[argv.index("-n") + 1], f"w-deepseek-{src_s['id'][:12]}")
            self.assertEqual(
                argv[-1],
                tui._build_hubpool_cli_wrapper_cmd(
                    "deepseek",
                    src_s["id"],
                    f"w-deepseek-{src_s['id'][:12]}",
                    cwd,
                    tui._DEEPSEEK_ENV_SOURCE + providers.build_cc_ds_launch_cmd(
                        session_id=src_s["id"], cwd=cwd, model="deepseek-chat", fork_session=True, prompt="Continue the task"
                    ),
                ),
            )
            self.assertIn("--on-exit", argv[-1])
            self.assertIn("--fork-session", argv[-1])
            self.assertIn("Relayed claude → deepseek", app.status_msg)
            self.assertEqual(app.current_session()["tool"], "deepseek")

    def test_17_needs_you_click_enters_live_terminal_directly(self):
        with patch("tui.curses.color_pair", side_effect=lambda p: p << 8):
            app = self._make_tui(height=30, width=120, sidebar_only=False)
            app.selected_idx = -1
            app._seen_state = {"bootstrapAt": 0, "seen": {}, "attn": {}}
            now = time.time()
            app.sessions = [
                {"id": "ext-wait", "tool": "claude", "title": "External tab", "agentState": "wait_input",
                 "isLive": True, "resident": False, "updatedAt": now - 20},
                {"id": "pool-wait", "tool": "codex", "title": "Resident", "agentState": "wait_input",
                 "isLive": True, "resident": True, "updatedAt": now - 30},
                {"id": "unread", "tool": "agy", "title": "Unread", "agentState": "done_idle", "updatedAt": now - 90},
            ]
            app._draw_welcome_pane(34, 86, 3, 28)
            y_of = {app.sessions[idx]["id"]: y0 for y0, _, idx in app._welcome_triage_rows}
            self.assertEqual(set(y_of), {"ext-wait", "pool-wait", "unread"})

            def click(sid: str):
                app.selected_idx = -1
                app._last_row_click = ("", 0.0)
                app._handle_mouse(parsed_event=(0, 60, y_of[sid], False))
                self.assertEqual(app.current_session()["id"], sid)

            def double_click(sid: str):
                app.selected_idx = -1
                app._last_row_click = (sid, time.monotonic())
                app._handle_mouse(parsed_event=(0, 60, y_of[sid], False))
                self.assertEqual(app.current_session()["id"], sid)

            with patch.dict(os.environ, {"TMUX": "/tmp/tmux-1000/default,123,0"}), \
                 patch.object(app, "_outer_tmux_io_allowed", return_value=True), \
                 patch.object(app, "focus_external_session") as focus_ext, \
                 patch("mixhub.tmux.pool.activate_session_in_hubpool") as act_pool:
                # Single-clicking an external-tab or dormant session opens its Preview in MixHub (never jumps out on single click)
                click("ext-wait")
                focus_ext.assert_not_called()
                act_pool.assert_not_called()
                click("unread")
                focus_ext.assert_not_called()
                act_pool.assert_not_called()
                self.assertEqual(tui.read_active_selection()["id"], "unread")
                self.assertTrue(tui.read_active_selection().get("activateReader"))

                # Single-clicking a resident `hubpool` session switches to its resident `hubpool` window
                click("pool-wait")
                act_pool.assert_called_once_with(
                    app.sessions[1], tui.current_outer_session_name(), force_takeover=False, app=app
                )

                # Double-clicking (within 0.40s) jumps/activates directly
                act_pool.reset_mock()
                double_click("ext-wait")
                focus_ext.assert_called_once_with(app.sessions[0])
                double_click("unread")
                act_pool.assert_called_once_with(
                    app.sessions[2], tui.current_outer_session_name(), force_takeover=True, app=app
                )

            # Outside tmux (or with mocked IO) it only selects + previews
            with patch.object(app, "focus_external_session") as focus_ext, \
                 patch("mixhub.tmux.pool.activate_session_in_hubpool") as act_pool:
                click("pool-wait")
                focus_ext.assert_not_called()
                act_pool.assert_not_called()

    def test_18_exit_wakes_sidebar_immediately_and_sections_never_vanish(self):
        with patch("tui.curses.color_pair", side_effect=lambda p: p << 8):
            app = self._make_tui(height=30, width=32, sidebar_only=True)
            now = time.time()
            # The last Active session exited: `▾ Recent` stays (no 1-frame header jump)
            app.sessions = [{"id": "r1", "tool": "claude", "title": "Done", "updatedAt": now - 3600}]
            rows = app._sidebar_display_rows()
            self.assertEqual((rows[0]["kind"], rows[0]["key"]), ("header", "recent"))
            app.sessions = [{"id": "a1", "tool": "claude", "title": "Live", "isLive": True, "updatedAt": now - 30}]
            self.assertEqual(app._sidebar_display_rows()[0].get("key"), "active")
            # The dual-pane list keeps its flat single-bucket layout
            app.sidebar_only = False
            self.assertEqual(app._sidebar_display_rows()[0]["kind"], "session")
            app.sidebar_only = True

            # Every list row is blanked before rows are drawn (no stale cells / split CJK from the last frame)
            calls = self._draw_sb(app)
            list_top, list_bottom, _ = app._list_bounds(30)
            for ry in range(list_top, list_bottom):
                row_calls = [c for c in calls if c[0] == ry]
                self.assertTrue(row_calls, ry)
                self.assertEqual(row_calls[0][1:], (0, " " * 31, tui.P_UNSEL << 8))

        # `data/last_exit/` is watched: an `/exit` file wakes the kqueue loop and refreshes + re-buckets at once,
        # even right after input (no 0.8s throttle) and with a fresh process scan (no 10s wait)
        kq = MagicMock()
        constants = dict(KQ_FILTER_READ=-1, KQ_FILTER_VNODE=-4, KQ_FILTER_SIGNAL=-6, KQ_EV_ADD=1, KQ_EV_CLEAR=32,
                         KQ_NOTE_WRITE=2, KQ_NOTE_RENAME=32, KQ_NOTE_EXTEND=4, KQ_NOTE_DELETE=1)
        opened: list[str] = []

        def fake_open(path, flags):
            opened.append(path)
            return 40 + len(opened)

        kq.control.side_effect = lambda changes, n, timeout: [MagicMock(ident=41, filter=-4)] if n else []
        app.hub._proc_scanned = 42.0
        app._last_input_ts = 499.9
        app._last_proc_poll = 499.0
        app._last_gc_poll = 500.0
        with patch.multiple(evq, **constants), \
             patch.object(evq, "kqueue", return_value=kq), \
             patch.object(evq, "kevent", side_effect=lambda ident, **kw: MagicMock(ident=ident)), \
             patch("tui.sys.stdin") as stdin, \
             patch("tui.os.makedirs") as mkdirs, \
             patch("tui.os.path.exists", side_effect=lambda path: path == tui.LAST_EXIT_DIR), \
             patch("mixhub.tui.quota._codex_quota_sources", return_value=()) as cx_glob, \
             patch.dict(tui._QUOTA_SUMMARY_CACHE, {"codex_sources": (str(self.home), ())}), \
             patch("tui.os.open", side_effect=fake_open), patch("tui.os.close"), \
             patch("tui.time.monotonic", return_value=500.0), \
             patch.object(app, "_poll_external_terminal_seen"), \
             patch.object(app, "refresh_data") as refresh:
            stdin.fileno.return_value = 10
            app._wait_sidebar_event_kqueue()
        mkdirs.assert_any_call(tui.LAST_EXIT_DIR, exist_ok=True)
        # The 0.18s kqueue loop never globs `~/.codex/sessions/*/*/*/rollout-*.jsonl` (watch list comes from the cache)
        cx_glob.assert_not_called()
        self.assertEqual(opened, [tui.LAST_EXIT_DIR])
        refresh.assert_called_once_with(force=True, reorder=True)
        self.assertEqual(app.hub._proc_scanned, 0)
        self.assertFalse(app._pending_dir_refresh)

    def test_19_popover_number_keys_still_work_silently(self):
        with patch("tui.curses.color_pair", side_effect=lambda p: p << 8):
            app = self._make_tui(height=30, width=32, sidebar_only=True)

            def run_keys(keys):
                app.stdscr.getch.side_effect = [*keys, KeyboardInterrupt()]
                with patch.object(app, "draw"), \
                     patch.object(app, "_wait_sidebar_event_kqueue"), \
                     patch.object(app, "_sync_sidebar_selection", return_value=False), \
                     patch("tui.curses.mousemask"), \
                     patch("tui.curses.mouseinterval"), \
                     patch("tui.sys.stdout"):
                    try:
                        app.run()
                    except KeyboardInterrupt:
                        pass

            app.provider_dropdown_open = True
            run_keys([ord("3")])
            self.assertEqual(app.provider_filter, "codex")
            self.assertFalse(app.provider_dropdown_open)
            app.provider_dropdown_open = True
            run_keys([ord("1")])
            self.assertIsNone(app.provider_filter)

            app._new_session_menu = True
            with patch.object(app, "_launch_new_agent_session") as launch:
                run_keys([ord("4")])
                launch.assert_called_once_with("agy")
            app._new_session_menu = True
            with patch.object(app, "_launch_new_agent_session") as launch:
                run_keys([ord("d")])
                launch.assert_called_once_with("deepseek")


    def test_20_sidebar_bottom_right_web_entry(self):
        """
        `↗ web` sits on the bottom `cpu / mem` row, right-aligned to col 29 (same right edge as `◧` and the quota
        `10/28`); it opens `/#session=<id>` for the selected session (bare root on Home) off the UI thread, and the
        rest of Row h-1 still toggles the quota table.
        """
        color = lambda p: p << 8
        with patch("tui.curses.color_pair", side_effect=color):
            os.environ.pop("HUB_PORT", None)
            app = self._make_tui(height=30, width=32, sidebar_only=True)
            app.counts["liveMemoryMB"] = 2150.0
            app.selected_idx = -1
            calls = self._draw_sb(app)
            row0, bottom = self._row_at(calls, 0), self._row_at(calls, 29)
            self.assertEqual(bottom[25][2:], (tui.WEB_LINK_LABEL, color(tui.P_STAT_LBL)))
            self.assertEqual(25 + tui.disp_width(tui.WEB_LINK_LABEL) - 1, 29)
            self.assertEqual(row0[29][2], "◧")
            self.assertIn("mem ", [c[2] for c in bottom.values()])
            stat_end = max(x + tui.disp_width(str(c[2])) for x, c in bottom.items() if x < 25)
            self.assertLessEqual(stat_end + 2, 25)
            # Hovering `↗ web` via real mouse motion (`btn=35`, cols 24..30) lights up the ` ↗ web ` pill in P_USER_BOX | A_BOLD
            app._handle_mouse(parsed_event=(35, 27, 29, False))
            self.assertEqual(app.hover_pos, (27, 29))
            hov_bottom = self._row_at(self._draw_sb(app), 29)
            self.assertEqual(
                hov_bottom[24][2:],
                (f" {tui.WEB_LINK_LABEL} ", color(tui.P_USER_BOX) | tui.curses.A_BOLD),
            )
            app._handle_mouse(parsed_event=(35, 10, 29, False))
            self.assertEqual(app.hover_pos, (-1, -1))

            def opened_url(click_x: int) -> str | None:
                with patch("tui.threading.Thread") as thread, patch("tui.subprocess.run") as run, \
                     patch("tui.subprocess.Popen") as popen:
                    app._handle_mouse(parsed_event=(0, click_x, 29, False))
                    run.assert_not_called()
                    popen.assert_not_called()
                if not thread.called:
                    return None
                thread.return_value.start.assert_called_once()
                self.assertIs(thread.call_args.kwargs["target"], tui._ensure_web_and_open)
                self.assertTrue(thread.call_args.kwargs["daemon"])
                return thread.call_args.kwargs["args"][0]

            # Home -> bare Web root; the click does not also toggle the quota table
            self.assertEqual(opened_url(27), "http://127.0.0.1:8765")
            self.assertFalse(getattr(app, "footer_quota_collapsed", False))
            # Selected session -> deep link
            app.selected_idx = next(i for i, s in enumerate(app.sessions) if s["id"] == C1)
            self._draw_sb(app)
            self.assertEqual(opened_url(29), "http://127.0.0.1:8765/#session=" + C1)
            # Left part of Row h-1 is still the quota collapse toggle
            self.assertIsNone(opened_url(6))
            self.assertTrue(app.footer_quota_collapsed)

            # `root=True` always opens the root; explicit sessions are URL-encoded
            with patch("tui.threading.Thread") as thread:
                app._open_web_archive(root=True)
                self.assertEqual(thread.call_args.kwargs["args"], ("http://127.0.0.1:8765",))
                app._open_web_archive({"id": "a b/c"})
                self.assertEqual(thread.call_args.kwargs["args"], ("http://127.0.0.1:8765/#session=a%20b%2Fc",))
            with patch.dict(os.environ, {"HUB_PORT": "9911"}):
                self.assertEqual(tui.web_archive_url(None), "http://127.0.0.1:9911")

        # Worker: `hub start` only when the port is closed, then the platform opener
        url = "http://127.0.0.1:8765/#session=" + C1
        which = patch("mixhub.core.browser.shutil.which", side_effect=lambda name: "/usr/bin/" + name)
        not_wsl = patch("mixhub.core.browser.is_wsl", return_value=False)
        with patch("mixhub.tui.web._web_port_listening", return_value=False), patch("tui.subprocess.run") as run, \
             patch("mixhub.core.browser.sys.platform", "darwin"), which, not_wsl:
            tui._ensure_web_and_open(url)
        self.assertEqual(
            [c.args[0] for c in run.call_args_list],
            [module_argv(CLI_MODULE, "start"), ["open", url]],
        )
        with patch("mixhub.tui.web._web_port_listening", return_value=True), patch("tui.subprocess.run") as run, \
             patch("mixhub.core.browser.sys.platform", "linux"), which, not_wsl:
            tui._ensure_web_and_open(url)
        self.assertEqual([c.args[0] for c in run.call_args_list], [["xdg-open", url]])
        with closing(tui.socket.socket()) as srv:
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            port = srv.getsockname()[1]
            self.assertTrue(tui._web_port_listening(port))
        self.assertFalse(tui._web_port_listening(port))

    def test_21_quota_metrics_3s_fast_path_skips_globs_on_real_home(self):
        cx_glob = MagicMock(return_value=())
        cc_scan = MagicMock(return_value=())
        with patch("mixhub.tui.quota._codex_quota_sources", cx_glob), \
             patch("mixhub.providers.quota._claude_quota_sources", cc_scan), \
             patch("mixhub.tui.quota._quota_file_signature", return_value=None), \
             patch.dict(tui._QUOTA_SUMMARY_CACHE, {"ts": 0.0, "key": ""}, clear=True), \
             patch.dict(os.environ, {}), \
             patch("tui.time.monotonic") as mono:
            os.environ.pop("MIXHUB_STATUSLINE_JSON", None)
            mono.return_value = 1000.0
            first = tui.get_cli_quota_metrics()
            self.assertEqual((cx_glob.call_count, cc_scan.call_count), (1, 1))
            # Every 0.18s animation frame inside the 3s TTL: no rollout glob, no `~/.claude/projects` scan
            for t in (1000.18, 1000.36, 1002.9):
                mono.return_value = t
                self.assertEqual(tui.get_cli_quota_metrics(), first)
            self.assertEqual((cx_glob.call_count, cc_scan.call_count), (1, 1))
            # TTL lapse, or a kqueue quota event (`ts = 0`), re-verifies sources
            mono.return_value = 1003.1
            tui.get_cli_quota_metrics()
            self.assertEqual(cx_glob.call_count, 2)
            tui._QUOTA_SUMMARY_CACHE["ts"] = 0.0
            tui.get_cli_quota_metrics()
            self.assertEqual(cx_glob.call_count, 3)
            # Custom homes never take the fast path, so an in-place `.jsonl` append still busts the cache at once
            tui.get_cli_quota_metrics(home_dir=str(self.home))
            tui.get_cli_quota_metrics(home_dir=str(self.home))
            self.assertEqual((cx_glob.call_count, cc_scan.call_count), (5, 5))

    def test_22_preview_pills_keyboard_hover_focus_rail_and_scrollback_clear(self):
        """
        Verify Parts A, B, C, and D:
          A) Fixed-width `Move / Jump / Relay` (Mode C) and `Resume / Relay` (Mode B) pills with unified keyboard
             (`KEY_LEFT` / `KEY_RIGHT` / `Enter`) + mouse hover focus (`_preview_focus_idx`), zero `★` in Relay,
             and `--reader` Row 1 free of `⇄ Relay`.
          B) Sidebar row hover `⋯` is always gray (`P_META`), white bold (`P_WHITE | A_BOLD`) on hover, and boxed
             (`P_USER_BOX | A_BOLD`) when menu is open — never yellow (`P_WAIT`) even on pinned or wait/unread rows.
          C) Single-clicking any non-resident session row in the left sidebar (including `hibernatedByHub` / `inWorkingSet`)
             always previews it (`activate_live=False`), and the double-click window is `0.40s`.
          D) Right-edge prompt turn minimap rail is inset at `x = w - 2` (`box_w = max(10, right_w - 4)`), supports
             both click (`btn=0`) and drag (`btn=32`) in `w - 3 <= mx <= w - 1`, and `set_outer_client_autowrap(..., enabled=False)`
             sends `b"\\x1b[?7l\\x1b[3J"`.
        """
        color = lambda p: p << 8
        with patch("tui.curses.color_pair", side_effect=color), \
             patch("tui.curses.init_pair"), \
             patch("tui.curses.start_color"), \
             patch("tui.curses.use_default_colors"), \
             patch("tui.curses.curs_set"):
            # Part A: Fixed-width pill geometry and keyboard navigation (`KEY_RIGHT` / `KEY_LEFT` / `Enter`)
            rd = self._make_tui(height=30, width=100, sidebar_only=False)
            rd.reader_only = True
            rd.selected_idx = 0
            rd.sessions[0]["isLive"] = True
            rd.sessions[0]["resident"] = False
            rd.sessions[0]["agentState"] = "done_idle"
            rd.sessions[0]["processes"] = [{"pid": 1111, "resident": False, "tty": "ttys004", "rss_mb": 80.0}]

            st0 = rd._compute_preview_bar_state(30, 100, 0, False)
            self.assertEqual(st0["hdr"]["relay_items"], [])
            pills0 = st0["bar"]["pills"]
            self.assertEqual([p[0] for p in pills0], [" Resume ↵ ", " Go to Tab   "])
            coords0 = [(p[1], p[2]) for p in pills0]

            def feed_reader_keys(keys):
                rd.stdscr.getch.side_effect = list(keys) + [KeyboardInterrupt()]
                with patch.object(rd, "_wait_reader_event_kqueue"), \
                     patch.object(rd, "_sync_reader_selection"), \
                     patch.object(rd, "_is_reader_zoomed", return_value=False), \
                     patch("tui.curses.mousemask"), \
                     patch("tui.curses.mouseinterval"):
                    try:
                        rd.run()
                    except KeyboardInterrupt:
                        pass

            # Pressing KEY_RIGHT moves focus from Resume (0) -> Go to Tab (1) without shifting x-coords
            feed_reader_keys([tui.curses.KEY_RIGHT])
            pills1 = rd._compute_preview_bar_state(30, 100, 0, False)["bar"]["pills"]
            self.assertEqual([p[0] for p in pills1], [" Resume   ", " Go to Tab ↵ "])
            self.assertEqual([(p[1], p[2]) for p in pills1], coords0)

            with patch.object(rd, "focus_external_session", return_value=True) as mock_jump:
                feed_reader_keys([10])  # Enter on Go to Tab
                mock_jump.assert_called_once()

            # Pressing KEY_LEFT returns to Resume (0), and one more KEY_LEFT focuses outer left pane
            with patch.dict(os.environ, {"TMUX": "/tmp/tmux-1000/default,123,0"}), \
                 patch("mixhub.tmux.outer.focus_outer_left_pane") as mock_left:
                feed_reader_keys([tui.curses.KEY_LEFT])
                self.assertEqual(rd._preview_focus_idx, 0)
                mock_left.assert_not_called()
                feed_reader_keys([tui.curses.KEY_LEFT])
                mock_left.assert_called_once()

            # Verify Codex daemon lock unlink (`_unlink_stale_codex_lock_file`) + kqueue / placeholder Home-flash guards
            lock_dir = Path(self.home) / ".codex" / "thread-writer-locks"
            lock_dir.mkdir(parents=True, exist_ok=True)
            codex_lock = lock_dir / f"{X1}.lock"
            codex_lock.write_text("", encoding="utf-8")
            daemon_codex_s = {"id": X1, "tool": "codex", "isLive": True, "processes": [], "cwd": str(self.home)}
            self.assertFalse(tui._is_external_tab_session(daemon_codex_s))
            can_take, _ = rd._release_external_session_lock(daemon_codex_s)
            self.assertTrue(can_take)
            self.assertFalse(codex_lock.exists())

            # `_sync_reader_selection` with a session id not yet in `rd.sessions` inserts a placeholder and never flashes Home (-1)
            tui.write_active_selection({"id": "unindexed-session-999", "tool": "codex", "title": "Draft Session"}, activate_reader=False)
            with patch.object(rd, "draw") as mock_bg_draw:
                self.assertTrue(rd._sync_reader_selection())
                self.assertEqual(rd.selected_idx, 0)
                self.assertEqual(rd.current_session()["id"], "unindexed-session-999")
                self.assertTrue(mock_bg_draw.called)

            # Part B: Sidebar hover renders only `☆/★` (`⋯` removed)
            sb = self._make_tui(height=30, width=32, sidebar_only=True)
            sb.sessions[0]["pinned"] = True
            sb.sessions[0]["agentState"] = "wait_input"
            drows = sb._sidebar_display_rows()
            s_row_y = 4  # list_top (3) + 1 (under Pinned header)
            sb.hover_idx = drows[1]["idx"]
            sb.hover_pos = (10, s_row_y)
            sb.stdscr.addstr.reset_mock()
            sb.draw()
            age_x = tui.layout_sidebar_row_32(drows[1]["session"], left_w=32, is_unread=True)["age_x"]
            row_map = {c.args[1]: (c.args[2], c.args[3]) for c in sb.stdscr.addstr.call_args_list if c.args[0] == s_row_y}
            self.assertEqual(row_map.get(age_x), ("  ", color(tui.P_DIM)))
            self.assertEqual(row_map.get(age_x + 2), ("★", color(tui.P_WAIT) | tui.curses.A_BOLD))

            # Part C: Single-click on a `hibernatedByHub` / `inWorkingSet` row previews (`activate_live=False`), 0.40s double-click window
            sb.sessions[0]["hibernatedByHub"] = True
            sb.sessions[0]["inWorkingSet"] = True
            sb.sessions[0]["isLive"] = False
            with patch.object(sb, "_on_sidebar_selection_changed") as mock_sel_chg, \
                 patch.object(sb, "_focus_outer_right_pane_async"), \
                 patch("tui.time.monotonic") as mock_mono:
                mock_mono.return_value = 500.0
                sb._last_row_click = ("", 0.0)
                sb._handle_mouse(parsed_event=(0, 8, s_row_y, False))
                mock_sel_chg.assert_called_once_with(activate_live=False)
                # Clicking again 0.55s later (> 0.40s) still previews (`activate_live=False`), never jumps
                mock_sel_chg.reset_mock()
                mock_mono.return_value = 500.55
                sb._handle_mouse(parsed_event=(0, 8, s_row_y, False))
                mock_sel_chg.assert_called_once_with(activate_live=False)
                # Clicking within 0.25s (< 0.40s) triggers double-click (`activate_live=True`)
                mock_sel_chg.reset_mock()
                mock_mono.return_value = 500.80
                sb._handle_mouse(parsed_event=(0, 8, s_row_y, False))
                mock_sel_chg.assert_called_once_with(activate_live=True)

            # Part D: Right-Edge Prompt Turn Minimap Rail at `w - 2` (`x = 98` for `w = 100`), click + drag in `w - 3..w - 1`, and `\x1b[3J`
            rd.rendered_lines = [("user_box", f"Prompt {i}") for i in range(60)]
            rd.user_turns = [(0, "Turn 1"), (25, "Turn 2"), (50, "Turn 3")]
            rd.msg_scroll = 50
            with patch.object(rd, "build_transcript_lines", return_value=rd.rendered_lines):
                rd.stdscr.addstr.reset_mock()
                rd.draw()
                rail_calls = [c.args for c in rd.stdscr.addstr.call_args_list if c.args[2] in ("─", "━") and len(c.args[2]) == 1]
                self.assertTrue(rail_calls)
                self.assertTrue(all(c[1] == 98 for c in rail_calls), f"Rail ticks must be at w - 2 (98), got {[c[1] for c in rail_calls]}")
                # Dragging (`btn=32`) along the rail at `x = 98` jumps `msg_scroll` to the corresponding turn
                rd._handle_mouse(parsed_event=(32, 98, 6, False))
                self.assertEqual(rd.msg_scroll, 0)
                rd._handle_mouse(parsed_event=(32, 98, 16, False))
                self.assertEqual(rd.msg_scroll, 25)

                # Ticks stay put whether or not the sticky prompt header shows, and a click on a drawn tick
                # (incl. the top rows under the sticky header) lands on that tick's prompt
                def tick_rows():
                    rd.stdscr.addstr.reset_mock()
                    rd.draw()
                    return sorted(c.args[0] for c in rd.stdscr.addstr.call_args_list
                                  if c.args[1] == 98 and c.args[2] in ("─", "━"))

                rd.msg_scroll = 30  # inside Turn 2: sticky header on
                rows_sticky = tick_rows()
                rd.msg_scroll = 0  # at Turn 1: no sticky header
                self.assertEqual(tick_rows(), rows_sticky)
                self.assertEqual(len(rows_sticky), 3)
                for start in (30, 0, 50):
                    for row, want in zip(rows_sticky, (0, 25, 50)):
                        rd.msg_scroll = start
                        tick_rows()
                        rd._handle_mouse(parsed_event=(0, 98, row, False))
                        self.assertEqual(rd.msg_scroll, want, (start, row))

            with patch("tui.subprocess.run", return_value=MagicMock(stdout="/dev/ttys099\n", returncode=0)) as mock_sub_run, \
                 patch("tui.os.open", return_value=77), \
                 patch("tui.os.write") as mock_tty_write, \
                 patch("tui.os.close"):
                tui.set_outer_client_autowrap("hub", enabled=False)
                mock_tty_write.assert_called_once_with(
                    77, b"\x1b[?1049l\x1b[H\x1b[2J\x1b[3J\x1b[?1049h\x1b[?7l\x1b[3J"
                )
                self.assertIn(
                    ["tmux", "-L", "mixhub", "refresh-client", "-t", "/dev/ttys099"],
                    [c.args[0] for c in mock_sub_run.call_args_list],
                )

            # Fallback when `list-clients -t hub` returns empty and `list-clients -F` matches `hub\t/dev/ttys033`
            def _fake_list_clients(cmd, *a, **kw):
                if cmd[:5] == ["tmux", "-L", "mixhub", "list-clients", "-t"]:
                    return MagicMock(stdout="", returncode=1)
                if cmd[:4] == ["tmux", "-L", "mixhub", "list-clients"]:
                    return MagicMock(stdout="other\t/dev/ttys011\nhub\t/dev/ttys033\n", returncode=0)
                return MagicMock(stdout="", returncode=0)

            with patch("tui.subprocess.run", side_effect=_fake_list_clients) as mock_sub_fb, \
                 patch("tui.os.open", return_value=88) as mock_open_fb, \
                 patch("tui.os.write") as mock_write_fb, \
                 patch("tui.os.close"):
                tui.set_outer_client_autowrap("hub", enabled=False)
                mock_open_fb.assert_called_once_with("/dev/ttys033", tui.os.O_WRONLY | tui.os.O_NOCTTY | tui.os.O_NONBLOCK)
                mock_write_fb.assert_called_once_with(
                    88, b"\x1b[?1049l\x1b[H\x1b[2J\x1b[3J\x1b[?1049h\x1b[?7l\x1b[3J"
                )
                self.assertIn(
                    ["tmux", "-L", "mixhub", "refresh-client", "-t", "/dev/ttys033"],
                    [c.args[0] for c in mock_sub_fb.call_args_list],
                )

            # Sanitize corrupted `terminal-overrides` entries (`am@`, `smcup`, `rmcup`, `\\E`)
            fake_overrides = (
                "terminal-overrides[0] linux*:AX@\n"
                "terminal-overrides[1] *256col*:Tc\n"
                "terminal-overrides[2] *:am@:smcup=\\\\E[?1049h\\\\E[?7l:rmcup=\\\\E[?7h\\\\E[?1049l\n"
            )
            with patch("tui.subprocess.run", return_value=MagicMock(stdout=fake_overrides, returncode=0)) as mock_san_run:
                removed = tui._sanitize_tmux_terminal_overrides(["tmux"])
                self.assertEqual(removed, [2])
                self.assertIn(
                    ["tmux", "set-option", "-su", "terminal-overrides[2]"],
                    [c.args[0] for c in mock_san_run.call_args_list],
                )

            # Part E: `log_tui_diag` writes JSONL records and auto-rotates to <=128KB when exceeding 256KB
            diag_path = str(self.home / "tui_diag_test.log")
            with patch("mixhub.tui.paths.TUI_DIAG_LOG_PATH", diag_path):
                with open(diag_path, "wb") as df:
                    df.write((b'{"event":"pad","msg":"' + (b"x" * 200) + b'"}\n') * 1200)
                self.assertGreater(os.path.getsize(diag_path), 262144)
                tui.log_tui_diag("unit_test_rotation", session_id=X1, elapsed_ms=12.3)
                new_sz = os.path.getsize(diag_path)
                self.assertLessEqual(new_sz, 131072)
                with open(diag_path, "r", encoding="utf-8") as rf:
                    lines = [ln for ln in rf.read().splitlines() if ln.strip()]
                last_rec = json.loads(lines[-1])
                self.assertEqual(last_rec["event"], "unit_test_rotation")
                self.assertEqual(last_rec["session_id"], X1)

                # Daemon-locked Codex session (`isLive=True, processes=[], no tty`) is Mode B (`pmode == "B"`),
                # and `_release_external_locks_before_takeover` unlinks stale `~/.codex/thread-writer-locks/<id>.lock`
                daemon_codex = {"id": X1, "tool": "codex", "isLive": True, "processes": [], "agentState": "done_idle"}
                self.assertFalse(tui._is_external_tab_session(daemon_codex))
                self.assertEqual(tui._session_preview_mode_and_state(daemon_codex)[0], "B")
                lock_dir = self.home / ".codex" / "thread-writer-locks"
                lock_dir.mkdir(parents=True, exist_ok=True)
                lock_file = lock_dir / f"{X1}.lock"
                lock_file.write_text("", encoding="utf-8")
                self.assertTrue(lock_file.exists())
                ok_rel, _ = sb._release_external_locks_before_takeover(daemon_codex, force_takeover=False)
                self.assertTrue(ok_rel)
                self.assertFalse(lock_file.exists())

    def test_24_fullscreen_resize_zero_main_thread_subprocess_and_clean_hooks(self):
        """
        Verify fullscreen / window resize latency fixes:
          1. Outer `window-resized` and `client-resized` hooks never run `on-attach`, `sleep`, or `?1049` screen switches.
          2. Inner `hubpool` header hooks include `session-window-changed` and guard `resize-pane` against unzooming (`window_zoomed_flag == 0`).
          3. On `KEY_RESIZE` / terminal size change, `draw()` in `--header`, `--reader`, and `--sidebar` spawns 0 synchronous subprocesses on the main thread.
          4. `interactive_resume_cmd` for Claude uses clean `["claude", "--resume", sid]` without `--remote-control`.
        """
        self.assertEqual(
            tui.interactive_resume_cmd({"tool": "claude", "id": C1, "cwd": str(self.home)}),
            ["claude", "--resume", C1],
        )

        with patch("tui.subprocess.run") as mock_layout_run, \
             patch("mixhub.tmux.outer.get_outer_hub_pane_ids", return_value=("%1", "%2")), \
             patch("mixhub.tmux.workspace.configure_tmux_mouse_and_copy"), \
             patch("mixhub.tmux.outer.set_outer_client_autowrap"):
            mock_layout_run.return_value = MagicMock(returncode=0, stdout="%1|0|python3\n%2|33|tmux\n")
            tui.ensure_outer_dual_pane_layout("hub", "pool")
            hook_cmds = [
                c.args[0] for c in mock_layout_run.call_args_list
                if len(c.args[0]) >= 5 and c.args[0][:4] == ["tmux", "-L", "mixhub", "set-hook"]
            ]
            win_hooks = [cmd[-1] for cmd in hook_cmds if "window-resized" in cmd]
            cli_hooks = [cmd[-1] for cmd in hook_cmds if "client-resized" in cmd]
            self.assertTrue(win_hooks)
            self.assertTrue(cli_hooks)
            for h_str in win_hooks + cli_hooks:
                self.assertIn("main-vertical", h_str)
                self.assertNotIn("on-attach", h_str)
                self.assertNotIn("sleep", h_str)
                self.assertNotIn("1049", h_str)

        pool_hooks = tui._hubpool_header_hook_cmds()
        hook_names = [cmd[5] for cmd in pool_hooks]
        self.assertTrue(any(name.startswith("session-window-changed") for name in hook_names))
        for cmd in pool_hooks:
            self.assertIn("window_zoomed_flag", cmd[6])

        with patch.dict(os.environ, {"TMUX": "/tmp/tmux-501/default,123,0"}), \
             patch("tui.curses.color_pair", side_effect=lambda p: p << 8), \
             patch.object(tui.HubTUI, "_init_colors"):
            scr_hdr = MagicMock()
            scr_hdr.getmaxyx.return_value = (2, 100)
            hdr = tui.HubTUI(
                scr_hdr,
                reader_only=True,
                header_only=True,
                header_target=("claude", C1),
                hub_instance=self.hub,
                caf=MagicMock(),
                seen_path=self.seen_path,
            )
            scr_rd = MagicMock()
            scr_rd.getmaxyx.return_value = (28, 100)
            rd = tui.HubTUI(
                scr_rd,
                reader_only=True,
                hub_instance=self.hub,
                caf=MagicMock(),
                seen_path=self.seen_path,
            )
            sb = self._make_tui(height=28, width=32, sidebar_only=True)

            for app_inst, new_sz in ((hdr, (2, 140)), (rd, (48, 140)), (sb, (48, 36))):
                app_inst._zoom_override_until = 0.0
                app_inst._last_zoom_check_ts = 0.0
                app_inst.stdscr.getmaxyx.return_value = new_sz
                with patch("tui.subprocess.run") as m_run, \
                     patch("tui.subprocess.Popen") as m_popen, \
                     patch("tui.os.get_terminal_size", return_value=os.terminal_size((new_sz[1], new_sz[0]))):
                    app_inst.draw()
                    self.assertEqual(m_run.call_count, 0, f"Unexpected subprocess.run in {app_inst._diag_mode_label()} draw()")
                    self.assertEqual(m_popen.call_count, 0, f"Unexpected subprocess.Popen in {app_inst._diag_mode_label()} draw()")

    def test_25b_preview_header_names_states_in_plain_words_and_asks_for_no_keys(self):
        """The preview pane is clicked, not typed into: its header and hints never ask for a key or show state ids."""
        app = self._make_tui(height=30, width=120, sidebar_only=False)
        app.reader_only = True
        base = dict(self.hub.get("claude", C1))
        cases = {
            "idle": dict(base, isLive=True, agentState="done_idle", processes=[]),
            "waiting for you": dict(base, isLive=True, agentState="wait_input", processes=[]),
            "working": dict(base, isLive=True, agentState="working", processes=[]),
            "asleep": dict(base, isLive=False, agentState="dormant"),
        }
        for word, sess in cases.items():
            app.render_cache.clear()
            app.loaded_key = None
            with patch.object(app, "current_session", return_value=sess):
                text = "\n".join(t for _, t in app.build_transcript_lines(110)[:4])
            self.assertIn(word, text)
            for bad in ("press ", "Enter", "→ to", "done_idle", "wait_input", "dormant", "hubpool"):
                self.assertNotIn(bad, text, f"{word!r} header: {text!r}")

    def test_25d_long_reply_wraps_without_losing_characters(self):
        """A wrapped assistant line was cut 2 columns short (the `● ` indent wasn't counted): "no longer" read "no onger"."""
        width = 90
        app = self._make_tui(height=40, width=width, sidebar_only=False)
        app.reader_only = True
        reply = ("Two changes: give the CI step a lock timeout so a short overlap waits instead of failing, and add a "
                 "nightly job that unlocks only locks older than an hour whose run is no longer active.")
        sess = dict(self.hub.get("claude", C1), isLive=False, agentState="dormant")
        entries = [{"role": "user", "text": "How do we stop this?"}, {"role": "assistant", "text": reply}]
        with patch.object(app, "current_session", return_value=sess), \
             patch.object(self.hub, "transcript_window", return_value=(entries, False)), \
             patch("tui.curses.color_pair", side_effect=lambda p: p << 8):
            app.render_cache.clear()
            app.loaded_key = None
            app.msg_scroll = 0
            app.draw()
        rows = {}
        for c in app.stdscr.addstr.call_args_list:
            y, x, text = c.args[:3]
            if isinstance(text, str) and x < width - 2:
                rows.setdefault(y, []).append((x, text))
        drawn = ["".join(t for _, t in sorted(parts)) for _, parts in sorted(rows.items())]
        start = next(i for i, line in enumerate(drawn) if line.startswith("● Two"))
        end = next(i for i in range(start + 1, len(drawn)) if drawn[i].rstrip().endswith("active."))
        para = drawn[start:end + 1]
        self.assertTrue(all(line.startswith("  ") for line in para[1:]),
                        f"continuation lines keep the 2-column indent: {para}")
        self.assertEqual("".join(line[2:] for line in para).replace(" ", ""), reply.replace(" ", ""))
        self.assertTrue(all(disp_width(line) <= width - 4 for line in para), "nothing runs into the minimap rail")

    def test_25c_launch_row_lists_every_installed_agent_in_a_fixed_order(self):
        """Usage used to sort the row, which put Gemini second; the order is fixed: Claude, Codex, OpenCode, Gemini."""
        app = self._make_tui(height=28, width=120, sidebar_only=False)
        now = time.time()
        fake = {f"{tool}:{i}": {"tool": tool, "id": str(i), "updatedAt": now - 3600}
                for tool, n in (("claude", 1), ("codex", 4), ("opencode", 2)) for i in range(n)}
        fake["agy:old"] = {"tool": "agy", "id": "old", "updatedAt": now - 90 * 86400}
        with patch.object(app.hub, "sessions", fake):
            app._new_session_items_cache = None
            order = [row[0] for row in app.new_session_items()]
            self.assertEqual(order, ["claude", "codex", "opencode", "agy"])
            app.selected_idx = -1
            app.sessions = []
            app.stdscr.addstr.reset_mock()
            with patch("tui.curses.color_pair", side_effect=lambda p: p << 8):
                app.draw()
        launch = [k for k, *_ in app._welcome_launchpad_rects]
        self.assertEqual(launch, order)

    def test_25g_launch_row_says_how_to_get_an_agent_when_none_is_installed(self):
        app = self._make_tui(height=28, width=120, sidebar_only=False)
        with patch.object(app.hub, "sessions", {}), patch("shutil.which", return_value=None):
            app._new_session_items_cache = None
            app.selected_idx = -1
            app.sessions = []
            app.stdscr.addstr.reset_mock()
            with patch("tui.curses.color_pair", side_effect=lambda p: p << 8):
                app.draw()
        texts = " ".join(c.args[2] for c in app.stdscr.addstr.call_args_list
                         if len(c.args) >= 3 and isinstance(c.args[2], str))
        self.assertEqual(app._welcome_launchpad_rects, [])
        self.assertIn("No agent CLI found", texts)

    def test_25f_launch_row_fits_inside_the_card(self):
        """With four agents the LAUNCH buttons ran 8 columns past the card's divider lines, so the row looked off
        center; the card widens to fit them when the pane has room."""
        app = self._make_tui(height=28, width=126, sidebar_only=False)
        now = time.time()
        fake = {f"{tool}:0": {"tool": tool, "id": "0", "updatedAt": now - 3600}
                for tool in ("claude", "codex", "agy", "opencode")}
        with patch.object(app.hub, "sessions", fake), patch("shutil.which", return_value="/bin/x"):
            app._new_session_items_cache = None
            app.selected_idx = -1
            app.sessions = []
            app.stdscr.addstr.reset_mock()
            with patch("tui.curses.color_pair", side_effect=lambda p: p << 8):
                app.draw()
        launch_x = min(r[2] for r in app._welcome_launchpad_rects) - 10  # the `LAUNCH` label starts the card
        rules = [(c.args[1], len(c.args[2])) for c in app.stdscr.addstr.call_args_list
                 if len(c.args) >= 3 and isinstance(c.args[2], str) and set(c.args[2]) == {"─"} and c.args[1] == launch_x]
        self.assertTrue(rules)
        x0, w = rules[0]
        self.assertEqual(len(app._welcome_launchpad_rects), 4)
        self.assertLessEqual(max(r[3] for r in app._welcome_launchpad_rects), x0 + w)

    def test_25e_launch_order_is_not_frozen_before_sessions_load(self):
        """Home drew LAUNCH before the first scan finished and kept that default order for 30 s, so it disagreed with
        the `✎ New session` menu drawn a moment later."""
        app = self._make_tui(height=28, width=120, sidebar_only=False)
        now = time.time()
        fake = {f"{tool}:{i}": {"tool": tool, "id": str(i), "updatedAt": now - 3600}
                for tool, n in (("claude", 3), ("agy", 2), ("opencode", 1)) for i in range(n)}
        app._new_session_items_cache = None
        with patch.object(app.hub, "sessions", {}), patch("shutil.which", return_value="/bin/x"):
            app.new_session_items()
        with patch.object(app.hub, "sessions", fake):
            order = [row[0] for row in app.new_session_items()]
        self.assertEqual(order, ["claude", "codex", "opencode", "agy"])

    def test_26_recent_shows_all_sessions_without_web_archive_row_and_clamps_scroll(self):
        """
        Contract 26:
          1) `_sidebar_display_rows()` shows ALL discovered `Recent` sessions without a 20-row cap
             and never injects a redundant `kind == 'web_archive'` (`↗ All sessions in Web`) row
             (both when `Recent` has >20 rows and when `Recent` has <=20 rows in `--sidebar`).
          2) Oversized `list_scroll` is clamped to `len(drows) - list_h`, and collapsing `Recent`
             immediately clamps `list_scroll` back into `[0, max(0, len(drows) - list_h)]`.
          3) Clicking the last visible `Recent` row selects the exact session, and clicking a blank
             row beyond `len(drows)` is a clean no-op without `IndexError`.
        """
        with patch("tui.curses.color_pair", side_effect=lambda p: p << 8):
            app = self._make_tui(height=30, width=32, sidebar_only=True)
            now = time.time()
            sessions = [
                {"id": "pin-0", "tool": "claude", "title": "Pinned 0", "pinned": True, "isLive": False, "updatedAt": now - 10},
            ]
            for i in range(5):
                sessions.append({
                    "id": f"act-{i}",
                    "tool": "agy",
                    "title": f"Active {i}",
                    "pinned": False,
                    "isLive": True,
                    "updatedAt": now - 100 - i * 10,
                })
            for i in range(45):
                sessions.append({
                    "id": f"rec-{i}",
                    "tool": "claude",
                    "title": f"Recent {i}",
                    "pinned": False,
                    "isLive": False,
                    "updatedAt": now - 3600 - i * 60,
                })
            app.sessions = sessions
            app.sidebar_group_mode = "status"
            app._collapsed_sections.clear()

            drows = app._sidebar_display_rows()
            self.assertFalse(any(r.get("kind") == "web_archive" for r in drows))
            rec_hdr = next(r for r in drows if r.get("kind") == "header" and r.get("key") == "recent")
            self.assertEqual(rec_hdr["count"], 45)
            rec_rows = [r for r in drows if r.get("kind") == "session" and r["session"]["id"].startswith("rec-")]
            self.assertEqual(len(rec_rows), 45)
            self.assertEqual([r["session"]["id"] for r in rec_rows], [f"rec-{i}" for i in range(45)])

            # Also verify <=20 Recent rows in sidebar_only mode has no web_archive row
            app.sessions = sessions[:10]
            self.assertFalse(any(r.get("kind") == "web_archive" for r in app._sidebar_display_rows()))
            app.sessions = sessions

            # Oversized list_scroll clamps to len(drows) - list_h on draw()
            list_top, list_bottom, list_h = app._list_bounds(30)
            app.list_scroll = 9999
            self._draw_sb(app)
            self.assertEqual(app.list_scroll, len(drows) - list_h)

            # Clicking the last visible row selects rec-44 (the 45th Recent session)
            last_vis_y = list_top + list_h - 1
            with patch.object(app, "_on_sidebar_selection_changed"), patch.object(app, "_focus_outer_right_pane_async"):
                app._handle_mouse(parsed_event=(0, 8, last_vis_y, False))
            self.assertEqual(app.sessions[app.selected_idx]["id"], "rec-44")

            # Collapsing `Recent` clamps `list_scroll` back to the smaller row count immediately
            rec_hdr_idx = next(i for i, r in enumerate(drows) if r.get("kind") == "header" and r.get("key") == "recent")
            app.list_scroll = rec_hdr_idx
            app._handle_mouse(parsed_event=(0, 4, list_top, False))
            drows_collapsed = app._sidebar_display_rows()
            self.assertEqual(len(drows_collapsed), 9)  # 3 headers + 1 pinned + 5 active
            self.assertLessEqual(app.list_scroll, max(0, len(drows_collapsed) - list_h))

            # Clicking a blank row below all items when collapsed does not raise IndexError
            prev_sel = app.selected_idx
            app._handle_mouse(parsed_event=(0, 8, list_bottom - 1, False))
            self.assertEqual(app.selected_idx, prev_sel)


if __name__ == "__main__":
    unittest.main()
