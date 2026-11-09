"""Leftovers between a CLI and MixHub: hibernate markers, the dim prompt suggestion, a takeover racing the old process,
and the hint for a session still on an older Claude Code."""
import json
import os
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
