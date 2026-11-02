"""The `Hub` facade object: local, read-only session discovery with explicit terminal actions.

`Hub` composes one mixin per concern (state, transcripts, processes, pane probes, discovery, metrics,
listing, actions, lifecycle, sleep blockers, integrations); `__init__` owns the shared instance state.
"""
from __future__ import annotations

import os
import threading

from mixhub.hub import constants
from mixhub.hub.actions import ActionsMixin
from mixhub.hub.discovery import DiscoveryMixin
from mixhub.hub.index import SessionIndex
from mixhub.hub.integrations import IntegrationsMixin
from mixhub.hub.lifecycle import LifecycleMixin
from mixhub.hub.listing import ListingMixin
from mixhub.hub.metrics import SystemMetricsMixin
from mixhub.hub.pane_probe import PaneProbeMixin
from mixhub.hub.processes import ProcessMixin
from mixhub.hub.sleep_blockers import SleepBlockersMixin
from mixhub.hub.summary import TranscriptMixin
from mixhub.hub.working_set import WorkingSetMixin


# Never leak internal automation beacon into user sessions spawned by Hub.
os.environ.pop("MIXHUB_INTERNAL", None)


class Hub(
    WorkingSetMixin,
    TranscriptMixin,
    ProcessMixin,
    PaneProbeMixin,
    DiscoveryMixin,
    SystemMetricsMixin,
    ListingMixin,
    ActionsMixin,
    LifecycleMixin,
    SleepBlockersMixin,
    IntegrationsMixin,
):
    def __init__(self, home=None, root=None, readonly_cache: bool = False):
        self.home = home or os.path.expanduser("~")
        self.root = root or constants.ROOT
        self.readonly_cache = bool(readonly_cache)
        self.state_path = os.path.join(self.root, "hub_state.json")
        self.working_set_path = os.path.join(self.root, "active_working_set.json")
        self.disk_cache_path = os.path.join(self.root, "data", "index_cache.json")
        self.cache = {}
        self.cache_dirty = False
        self._db_rows_cache: dict[tuple[str, str], list[dict]] = {}
        self._db_sig_cache: dict[tuple[str, str], tuple] = {}
        self._db_fail_counts: dict[tuple[str, str], int] = {}
        self._session_db_meta: dict[str, dict] = {}
        self._codex_row_memo: dict[str, tuple[tuple, dict]] = {}
        self._agy_row_memo: dict[str, tuple[tuple, dict]] = {}
        self._codex_index_cache: tuple = (None, [])
        self._subagent_links_cache: dict[str, tuple[tuple[int, int], list[str]]] = {}
        self._claude_index_cache: dict[str, tuple[int, dict[str, dict]]] = {}
        self._last_proc_table: dict = {}
        self._last_subagent_pids: dict[int, str] = {}
        self._known_agy_transcripts: dict[str, str] = {}
        self._agy_brain_sig: tuple | None = None
        self._agy_tui_history: tuple = (None, None)
        self._agy_missing_uids: set[str] = set()
        self._codex_glob_cache: tuple = (None, [], {})
        self._cold_unlocked_locks: dict[str, tuple[float, tuple | None, set[str]]] = {}
        self._pane_probe_cache: dict[str, dict] = {}
        self._pane_probe_last_sched: float = 0.0
        self._pane_probe_running: bool = False
        self._pane_probe_done_at: float = 0.0  # monotonic end of the last probe pass, background or not
        self.pane_probe_fn = None
        self._load_disk_cache()
        self.sessions = SessionIndex()
        self.scanned = 0
        self._proc_scanned = 0
        self._proc_cache = []
        self._files_cache = {}
        self._proc_snapshot: tuple = ([], {}, set())
        self._bg_proc_scan: dict | None = None
        self.proc_snapshot_seq = 0
        self._proc_snapshot_cond = threading.Condition()
        self._pid_lsof_sig: dict[int, tuple] = {}
        self.lock = threading.RLock()
        self.warnings = []


get_system_resource_metrics = Hub.get_system_resource_metrics
