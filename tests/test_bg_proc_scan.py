"""Sidebar input must never wait on `ps` / `lsof`: process scans run on a background thread."""
import threading
import time
from unittest.mock import patch

import server


def _slow_processes(delay, procs_box):
    def fake(self):
        time.sleep(delay)
        self._last_proc_table = {}
        self._last_subagent_pids = {}
        return list(procs_box[0])
    return fake


def _proc(pid):
    return {"pid": pid, "start_time": "t", "args": ["claude"], "tty": "ttys001", "rss_mb": 1.0}


def test_discover_does_not_block_on_process_scan_once_a_snapshot_exists(tmp_path):
    root = tmp_path / "hub"
    root.mkdir()
    hub = server.Hub(str(tmp_path), str(root))
    procs_box = [[]]
    with patch.object(server.Hub, "processes", _slow_processes(0.4, procs_box)), \
         patch.object(server.Hub, "open_files", lambda self, p: {}):
        hub.start_background_proc_scan(interval=60.0)
        try:
            assert hub.wait_proc_snapshot(seq=0, timeout=3.0)  # first snapshot
            seq = hub.proc_snapshot_seq
            procs_box[0] = [_proc(4242)]
            hub._proc_scanned = 0  # what the sidebar does to force a rescan
            t0 = time.monotonic()
            hub.discover()
            assert time.monotonic() - t0 < 0.2, "discover() waited for the slow process scan"
            assert hub.wait_proc_snapshot(seq=seq, timeout=3.0)  # rescan landed in the background
            assert [p["pid"] for p in hub._proc_cache] == [4242]
        finally:
            hub.stop_background_proc_scan()


def test_background_scanner_stops_cleanly(tmp_path):
    root = tmp_path / "hub"
    root.mkdir()
    hub = server.Hub(str(tmp_path), str(root))
    with patch.object(server.Hub, "processes", lambda self: []), \
         patch.object(server.Hub, "open_files", lambda self, p: {}):
        hub.start_background_proc_scan(interval=60.0)
        hub.stop_background_proc_scan()
    assert not any(t.name == "mixhub-proc-scan" and t.is_alive() for t in threading.enumerate())
