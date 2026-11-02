"""Regression coverage for bounded discovery and persistent index caches."""
import errno
import json
import os
from pathlib import Path
import sqlite3
import sys
from unittest.mock import Mock, patch
import zlib

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import server
import probe


SID = "11111111-1111-4111-8111-111111111111"
CHILD = "22222222-2222-4222-8222-222222222222"


@pytest.fixture
def hub(tmp_path):
    root = tmp_path / "hub"
    root.mkdir()
    h = server.Hub(str(tmp_path), str(root))
    h.discover = Mock(side_effect=AssertionError("lightweight lookup must not discover"))
    return h


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))
    return path


def make_db(path, table, row):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.execute(f"CREATE TABLE {table} (" + ",".join(row) + ")")
        db.execute(f"INSERT INTO {table} VALUES (" + ",".join("?" for _ in row) + ")", tuple(row.values()))
    return path


def test_lightweight_claude_native_title_sources_and_prefix(hub):
    project = Path(hub.path(".claude/projects/project"))
    tx = write_json(project / (SID + ".jsonl"), {
        "type": "user", "cwd": hub.home,
        "message": {"role": "user", "content": "first real prompt"},
    })
    write_json(project / "sessions-index.json", {"entries": [{"sessionId": SID, "aiTitle": "Index title"}]})
    s = hub.get_lightweight_session_meta(" CLAUDE ", " " + SID[:12] + " ")
    assert s["id"] == SID and s["path"] == str(tx)
    assert s["title"] == "Index title" and s["firstPrompt"] == "first real prompt"
    assert s["shortCwd"] == "~"
    write_json(project / SID / "custom-title.json", {"customTitle": "Sidecar title"})
    assert hub.get_lightweight_session_meta("claude", SID)["title"] == "Sidecar title"
    write_json(hub.path(".claude/sessions/123.json"), {
        "sessionId": SID, "cwd": "/workspace", "name": "User title", "nameSource": "user",
    })
    assert hub.get_lightweight_session_meta("claude", SID)["title"] == "User title"
    labels = write_json(hub.path(".claude/session_labels.json"), {SID: "Label title"})
    os.utime(labels, (2_000_000_000, 2_000_000_000))
    assert hub.get_lightweight_session_meta("", SID)["title"] == "Label title"
    write_json(hub.state_path, {"sessions": {"claude:" + SID: {"title": "Hub title", "pinned": True}}})
    s = hub.get_lightweight_session_meta("claude", SID[:12])
    assert s["title"] == "Hub title" and s["pinned"]
    assert s["cwd"] == "/workspace" and s["userNameMtime"] > 0


def test_lightweight_claude_pid_state_only_and_ambiguous_prefix(hub):
    write_json(hub.path(".claude/sessions/123.json"), {"sessionId": SID, "name": "slug", "cwd": "/work"})
    s = hub.get_lightweight_session_meta("claude", SID)
    assert s["slug"] == "slug" and s["cwd"] == "/work"
    for suffix in ("a", "b"):
        write_json(hub.path(f".claude/projects/p/shared-{suffix}.jsonl"), {})
    assert hub.get_lightweight_session_meta("claude", "shared-") is None


def test_lightweight_agy_db_and_transcript(hub):
    db = make_db(hub.path(".gemini/antigravity-cli/conversation_summaries.db"), "conversation_summaries", {
        "conversation_id": SID, "title": "AGY title", "preview": "Preview",
        "workspace_uris": json.dumps(["file:///work%20space"]), "last_modified_time": 1234567890,
        "step_count": 7, "status": "idle", "parent_conversation_id": CHILD, "agent_name": "worker",
    })
    s = hub.get_lightweight_session_meta("agy", SID[:12])
    assert s["id"] == SID and s["sourceTitle"] == "AGY title" and s["lastPrompt"] == "Preview"
    assert s["cwd"] == "/work space" and s["updatedAt"] == 1234567890
    assert s["stepCount"] == 7 and s["status"] == "idle" and s["parentId"] == CHILD
    assert s["agentName"] == "worker" and s["isSubagent"]
    tx = write_json(hub.path(f".gemini/antigravity-cli/brain/{SID}/.system_generated/logs/transcript.jsonl"),
                    {"type": "USER_INPUT", "content": "First AGY question"})
    s = hub.get_lightweight_session_meta("agy", SID)
    assert s["path"] == str(tx) and s["firstPrompt"] == "First AGY question"
    assert not hub._db_rows_cache
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO conversation_summaries (conversation_id) VALUES (?)", (SID[:-1] + "2",))
    assert hub.get_lightweight_session_meta("agy", SID[:12]) is None


