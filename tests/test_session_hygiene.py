"""Leftovers between a CLI and MixHub: hibernate markers, the dim prompt suggestion, a takeover racing the old process,
and the hint for a session still on an older Claude Code."""
import json
import os
import re
import time
from types import SimpleNamespace
from unittest import mock

import tui
from mixhub.core import cli_version
from mixhub.tui.hubtui.actions import SessionActionsMixin


def _exit_env(tmp_path):
    marker_dir = tmp_path / "data" / "hibernating"
    marker_dir.mkdir(parents=True)
    ws_path = tmp_path / "active_working_set.json"
    ws_path.write_text(json.dumps({"sessions": ["claude:s1"]}), encoding="utf-8")
    patches = (
        mock.patch("mixhub.tui.paths.HIBERNATING_MARKER_DIR", str(marker_dir)),
        mock.patch("mixhub.tui.paths.LAST_EXIT_DIR", str(tmp_path / "data" / "last_exit")),
        mock.patch("tui.subprocess.run"),
        mock.patch("mixhub.tui.selection.write_active_selection"),
    )
    for p in patches:
        p.start()
    return marker_dir, ws_path, patches


def test_old_hibernate_marker_does_not_swallow_a_real_exit(tmp_path):
    marker_dir, ws_path, patches = _exit_env(tmp_path)
    try:
        marker = marker_dir / "s1"
        marker.write_text("1792947630.78929", encoding="utf-8")
        week_ago = time.time() - 7 * 86400
        os.utime(marker, (week_ago, week_ago))
        assert tui.handle_cli_on_exit("claude", "s1", "", 0, working_set_path=str(ws_path)) == 0
        assert "claude:s1" not in json.loads(ws_path.read_text(encoding="utf-8"))["sessions"]
        assert not marker.exists()
    finally:
        for p in patches:
            p.stop()


def test_fresh_hibernate_marker_still_keeps_the_session_active(tmp_path):
    marker_dir, ws_path, patches = _exit_env(tmp_path)
    try:
        (marker_dir / "s1").write_text(str(time.time()), encoding="utf-8")
        assert tui.handle_cli_on_exit("claude", "s1", "", 0, working_set_path=str(ws_path)) == 0
        assert "claude:s1" in json.loads(ws_path.read_text(encoding="utf-8"))["sessions"]
    finally:
        for p in patches:
            p.stop()


def test_dim_prompt_suggestion_is_not_saved_as_a_draft(tmp_path):
    session = {"id": "s-1", "tool": "claude", "lastPrompt": ""}
    pane = "────\n\x1b[39m❯ \x1b[2mcommit the header fix\x1b[22m\n────\n"
    drafts = str(tmp_path / "drafts.json")
    with mock.patch("tui.subprocess.run", return_value=mock.MagicMock(stdout=pane)) as run:
        assert tui.capture_and_save_hubpool_draft(session, path=drafts) == ""
    assert "-e" in run.call_args.args[0]
    assert not os.path.exists(drafts)


def test_typed_text_with_colors_is_still_a_draft(tmp_path):
    session = {"id": "s-1", "tool": "claude", "lastPrompt": ""}
    pane = "────\n\x1b[39m❯ \x1b[1;38;5;231mhalf typed\x1b[0m \x1b[2mghost\x1b[0m\n────\n"
    with mock.patch("tui.subprocess.run", return_value=mock.MagicMock(stdout=pane)):
        assert tui.capture_and_save_hubpool_draft(session, path=str(tmp_path / "d.json")) == "half typed"


def _takeover_app(alive_rounds):
    """`hub.processes()` reports pid 7 alive for `alive_rounds` polls, then gone."""
    calls = {"n": 0}

    def processes():
        calls["n"] += 1
        return [{"pid": 7}] if calls["n"] <= alive_rounds else []

    hub = mock.MagicMock(home="/nonexistent")
    hub.hibernate.return_value = {"ok": True, "pendingPids": [7]}
    hub.processes.side_effect = processes
    return SimpleNamespace(hub=hub)


def test_takeover_waits_for_the_old_process_before_resuming():
    outside = {"pid": 7, "tty": "ttys001", "resident": False, "boot_id": "bt:1", "start_time": "Sat Oct 31 00:00:00 2026"}
    s = {"id": "s1", "tool": "claude", "processes": [outside], "agentState": "idle"}
    app = _takeover_app(alive_rounds=3)
    with mock.patch("mixhub.tui.hubtui.actions.time.sleep"), \
            mock.patch("mixhub.tui.hubtui.actions._clear_hibernating") as clear:
        ok, _ = SessionActionsMixin._release_external_locks_before_takeover(app, s, force_takeover=True)
    assert ok
    assert app.hub.processes.call_count >= 4
    # The outside process has no MixHub on-exit to consume the marker; left behind it would swallow a later `/exit`.
    clear.assert_called_with("s1")


