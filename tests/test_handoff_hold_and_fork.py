#!/usr/bin/env python3
"""Comprehensive unit tests for Relay Handoff Fidelity, HoldUntilTurnEndQueue, and Distilled Clean Fork (Bugs A-E)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

HUB_ROOT = Path(__file__).resolve().parent.parent
if str(HUB_ROOT) not in sys.path:
    sys.path.insert(0, str(HUB_ROOT))

import handoff


class DummyHub:
    def __init__(self, root: Path, sessions: dict[str, dict]):
        self.root = str(root)
        self.home = str(root)
        self.sessions = sessions

    def discover(self, force: bool = False) -> dict[str, dict]:
        return self.sessions

    def save(self, key: str, updates: dict) -> None:
        if key in self.sessions:
            self.sessions[key].update(updates)
        else:
            sid = key.split(":", 1)[-1]
            if sid in self.sessions:
                self.sessions[sid].update(updates)


class TestRelayFidelityBugsAtoE(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "data").mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_bug_a_and_b_and_c_large_codex_rollout_extraction(self) -> None:
        """
        Verify Bugs A, B, C on a realistic >350KB GPT-6 Codex rollout JSONL:
          - Bug A: First 6 lines (session_meta, additional_tools, developer prompts, injected AGENTS.md)
            exceed 120KB, and middle tool outputs exceed 280KB (total > 400KB).
            `extract_handoff_artifacts` must still capture the real initial user prompt
            (including `严禁修改 legacy_v1_schema.sql`) and ignore injected `# AGENTS.md instructions`.
          - Bug B: Codex `custom_tool_call` with `name="exec"` running `tools.exec_command({cmd:"..."})`
            and `tools.apply_patch(...)` must be extracted into `verification_cmds` (with exit code)
            and `files_modified`, while traceback locations (`billing_reconciler.py:287`) are captured
            in `errors_seen` and `locations` WITHOUT polluting `files_modified` with unedited files.
          - Bug C: When an earlier turn has an `[IRREV]` or decision match, the latest substantive
            assistant conclusion (containing function signature + port override 19476 -> 19477)
            must still be preserved in `Pinned Decisions [IRREV]` / `decisions_rationale`, and
            decoy `[IRREV] 端口定为 8080` inside tool output must NEVER enter `Pinned Decisions [IRREV]`.
        """
        rollout_path = self.root / "rollout-large-codex.jsonl"
        lines = [
            json.dumps({"type": "session_meta", "payload": {"id": "codex-001", "blob": "M" * 22000}}),
            json.dumps({"type": "response_item", "payload": {"type": "additional_tools", "role": "developer", "blob": "T" * 50000}}),
            json.dumps({
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "developer",
                    "content": [{"type": "input_text", "text": "You are Codex. " + ("D" * 55000)}],
                },
            }),
            json.dumps({
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "# AGENTS.md instructions\n<INSTRUCTIONS>\nGlobal context\n</INSTRUCTIONS>"}],
                },
            }),
            json.dumps({
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{
                        "type": "input_text",
                        "text": (
                            "排查账单对账崩溃问题并补齐对账函数。\n"
                            "硬约束：严禁修改 legacy_v1_schema.sql，所有迁移只能在 Python 层完成。"
                        ),
                    }],
                },
            }),
            json.dumps({
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "call_id": "call_1",
                    "name": "exec",
                    "input": 'const r=await tools.exec_command({cmd:"pytest -q tests/test_billing.py",workdir:"/tmp/ws"});text(r);',
                },
            }),
            json.dumps({
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call_output",
                    "call_id": "call_1",
                    "output": (
                        "Process exited with code 1\n"
                        "Output:\n"
                        "[IRREV] 端口定为 8080 (decoy inside log dump)\n"
                        + ("NOISE_LOG_LINE_ABCDEF_0123456789\n" * 9500)
                        + 'Traceback (most recent call last):\n'
                        + '  File "/tmp/ws/billing_reconciler.py", line 287, in reconcile_epoch\n'
                        + "    raise RuntimeError('wal_epoch_mismatch: expected monotonic fence')\n"
                        + "RuntimeError: wal_epoch_mismatch: expected monotonic fence\n"
                    ),
                },
            }),
            json.dumps({
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{
                        "type": "input_text",
                        "text": (
                            "先暂定守护端口 19476。\n"
                            "另外已否决方案：放弃 Redis PubSub 广播锁（跨机房闪断下会丢失 ACK 导致脑裂），改用 SQLite WAL 单写者租约。"
                        ),
                    }],
                },
            }),
            json.dumps({
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{
                        "type": "output_text",
                        "text": "[IRREV] 架构锁方案：决定采用 SQLite WAL 单写者租约替代 Redis PubSub。",
                    }],
                },
            }),
            json.dumps({
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{
                        "type": "input_text",
                        "text": "端口 19476 已被占用，守护进程监听端口就定为 19477 吧，别再动了，握手暗号设为 CANARY-VORTEX-7391。",
                    }],
                },
            }),
            json.dumps({
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call",
                    "call_id": "call_2",
                    "name": "exec",
                    "input": (
                        'text(await tools.apply_patch("*** Begin Patch\\n'
                        '*** Update File: /tmp/ws/billing_reconciler.py\\n@@\\n'
                        '+def reconcile_epoch_ledger(epoch_id: int, strict_fence: bool = True) -> tuple[int, str]:\\n'
                        '*** End Patch\\n"));'
                    ),
                },
            }),
            json.dumps({
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call_output",
                    "call_id": "call_2",
                    "output": "Success. Updated the following files:\nM /tmp/ws/billing_reconciler.py\n",
                },
            }),
            json.dumps({
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{
                        "type": "output_text",
                        "text": (
                            "已完成定位与初步补丁：\n"
                            "1. 报错根因位于 `billing_reconciler.py:287` (`wal_epoch_mismatch`)。\n"
                            "2. 新增核心对账函数签名：`def reconcile_epoch_ledger(epoch_id: int, strict_fence: bool = True) -> tuple[int, str]`。\n"
                            "3. 守护进程监听端口最终定为 `19477`，握手暗号 `CANARY-VORTEX-7391`。\n"
                            "4. 待验证 (TODO)：需确认 WAL checkpoint 阈值设为 4096 页还是 8192 页。"
                        ),
                    }],
                },
            }),
            json.dumps({
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "好的，随时可以交接。"}],
                },
            }),
        ]
        rollout_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.assertGreater(rollout_path.stat().st_size, 350_000)

        sid = "codex-001"
        session = {
            "id": sid,
            "tool": "codex",
            "title": "账单对账崩溃修复",
            "path": str(rollout_path),
            "cwd": str(self.root),
            "sessionState": "IDLE",
        }
        hub = DummyHub(self.root, {sid: session})
        res = handoff.create_handoff(hub, sid, to_tool="agy", mode="delegate")
        self.assertTrue(res["ok"])
        md = res["markdown"]
        contract = res["contract"]

        # Bug A assertions: initial prompt & constraint from line 4 (after >120KB headers) must be present
        self.assertIn("排查账单对账崩溃问题", md)
        self.assertNotIn("AGENTS.md instructions", md)
        self.assertTrue(
            any("严禁修改 legacy_v1_schema.sql" in c for c in contract["interface_contracts"]),
            f"Missing verbatim constraint in contract: {contract['interface_contracts']}",
        )

        # Bug B assertions: exec command + traceback billing_reconciler.py:287 + modified file
        self.assertIn("/tmp/ws/billing_reconciler.py", contract["repository"]["changed_files"])
        self.assertNotIn("legacy_v1_schema.sql", contract["repository"]["changed_files"])
        self.assertTrue(
            any("pytest -q tests/test_billing.py" in v for v in res["sections"]["Verification Snapshot"]),
            f"Missing exec command in Verification Snapshot: {res['sections']['Verification Snapshot']}",
        )
        self.assertTrue(
            any("billing_reconciler.py:287" in e for e in contract["failed_attempts"])
            or "billing_reconciler.py:287" in md,
            f"Missing exact error line billing_reconciler.py:287 in bundle: {contract['failed_attempts']}",
        )

        # Bug C assertions: latest substantive assistant conclusion & port 19477 + signature + canary preserved,
        # while decoy 8080 from tool output is NOT in Pinned Decisions
        pinned_join = "\n".join(res["sections"]["Pinned Decisions [IRREV]"])
        self.assertNotIn("8080", pinned_join)
        self.assertIn("19477", pinned_join)
        self.assertIn("CANARY-VORTEX-7391", pinned_join)
        self.assertIn("def reconcile_epoch_ledger(epoch_id: int, strict_fence: bool = True) -> tuple[int, str]", pinned_join)

        # Rejected approach must have all 3 elements (what, why, alternative)
        rejected_join = "\n".join(res["sections"]["Rejected Approaches"])
        self.assertIn("Redis PubSub", rejected_join)
        self.assertIn("ACK", rejected_join)
        self.assertIn("SQLite WAL", rejected_join)

        # Unresolved question from latest assistant conclusion
        unresolved_join = "\n".join(res["sections"]["Unresolved Questions"])
        self.assertIn("4096", unresolved_join)
        self.assertIn("8192", unresolved_join)


    def test_two_hop_multiline_subbullet_coalescing_zero_attenuation(self) -> None:
        """
        Verify that when Hop 1 (AGY) formats its findings with numbered headers and indented
        sub-bullets (e.g. '3. 端口与暗号：\\n - 最终监听端口：19477\\n - 废弃端口：19476\\n - 诱饵：8080',
        '4. 被否决方案：\\n - 被否决方案：Redis PubSub\\n - 否决原因：丢 ACK\\n - 替代方案：SQLite WAL',
        '5. 硬约束严禁修改的文件：\\n - legacy_v1_schema.sql'), Hop 2 (`AGY -> Codex`) coalesces
        the blocks without losing the parent header or sub-bullet values.
        """
        agy_jsonl = self.root / "agy_hop1.jsonl"
        rows = [
            {
                "step_index": 0,
                "type": "USER_INPUT",
                "content": "<USER_REQUEST>Read handoff bundle and verify all facts.</USER_REQUEST>",
            },
            {
                "step_index": 1,
                "type": "PLANNER_RESPONSE",
                "input_tokens": 5000,
                "cache_read_tokens": 14000,
                "content": (
                    "1. **修复文件、精确报错行号与异常原文**：\n"
                    "   - **修复文件**：billing_reconciler.py\n"
                    "   - **精确报错行号**：第 287 行（`billing_reconciler.py:287`）\n"
                    "   - **抛出的异常原文**：`RuntimeError: wal_epoch_mismatch: monotonic fence broken at slot 73`\n\n"
                    "2. **新增函数的完整 Python 签名**：\n"
                    "   `def reconcile_epoch_ledger(epoch_id: int, strict_fence: bool = True) -> tuple[int, str]:`\n\n"
                    "3. **守护进程监听端口与握手暗号**：\n"
                    "   - **最终监听端口**：`19477`\n"
                    "   - **废弃的旧端口**：`19476`\n"
                    "   - **日志诱饵端口**：`8080`\n"
                    "   - **握手暗号原文**：`CANARY-VORTEX-7391`\n\n"
                    "4. **被否决的锁方案、原因及替代方案**：\n"
                    "   - **被否决方案**：Redis PubSub 广播锁\n"
                    "   - **否决原因**：跨机房闪断下可能丢失 ACK，导致脑裂\n"
                    "   - **替代方案**：SQLite WAL 单写者租约\n\n"
                    "5. **硬约束严禁修改的文件**：\n"
                    "   - `legacy_v1_schema.sql`（所有迁移适配只能在 Python 层完成）\n\n"
                    "6. **接力推进决策 [IRREV] 与新待验证项 (TODO)**：\n"
                    "   - **技术裁决 [IRREV]**：将 WAL checkpoint 阈值正式拍板定为 **4096 页**（放弃 8192 页）。\n"
                    "   - **新增待验证项 (TODO)**：验证 `strict_fence=True` 超时重试次数（3 次 vs 5 次）。"
                ),
            },
        ]
        agy_jsonl.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")

        sid = "agy-hop1"
        session = {
            "id": sid,
            "tool": "agy",
            "title": "↳ 账单对账崩溃修复",
            "path": str(agy_jsonl),
            "cwd": str(self.root),
            "handoffChain": ["codex", "agy"],
            "sessionState": "IDLE",
        }
        hub = DummyHub(self.root, {sid: session})
        res = handoff.create_handoff(hub, sid, to_tool="codex", mode="delegate")
        self.assertTrue(res["ok"])
        self.assertEqual(res["handoffChain"], ["codex", "agy", "codex"])
        md = res["markdown"]
        self.assertIn("billing_reconciler.py:287", md)
        self.assertIn("RuntimeError: wal_epoch_mismatch: monotonic fence broken at slot 73", md)
        self.assertIn("def reconcile_epoch_ledger(epoch_id: int, strict_fence: bool = True) -> tuple[int, str]", md)
        self.assertIn("19477", md)
        self.assertIn("CANARY-VORTEX-7391", md)
        self.assertIn("Redis PubSub", md)
        self.assertIn("跨机房闪断", md)
        self.assertIn("SQLite WAL", md)
        self.assertIn("legacy_v1_schema.sql", md)
        self.assertIn("4096", md)


if __name__ == "__main__":
    unittest.main()


def test_resolve_5state_uses_canonical_classifier_for_unread():
    import handoff
    s = {"tool": "claude", "id": "u1", "isLive": True, "agentState": "done_idle", "updatedAt": 200.0}
    assert handoff._resolve_session_5state(s, seen_timestamps={"claude:u1": 100.0}) == "UNREAD"