def test_lightweight_codex_db_schema_fallback_and_index(hub):
    tx = write_json(hub.path(".codex/sessions/rollout.jsonl"), {})
    make_db(hub.path(".codex/state_6.sqlite"), "unrelated", {"id": SID})
    make_db(hub.path(".codex/state_5.sqlite"), "threads", {
        "id": SID, "title": "Codex title", "cwd": "/code", "first_user_message": "First question",
        "updated_at": 1234567890, "rollout_path": str(tx), "model": "test-model", "reasoning_effort": "high",
    })
    s = hub.get_lightweight_session_meta("codex", SID[:12])
    assert s["id"] == SID and s["path"] == str(tx) and s["title"] == "Codex title"
    assert s["firstPrompt"] == "First question" and s["cwd"] == "/code"
    assert s["model"] == "test-model" and s["reasoningEffort"] == "high"
    assert s["updatedAt"] == 1234567890 and not hub._db_rows_cache
    write_json(hub.path(".codex/session_index.jsonl"), {"id": CHILD, "thread_name": "Index only", "updated_at": 1234567891})
    assert hub.get_lightweight_session_meta("codex", CHILD[:12])["title"] == "Index only"


@pytest.mark.parametrize("tool", ["claude", "agy", "codex", ""])
def test_lightweight_missing_and_new_sessions(hub, tool):
    assert hub.get_lightweight_session_meta(tool, "") is None
    assert hub.get_lightweight_session_meta(tool, "not-found") is None
    assert hub.get_lightweight_session_meta(tool, "new-test")["isLive"]


def test_lightweight_stored_only_and_literal_prefix(hub):
    write_json(hub.state_path, {"sessions": {"codex:stored": {"title": "Saved", "pinned": True}}})
    assert hub.get_lightweight_session_meta("codex", "stored")["title"] == "Saved"
    make_db(hub.path(".codex/state_5.sqlite"), "threads", {"id": SID})
    assert hub.get_lightweight_session_meta("codex", "%") is None
    assert hub.get_lightweight_session_meta("codex", SID)["id"] == SID


def test_rows_truncate_only_large_prompt_columns_before_caching(hub):
    path = make_db(hub.path("data.sqlite"), "threads", {
        "id": SID, "first_user_message": "文" * 5000, "preview": "p" * 5000, "title": "t" * 5000,
    })
    wanted = ["id", "first_user_message", "preview", "title"]
    first = list(hub.rows(str(path), "threads", wanted))[0]
    assert len(first["first_user_message"]) == len(first["preview"]) == len(first["title"]) == 512
    with patch.object(server.sqlite3, "connect", side_effect=AssertionError("cache miss")):
        assert list(hub.rows(str(path), "threads", wanted)) == [first]
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE threads SET first_user_message = NULL, preview = 42")
    row = list(hub.rows(str(path), "threads", wanted))[0]
    assert row["first_user_message"] is None and row["preview"] == 42


def test_subagent_cache_roundtrip_dirty_and_deleted_paths(hub):
    tx = write_json(hub.path(f".gemini/antigravity-cli/brain/{SID}/.system_generated/logs/transcript.jsonl"),
                    {"name": "invoke_subagent", "conversationId": CHILD})
    assert hub._extract_invoked_subagent_parents([str(tx)]) == {CHILD: SID}
    assert hub.cache_dirty
    hub._subagent_links_cache[hub.path("missing.jsonl")] = ((1, 2), [CHILD])
    hub._save_disk_cache()
    payload = json.loads(zlib.decompress(Path(hub.disk_cache_path).read_bytes()))
    assert payload["subagent_links_v"] == 1 and list(payload["subagent_links"]) == [str(tx)]
    restored = server.Hub(hub.home, hub.root)
    with patch("builtins.open", side_effect=AssertionError("unchanged transcript reread")):
        assert restored._extract_invoked_subagent_parents([str(tx)]) == {CHILD: SID}
    assert not restored.cache_dirty
    with tx.open("a") as f:
        f.write("\n{}\n")
    restored._extract_invoked_subagent_parents([str(tx)])
    assert restored.cache_dirty