def test_takeover_refuses_while_the_old_process_is_still_writing():
    outside = {"pid": 7, "tty": "ttys001", "resident": False, "boot_id": "bt:1", "start_time": "Sat Oct 31 00:00:00 2026"}
    s = {"id": "s1", "tool": "claude", "processes": [outside], "agentState": "idle"}
    app = _takeover_app(alive_rounds=10**6)
    with mock.patch("mixhub.tui.hubtui.actions.time.sleep"), \
            mock.patch("mixhub.tui.hubtui.actions.time.monotonic", side_effect=[0.0] + [100.0] * 50), \
            mock.patch("mixhub.tui.hubtui.actions._clear_hibernating"):
        ok, msg = SessionActionsMixin._release_external_locks_before_takeover(app, s, force_takeover=True)
    assert not ok
    assert "still" in msg


def test_installed_claude_version_comes_from_the_versioned_symlink(tmp_path):
    versions = tmp_path / "share" / "claude" / "versions"
    versions.mkdir(parents=True)
    (versions / "2.1.318").write_text("", encoding="utf-8")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    os.symlink(versions / "2.1.318", bindir / "claude")
    cli_version._installed_cache.clear()
    with mock.patch("mixhub.core.cli_version.shutil.which", return_value=str(bindir / "claude")):
        assert cli_version.installed_claude_version() == "2.1.318"


def test_old_version_hint_only_for_an_older_claude_session():
    with mock.patch("mixhub.core.cli_version.installed_claude_version", return_value="2.1.318"):
        assert cli_version.old_version_hint({"tool": "claude", "cliVersion": "2.1.316"}) == "old 2.1.316 · 2.1.318 installed"
        assert cli_version.old_version_hint({"tool": "claude", "cliVersion": "2.1.318"}) == ""
        assert cli_version.old_version_hint({"tool": "claude", "cliVersion": "2.1.320"}) == ""
        assert cli_version.old_version_hint({"tool": "claude"}) == ""
        assert cli_version.old_version_hint({"tool": "codex", "cliVersion": "0.1.0"}) == ""
    with mock.patch("mixhub.core.cli_version.installed_claude_version", return_value=""):
        assert cli_version.old_version_hint({"tool": "claude", "cliVersion": "2.1.316"}) == ""


def test_old_version_hint_reads_the_pid_file_when_the_session_has_no_version(tmp_path):
    """A `--header` pane has only lightweight session meta, no discovery; the hint finds the version itself."""
    sessions_dir = tmp_path / ".claude" / "sessions"
    sessions_dir.mkdir(parents=True)
    (sessions_dir / f"{os.getpid()}.json").write_text(
        json.dumps({"pid": os.getpid(), "sessionId": "s-live", "version": "2.1.311"}), encoding="utf-8")
    (sessions_dir / "999999.json").write_text(
        json.dumps({"pid": 999999, "sessionId": "s-dead", "version": "2.1.200"}), encoding="utf-8")
    cli_version._running_cache.clear()
    with mock.patch("mixhub.core.cli_version.installed_claude_version", return_value="2.1.319"), \
            mock.patch("mixhub.core.cli_version.os.path.expanduser", return_value=str(tmp_path)):
        assert cli_version.old_version_hint({"tool": "claude", "id": "s-live"}) == "old 2.1.311 · 2.1.319 installed"
        assert cli_version.old_version_hint({"tool": "claude", "id": "s-dead"}) == ""


def test_session_parked_into_a_finished_background_job_is_not_busy(tmp_path):
    """Claude Code leaves `status: busy` on a session that parked its turn into a background job; once the job is
    no longer working, the session is idle."""
    from mixhub.hub.discovery import settle_parked_status

    jobs = tmp_path / "jobs"
    (jobs / "j-done").mkdir(parents=True)
    (jobs / "j-done" / "state.json").write_text(json.dumps({"state": "done"}), encoding="utf-8")
    (jobs / "j-run").mkdir()
    (jobs / "j-run" / "state.json").write_text(json.dumps({"state": "working"}), encoding="utf-8")

    done = settle_parked_status({"status": "busy", "parkedJobId": "j-done"}, str(jobs))
    assert done["status"] == "idle" and done["_parkedDone"]
    assert settle_parked_status({"status": "busy", "parkedJobId": "j-gone"}, str(jobs))["status"] == "idle"
    running = settle_parked_status({"status": "busy", "parkedJobId": "j-run"}, str(jobs))
    assert running["status"] == "busy" and not running.get("_parkedDone")
    assert settle_parked_status({"status": "busy"}, str(jobs))["status"] == "busy"


