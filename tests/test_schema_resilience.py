"""Schema-evolution hardening: 5 brittle points (see agent_schema_evolution_and_whitelist.md)."""
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server

C = "11111111-1111-4111-8111-111111111111"
X = "33333333-3333-4333-8333-333333333333"
X6 = "66666666-6666-4666-8666-666666666666"
NEW = "77777777-7777-4777-8777-777777777777"
B = "88888888-8888-4888-8888-888888888888"


def cu(text, ts, **extra):
    row = {"type": "user", "cwd": "/w", "timestamp": ts, "message": {"role": "user", "content": text}}
    row.update(extra)
    return row


def ca(text, ts, **extra):
    row = {"type": "assistant", "cwd": "/w", "timestamp": ts,
           "message": {"role": "assistant", "content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}}
    row.update(extra)
    return row


class SchemaResilienceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.root = self.home / "hub"
        self.root.mkdir()
        self.hub = server.Hub(str(self.home), str(self.root))
        self.hub.processes = lambda: []
        self.hub.open_files = lambda _: {}

    def tearDown(self):
        self.temp.cleanup()

    def rows_to(self, path, rows):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        return path

    def rescan(self):
        self.hub.scanned = 0
        return self.hub.discover(True)

    # ---- Brittle #1: Codex state_N.sqlite ---------------------------------
    def make_codex_db(self, n, sid=None, title="", with_threads=True):
        path = self.home / (".codex/state_%d.sqlite" % n)
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path)
        if with_threads:
            db.execute("CREATE TABLE threads(id TEXT, title TEXT, cwd TEXT, first_user_message TEXT, updated_at INTEGER)")
            if sid:
                db.execute("INSERT INTO threads VALUES(?,?,?,?,?)", (sid, title, "/w", "q " + title, int(time.time())))
        else:
            db.execute("CREATE TABLE unrelated(x TEXT)")
        db.commit()
        db.close()
        return path

    def test_codex_newest_state_db_wins(self):
        self.make_codex_db(5, X, "from state5")
        self.make_codex_db(6, X6, "from state6")
        sessions = self.rescan()
        self.assertIn("codex:" + X6, sessions)
        self.assertNotIn("codex:" + X, sessions)

    def test_codex_newest_db_without_threads_falls_back(self):
        self.make_codex_db(5, X, "from state5")
        self.make_codex_db(7, with_threads=False)
        sessions = self.rescan()
        self.assertIn("codex:" + X, sessions)
        self.assertEqual(self.hub.warnings, [])

    def test_codex_candidates_order_numeric_not_lexical(self):
        for n in (5, 10, 9):
            self.make_codex_db(n)
        names = [os.path.basename(p) for p in server.codex_state_db_candidates(self.hub.path)]
        self.assertEqual(names[:3], ["state_10.sqlite", "state_9.sqlite", "state_5.sqlite"])

    # ---- Brittle #2: Claude entrypoint / kind ------------------------------
    def claude_path(self):
        return self.home / (".claude/projects/p/" + C + ".jsonl")

    def test_claude_desktop_session_with_one_sdk_row_stays_human(self):
        self.rows_to(self.claude_path(), [
            cu("desktop human question", "2026-10-25T01:00:00Z", entrypoint="claude-desktop"),
            ca("answer", "2026-10-25T01:00:05Z", entrypoint="claude-desktop"),
            ca("sdk side effect", "2026-10-25T01:00:06Z", entrypoint="sdk-cli"),
        ])
        s = self.rescan()["claude:" + C]
        self.assertFalse(s.get("automated"))
        self.assertFalse(s.get("isTemp"))

    def test_claude_pure_sdk_cli_still_headless(self):
        self.rows_to(self.claude_path(), [
            cu("headless prompt", "2026-10-25T01:00:00Z", entrypoint="sdk-cli"),
            ca("answer", "2026-10-25T01:00:05Z", entrypoint="sdk-cli"),
        ])
        self.assertTrue(self.rescan()["claude:" + C].get("automated"))

    def write_pid(self, pid, kind, entrypoint="cli"):
        self.rows_to(self.claude_path(), [cu("human prompt", "2026-10-25T01:00:00Z", entrypoint="cli")])
        p = self.home / (".claude/sessions/%d.json" % pid)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"pid": pid, "sessionId": C, "cwd": "/w", "entrypoint": entrypoint, "kind": kind}))

    def test_unknown_interactive_kind_fails_open(self):
        self.write_pid(424242, "tui")
        s = self.rescan()["claude:" + C]
        self.assertFalse(s.get("consultant"))
        self.assertFalse(s.get("automated"))

    def test_known_headless_kind_still_hidden(self):
        self.write_pid(424243, "headless")
        self.assertTrue(self.rescan()["claude:" + C].get("consultant"))

    # ---- Brittle #3: synthetic Claude user rows ----------------------------
    def test_synthetic_user_rows_do_not_pollute_prompt_or_time(self):
        self.rows_to(self.claude_path(), [
            cu("real human question", "2026-10-25T01:00:00Z", entrypoint="cli"),
            ca("answer", "2026-10-25T01:00:05Z", entrypoint="cli"),
            cu("This session is being continued from a previous conversation...", "2026-10-25T03:00:00Z",
               entrypoint="cli", isCompactSummary=True, isVisibleInTranscriptOnly=True),
            cu("Output token limit hit. Resume directly", "2026-10-25T03:00:01Z", entrypoint="cli", isMeta=True),
        ])
        s = self.rescan()["claude:" + C]
        self.assertEqual(s["lastPrompt"], "real human question")
        self.assertEqual(s["attentionAt"], server.stamp("2026-10-25T01:00:05Z"))
        # Transcript view still renders the synthetic rows.
        rendered = list(server.entries(json.loads("[" + ",".join(
            self.claude_path().read_text().strip().splitlines()) + "]"), "claude"))
        self.assertTrue(any(e.get("synthetic") for e in rendered))

    def test_session_with_only_synthetic_and_sdk_rows_is_headless(self):
        self.rows_to(self.claude_path(), [
            cu("hook expansion", "2026-10-25T01:00:00Z", entrypoint="claude-desktop", isMeta=True),
            ca("answer", "2026-10-25T01:00:05Z", entrypoint="sdk-cli"),
        ])
        self.assertTrue(self.rescan()["claude:" + C].get("automated"))

    # ---- Brittle #4: AGY DB lag quarantine ---------------------------------
    def make_agy_db(self):
        path = self.home / ".gemini/antigravity-cli/conversation_summaries.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path)
        db.execute("CREATE TABLE conversation_summaries(conversation_id TEXT, title TEXT, preview TEXT, workspace_uris TEXT, "
                   "parent_conversation_id TEXT, step_count INTEGER, last_modified_time INTEGER, last_user_input_step_index INTEGER)")
        db.execute("INSERT INTO conversation_summaries VALUES(?,?,?,?,?,?,?,?)",
                   (B, "other", "", json.dumps(["file:///w"]), "", 2, int(time.time()), 0))
        db.commit()
        db.close()
        return path

    def agy_transcript(self):
        return self.rows_to(self.home / (".gemini/antigravity-cli/brain/" + NEW + "/.system_generated/logs/transcript.jsonl"),
                            [{"step_index": 0, "type": "USER_INPUT", "source": "USER_EXPLICIT",
                              "content": "<USER_REQUEST>human prompt</USER_REQUEST>", "created_at": "2026-10-25T01:00:00Z"}])

    def test_agy_young_unindexed_transcript_with_live_db_is_quarantined(self):
        self.make_agy_db()
        self.agy_transcript()
        self.assertTrue(self.rescan()["agy:" + NEW].get("automated"))

    def test_agy_stalled_db_fails_open(self):
        db = self.make_agy_db()
        old = time.time() - 3600
        os.utime(db, (old, old))
        self.agy_transcript()
        s = self.rescan()["agy:" + NEW]
        self.assertFalse(s.get("automated"))
        self.assertEqual(s.get("lastPrompt"), "human prompt")

    def test_agy_unindexed_past_grace_fails_open(self):
        self.make_agy_db()
        self.agy_transcript()
        with patch.dict(server.PROVIDER_SCHEMA_ADAPTERS["agy"], {"db_lag_grace_s": 0}):
            self.assertFalse(self.rescan()["agy:" + NEW].get("automated"))

    # ---- Brittle #5: adapter table is the single source of truth -----------
    def test_adapter_table_redirects_transcript_glob(self):
        alt = self.home / (".claude/projects_v2/p/" + C + ".jsonl")
        self.rows_to(alt, [cu("moved storage prompt", "2026-10-25T01:00:00Z", entrypoint="cli")])
        with patch.dict(server.PROVIDER_SCHEMA_ADAPTERS["claude"], {"transcript_glob": ".claude/projects_v2/*/*.jsonl"}):
            s = self.rescan()["claude:" + C]
        self.assertEqual(s["lastPrompt"], "moved storage prompt")

    # ---- Codex quota live rollout tracking & dot vs started window ---------
    def test_codex_quota_dot_when_expired_and_live_update_when_started(self):
        import tui

        now = time.time()
        rollout = self.home / ".codex/sessions/2026/10/25/rollout-2026-10-25T16-14-51-01a171e9-84a6-7372-9f16-72cd257090a8.jsonl"
        # 1) Expired 5h window (resets_at in the past), active weekly window (21% used):
        self.rows_to(rollout, [{
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 3600)),
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "rate_limits": {
                    "primary": {"used_percent": 67.0, "window_minutes": 300, "resets_at": int(now - 600)},
                    "secondary": {"used_percent": 21.0, "window_minutes": 10080, "resets_at": int(now + 400000)},
                },
            },
        }])
        items1 = tui.get_cli_quota_metrics(home_dir=str(self.home))
        cx1 = next(it for it in items1 if it["key"] == "cx")
        self.assertIsNone(cx1["rem_5h"])
        self.assertFalse(cx1.get("started_5h"))
        self.assertEqual(cx1["rem_wk"], 79)
        self.assertTrue(cx1.get("started_wk"))

        # 2) New 5h window starts on Turn 1 with used_percent == 0.0 -> immediately started_5h=True (not hidden as dot)
        #    and cache invalidates immediately on append without waiting 3s TTL!
        rst_5h = int(now + 17990)
        rst_wk = int(now + 604790)
        with open(rollout, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 5)),
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "rate_limits": {
                        "primary": {"used_percent": 0.0, "window_minutes": 300, "resets_at": rst_5h},
                        "secondary": {"used_percent": 0.0, "window_minutes": 10080, "resets_at": rst_wk},
                    },
                },
            }) + "\n")
        items2 = tui.get_cli_quota_metrics(home_dir=str(self.home))
        cx2 = next(it for it in items2 if it["key"] == "cx")
        self.assertEqual(cx2["rem_5h"], 100)
        self.assertTrue(cx2.get("started_5h"))
        self.assertTrue(bool(cx2.get("reset_5h")))

        # 3) Fractional usage (0.3% used) + negative 2s resets_at jitter + half-written trailing JSON line:
        with open(rollout, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 1)),
                "type": "event_msg",
                "payload": {
                    "type": "token_count",
                    "rate_limits": {
                        "primary": {"used_percent": 0.3, "window_minutes": 300, "resets_at": rst_5h - 2},
                        "secondary": {"used_percent": 1.0, "window_minutes": 10080, "resets_at": rst_wk - 2},
                    },
                },
            }) + "\n")
            f.write('{"timestamp":"2026-10-25T07:16:00Z","payload":{"rate_limits":')
        items3 = tui.get_cli_quota_metrics(home_dir=str(self.home))
        cx3 = next(it for it in items3 if it["key"] == "cx")
        self.assertEqual(cx3["rem_5h"], 99)  # 100 - 99 = 1% used
        self.assertEqual(cx3["rem_wk"], 99)


if __name__ == "__main__":
    unittest.main()