def test_subagent_disk_validation_and_readonly_cache(hub):
    path = Path(hub.disk_cache_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": server.INDEX_CACHE_SCHEMA_VERSION, "entries": {"cached": [1, 2, {"title": "Heavy"}]},
               "subagent_links_v": 1, "subagent_links": {
                   "bad": ["invalid", 2, []], "wrong": [1, 2, {}], "short": [],
                   "valid": ["1", "2", [CHILD, "", None, 3]],
               }}
    path.write_bytes(zlib.compress(json.dumps(payload).encode()))
    loaded = server.Hub(hub.home, hub.root)
    assert loaded._subagent_links_cache == {"valid": ((1, 2), [CHILD])}
    assert loaded.cache["cached"][1]["title"] == "Heavy"
    before = path.read_bytes(), path.stat().st_mtime_ns
    with patch.object(server.zlib, "decompress", side_effect=AssertionError("readonly cache decoded")):
        readonly = server.Hub(hub.home, hub.root, readonly_cache=True)
    assert not readonly.cache and not readonly._subagent_links_cache
    readonly.cache_dirty = True
    readonly._save_disk_cache()
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before


@pytest.mark.parametrize("error, alive", [
    (None, True), (PermissionError(), True), (OSError(errno.EPERM, "denied"), True),
    (ProcessLookupError(), False), (OSError(errno.ESRCH, "gone"), False), (OSError(errno.EIO, "unknown"), False),
])
def test_cached_pids_alive_only_sends_signal_zero(error, alive):
    with patch.object(server.os, "kill", side_effect=error) as kill:
        assert server.Hub._cached_pids_alive([{"pid": 123}]) is alive
        kill.assert_called_once_with(123, 0)


@pytest.mark.parametrize("pid", [None, 0, -1, "123", True])
def test_invalid_cached_pids_force_refresh(pid):
    with patch.object(server.os, "kill") as kill:
        assert not server.Hub._cached_pids_alive([{"pid": pid}])
        kill.assert_not_called()


@pytest.mark.parametrize("age,alive,env_changed,reused", [
    (20, True, False, True), (59, True, False, True), (60, True, False, False),
    (20, False, False, False), (20, True, True, False),
])
def test_process_cache_extended_ttl_requires_live_pids_and_same_environment(hub, age, alive, env_changed, reused):
    hub._proc_cache = [{"pid": 123, "ppid": 1, "tool": "claude", "args": ["claude", "--resume", SID],
                        "tty": "ttys001", "cwd": "/work", "rss_mb": 1, "etime": "1:00", "wrapper": False}]
    hub._proc_scanned = hub._proc_real_scanned = 100.0
    hub._last_proc_env_sig = ((), (), (), None) if not env_changed else ("changed",)
    with patch.object(server.time, "monotonic", return_value=100 + age), \
         patch.object(server.os, "kill", side_effect=None if alive else ProcessLookupError()), \
         patch.object(server.Hub, "processes", return_value=[]) as processes, \
         patch.object(server.Hub, "open_files", return_value={}), \
         patch.object(server.Hub, "_probe_active_lock_uuids", return_value={"codex": {}, "agy": {}}), \
         patch.object(server.Hub, "_maybe_schedule_pane_probes"), \
         patch.object(server.subprocess, "run"):
        hub._discover_locked()
    assert (processes.call_count == 0) is reused
    assert hub._proc_real_scanned == (100 if reused else 100 + age)