def test_codex_task_sent_by_claude_code_is_automated():
    """A Codex task Claude dispatched (codex-companion) carries `originator: Claude Code`, whatever its prompt says."""
    from mixhub.hub.session_filters import is_codex_automated

    prompt = "You are working on a scroll-driven three.js homepage:"
    assert is_codex_automated("vscode", prompt, prompt, originator="Claude Code")
    assert not is_codex_automated("vscode", prompt, prompt, originator="codex-tui")
    assert not is_codex_automated("vscode", prompt, prompt)


def _with_local_rules(tmp_path, rules):
    from mixhub.hub import constants, session_filters

    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "local_rules.json").write_text(json.dumps(rules), encoding="utf-8")
    session_filters._LOCAL_RULES_CACHE["mtime"] = None
    return mock.patch.object(constants, "ROOT", str(tmp_path))


def test_codex_automation_reason_prefers_what_codex_recorded(tmp_path):
    """The CLI's own record of who started a thread decides first; text rules are only the fallback."""
    from mixhub.hub.session_filters import codex_automation_reason

    with _with_local_rules(tmp_path, {}), mock.patch.dict(os.environ, {"MIXHUB_PREFERENCES_PATH": str(tmp_path / "none")}):
        assert codex_automation_reason("vscode", "<task> x", "<task> x", originator="Claude Code") == "originator"
        assert codex_automation_reason("exec", "fix it", "fix it") == "source"
        assert codex_automation_reason('{"subagent": {}}', "fix it", "fix it") == "source"
        assert codex_automation_reason("vscode", "", "<task> Review the site") == "text:<task>"
        assert codex_automation_reason("cli", "exit", "exit") == "text:exit"
        assert codex_automation_reason("cli", "fix the login bug", "fix the login bug", originator="codex-tui") == ""


def test_personal_codex_prompt_prefixes_live_in_local_rules_and_only_hide_codex(tmp_path):
    from mixhub.hub import session_filters

    rules = {"codex_automated_prefixes": ["Second opinion on"]}
    with _with_local_rules(tmp_path, rules), mock.patch.dict(os.environ, {"MIXHUB_PREFERENCES_PATH": str(tmp_path / "none")}):
        assert session_filters.codex_automation_reason("vscode", "", "Second opinion on the bot") == "text:Second opinion on"
        assert not session_filters.is_automated_or_subagent_text("Second opinion on the bot")
    with _with_local_rules(tmp_path, {}), mock.patch.dict(os.environ, {"MIXHUB_PREFERENCES_PATH": str(tmp_path / "none")}):
        assert session_filters.codex_automation_reason("vscode", "", "Second opinion on the bot") == ""


def test_doctor_hidden_lists_sessions_hidden_only_by_text_rules():
    from mixhub.ops.doctor import hidden_by_text_rules

    sessions = [
        {"tool": "codex", "id": "a", "automated": True, "automatedBy": "originator", "title": "t"},
        {"tool": "codex", "id": "b", "automated": True, "automatedBy": "text:<task>", "title": "Review"},
        {"tool": "claude", "id": "c", "automated": True, "isSubagent": True, "title": "sub"},
        {"tool": "claude", "id": "d", "automated": False, "title": "mine"},
    ]
    assert [(s["id"], rule) for s, rule in hidden_by_text_rules(sessions)] == [("b", "<task>")]


def test_a_cli_that_fails_to_start_is_not_a_user_exit(tmp_path):
    """`claude --resume` failing (rc=1, e.g. "No conversation found") must not drop the session to `Recent` or move
    the user's view; a background retry loop did that every few seconds."""
    marker_dir, ws_path, patches = _exit_env(tmp_path)
    try:
        from mixhub.tui import selection

        assert tui.handle_cli_on_exit("claude", "s1", "w-claude-s1", 1, working_set_path=str(ws_path)) == 0
        assert "claude:s1" in json.loads(ws_path.read_text(encoding="utf-8"))["sessions"]
        selection.write_active_selection.assert_not_called()
    finally:
        for p in patches:
            p.stop()


def test_a_background_window_exiting_does_not_move_the_users_view(tmp_path):
    """`/exit` in a window the user is not looking at still drops it to `Recent`, but the selection stays put."""
    marker_dir, ws_path, patches = _exit_env(tmp_path)
    try:
        from mixhub.tui import selection

        # The mocked `tmux display-message` prints nothing: the window is not the active one
        assert tui.handle_cli_on_exit("claude", "s1", "w-claude-s1", 0, working_set_path=str(ws_path)) == 0
        assert "claude:s1" not in json.loads(ws_path.read_text(encoding="utf-8"))["sessions"]
        selection.write_active_selection.assert_not_called()
    finally:
        for p in patches:
            p.stop()


def test_the_users_own_exit_still_shows_the_session_preview(tmp_path):
    marker_dir, ws_path, patches = _exit_env(tmp_path)
    try:
        from mixhub.tui import selection

        tui.subprocess.run.return_value = SimpleNamespace(stdout="1\n", returncode=0)
        assert tui.handle_cli_on_exit("claude", "s1", "w-claude-s1", 0, working_set_path=str(ws_path)) == 0
        selection.write_active_selection.assert_called_once()
    finally:
        for p in patches:
            p.stop()


def test_claude_resumes_from_the_folder_its_transcript_belongs_to(tmp_path):
    """Claude finds a transcript by the folder it runs in, so a session whose cwd moved after it started must be resumed
    from the folder it started in, not the last one."""
    from mixhub.hub.shell import resume_cwd

    start, moved = tmp_path / "start", tmp_path / "start" / ".notes" / "memory"
    moved.mkdir(parents=True)
    proj = tmp_path / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(start))
    proj.mkdir(parents=True)
    path = proj / "s1.jsonl"
    path.write_text("\n".join(json.dumps({"type": "user", "cwd": c}) for c in (str(start), str(moved))), encoding="utf-8")

    assert resume_cwd({"tool": "claude", "id": "s1", "cwd": str(moved), "path": str(path)}) == str(start)
    assert resume_cwd({"tool": "claude", "id": "s1", "cwd": str(start), "path": str(path)}) == str(start)
    assert resume_cwd({"tool": "codex", "id": "s1", "cwd": str(moved)}) == str(moved)


def test_claude_quota_going_back_up_in_the_same_window_is_taken_from_a_live_session(tmp_path):
    """A reset (e.g. a usage reset) raises the remaining share inside the same window. A session that just talked to
    the API reports it and wins; an idle window re-rendering its old numbers neither lowers it nor looks fresh."""
    from mixhub.providers.statusline import claude_windows, merge_windows

    now = 1_000_000.0
    reset = now + 3 * 86400
    live_tx, idle_tx = tmp_path / "live.jsonl", tmp_path / "idle.jsonl"
    live_tx.write_text("{}\n", encoding="utf-8")
    idle_tx.write_text("{}\n", encoding="utf-8")
    os.utime(live_tx, (now - 5, now - 5))
    os.utime(idle_tx, (now - 3600, now - 3600))

    def status(used, tx):
        return {"transcript_path": str(tx), "rate_limits": {"seven_day": {"used_percentage": used, "resets_at": reset}}}

    old = {"claude-weekly": {"remaining_fraction": 0.38, "reset_time": reset, "observed_at": now - 600, "started": True}}
    after_reset = merge_windows(old, claude_windows(status(13, live_tx), now))["claude-weekly"]
    assert after_reset["remaining_fraction"] == 0.87 and after_reset["observed_at"] == now

    stale = merge_windows({"claude-weekly": after_reset}, claude_windows(status(62, idle_tx), now + 30))["claude-weekly"]
    assert stale["remaining_fraction"] == 0.87 and stale["observed_at"] == now


def test_a_clearly_newer_claude_quota_observation_wins_in_the_same_window():
    from mixhub.providers.quota import QuotaObs, merge_quota_obs

    reset = 2_000_000.0
    statusline = QuotaObs(0.38, reset, 1_000_000.0, True)
    fetched = QuotaObs(0.87, reset, 1_000_000.0 + 1800, True)  # `cachedUsageUtilization` fetched half an hour later
    assert merge_quota_obs(statusline, fetched).rem == 0.87
    assert merge_quota_obs(fetched, statusline).rem == 0.87