@pytest.mark.parametrize("age,env_changed,reused", [
    (2, False, True), (20, False, True), (59, False, True),
    (60, False, False), (20, True, False),
])
def test_successful_empty_process_scan_is_cached(hub, age, env_changed, reused):
    with patch.object(server.time, "monotonic", return_value=100) as clock, \
         patch.object(server.Hub, "processes", return_value=[]) as processes, \
         patch.object(server.Hub, "open_files", return_value={}), \
         patch.object(server.Hub, "_probe_active_lock_uuids", return_value={"codex": {}, "agy": {}}), \
         patch.object(server.Hub, "_maybe_schedule_pane_probes"), \
         patch.object(server.subprocess, "run") as run:
        hub._discover_locked()
        assert hub._proc_cache == [] and hub._proc_real_scanned == 100
        run.assert_not_called()  # No resident tmux pane scan when there are no processes.
        if env_changed:
            hub._last_proc_env_sig = ("changed",)
        clock.return_value = 100 + age
        hub._discover_locked()
    assert processes.call_count == (1 if reused else 2)


@pytest.mark.parametrize("inside_tmux,socket,probed", [
    (True, False, True), (False, True, True), (False, False, False),
])
def test_web_daemon_started_outside_tmux_still_watches_agent_screens(hub, tmp_path, monkeypatch,
                                                                     inside_tmux, socket, probed):
    # `mixhub web` typed in a plain terminal has no $TMUX. Codex and Gemini approval prompts only show on their screen,
    # so without the probe the phone never showed an approval bar for them.
    monkeypatch.setenv("TMUX_TMPDIR", str(tmp_path / "t"))
    if inside_tmux:
        monkeypatch.setenv("TMUX", "/tmp/tmux-501/default,1,0")
    else:
        monkeypatch.delenv("TMUX", raising=False)
    if socket:
        sock = tmp_path / "t" / f"tmux-{os.getuid()}" / "hubpool"
        sock.parent.mkdir(parents=True)
        sock.touch()
    with patch.object(server.Hub, "processes", return_value=[]), \
         patch.object(server.Hub, "open_files", return_value={}), \
         patch.object(server.Hub, "_probe_active_lock_uuids", return_value={"codex": {}, "agy": {}}), \
         patch.object(server.Hub, "_maybe_schedule_pane_probes") as schedule, \
         patch.object(server.subprocess, "run", new=lambda *a, **k: Mock(returncode=1, stdout="", stderr="")):
        hub._discover_locked()
    assert schedule.called is probed


def test_web_refresh_every_30s_still_sees_a_screen_only_approval(hub, monkeypatch):
    # The web refreshes every 30 s. The probe left over from the previous refresh was stale by then and ignored, so a
    # Codex or Gemini approval prompt read as "working" on the phone. A stale probe is now redone before use.
    monkeypatch.setenv("TMUX", "/tmp/tmux-501/default,1,0")
    probed = []
    with patch.object(server.time, "monotonic", return_value=100) as clock, \
         patch.object(server.Hub, "processes", return_value=[]), \
         patch.object(server.Hub, "open_files", return_value={}), \
         patch.object(server.Hub, "_probe_active_lock_uuids", return_value={"codex": {}, "agy": {}}), \
         patch.object(server.Hub, "_maybe_schedule_pane_probes"), \
         patch.object(server.Hub, "refresh_pane_probes_sync",
                      side_effect=lambda *a, **k: probed.append(clock.return_value) or 0), \
         patch.object(server.subprocess, "run", new=lambda *a, **k: Mock(returncode=1, stdout="", stderr="")):
        for t in (100, 102, 130):
            clock.return_value = t
            hub._discover_locked()
    assert probed == [100, 130]  # a fresh probe (2 s old) is reused


def test_failed_process_scan_is_retried_without_caching_empty_result(hub):
    with patch.object(server.time, "monotonic", return_value=100), \
         patch.object(server.Hub, "processes", side_effect=[OSError("ps failed"), []]) as processes, \
         patch.object(server.Hub, "open_files", return_value={}), \
         patch.object(server.Hub, "_probe_active_lock_uuids", return_value={"codex": {}, "agy": {}}), \
         patch.object(server.Hub, "_maybe_schedule_pane_probes"), \
         patch.object(server.subprocess, "run"):
        hub._discover_locked()
        assert hub._proc_real_scanned == hub._proc_scanned == 0
        assert any("ps failed" in warning for warning in hub.warnings)
        hub._discover_locked()
    assert processes.call_count == 2 and hub._proc_real_scanned == 100


def batch_capture_runner(panes, calls):
    """Simulate tmux's stop-on-error command sequence, including framing messages."""
    def run(argv):
        calls.append(argv)
        if "list-windows" in argv:
            return "\n".join(target.removeprefix("pool:").split(".")[0] for target in panes)
        chunks, current = [], []
        for arg in [*argv[3:], ";"]:
            if arg == ";":
                chunks.append(current)
                current = []
            else:
                current.append(arg)
        out = []
        for command in chunks:
            if command[0] == "display-message":
                out.append(command[-1])
            elif command[0] == "capture-pane":
                text = panes.get(command[-1])
                if text is None:
                    return server.subprocess.CompletedProcess(argv, 1, "\n".join(out) + "\n")
                out.append(text)
            else:
                raise AssertionError(command)
        return "\n".join(out) + "\n"
    return run


def test_pane_probes_batch_capture_and_detect_changed_prompt_without_transcript(hub):
    targets = [f"pool:w-claude-{SID[:12]}.{{bottom}}", f"pool:w-agy-{CHILD[:12]}.{{bottom}}"]
    panes = {targets[0]: "❯ ", targets[1]: "Thinking (esc to interrupt)"}
    sessions = {f"claude:{SID}": {"id": SID, "tool": "claude", "isLive": True},
                f"agy:{CHILD}": {"id": CHILD, "tool": "agy", "isLive": True}}
    calls = []
    runner = batch_capture_runner(panes, calls)
    assert hub.refresh_pane_probes_sync(sessions, runner) == 2
    assert len(calls) == 2
    assert hub._pane_probe_cache[f"claude:{SID}"]["result"]["state"] == "idle"
    assert hub._pane_probe_cache[f"agy:{CHILD}"]["result"]["state"] == "working"
    panes[targets[0]] = "Allow execution? [y/n]"
    assert hub.refresh_pane_probes_sync(sessions, runner) == 2
    assert len(calls) == 4  # Same cadence; no idle TTL can hide the new approval prompt.
    assert hub._pane_probe_cache[f"claude:{SID}"]["result"]["state"] == "awaiting_permission"


def test_batch_capture_discards_incomplete_frames_after_pane_disappears():
    targets = ["pool:w-first.{bottom}", "pool:w-gone.{bottom}", "pool:w-last.{bottom}"]
    panes = {targets[0]: "first pane", targets[1]: None, targets[2]: "third pane"}
    calls = []
    captures = probe.capture_panes(targets, runner=batch_capture_runner(panes, calls))
    assert len(calls) == 1 and list(captures) == targets[:1]
    assert captures[targets[0]]["text"] == "first pane"


def test_batch_capture_rejects_unframed_duplicate_or_reordered_markers():
    target = "pool:w-first.{bottom}"
    for mode in ("unframed", "duplicate", "reordered"):
        def runner(argv):
            begin, end = argv[5], argv[-1]
            return {"unframed": "❯ wrong pane", "duplicate": f"{begin}\n{begin}\ntext\n{end}",
                    "reordered": f"{end}\ntext\n{begin}"}[mode]
        assert probe.capture_panes([target], runner=runner) == {}
    with patch("mixhub.probe.capture._run_cmd") as run:
        assert probe.capture_panes([]) == {}
        run.assert_not_called()


def _occupy_port(body: bytes):
    import http.server
    import threading

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.mark.parametrize("body,code,needle", [
    (b'{"ok": true, "service": "agent-session-hub"}', 0, "already running"),
    (b"<html>other app</html>", 2, "HUB_PORT"),
])
def test_main_port_in_use_exits_cleanly(monkeypatch, capsys, body, code, needle):
    srv = _occupy_port(body)
    try:
        monkeypatch.setenv("HUB_PORT", str(srv.server_port))
        with pytest.raises(SystemExit) as exc:
            server.main()
        assert exc.value.code == code
        assert needle in capsys.readouterr().err
    finally:
        srv.shutdown()
        srv.server_close()
