#!/usr/bin/env python3
"""`mixhub.tui.app`: `HubTUI`, composed from the `mixhub.tui.hubtui` mixins, and the `tui.py` entry point (`main`).

The same class runs as the sidebar (`--sidebar`), the reader (`--reader`) or a `hubpool` window's `--header`;
`main` also dispatches `--on-exit`, `--name` and `--workspace`.
"""
from __future__ import annotations

try:
    import curses
except ImportError:  # Windows without windows-curses installed
    curses = None
import os
import subprocess
import sys
import time

from mixhub.hub.hub import Hub
from mixhub.tmux.gateway import DEFAULT_TMUX_GATEWAY, TmuxGateway
from mixhub.tmux.onexit import handle_cli_on_exit
from mixhub.tmux.workspace import launch_tmux_workspace
from mixhub.tui import caffeinate as _caffeinate
from mixhub.tui import paths as _paths
from mixhub.tui import quota as _quota
from mixhub.tui import selection as _selection
from mixhub.tui.core import DEFAULT_THEME, HitMap, InputDecoder, Theme
from mixhub.tui.hubtui.actions import SessionActionsMixin
from mixhub.tui.hubtui.attention import AttentionMixin
from mixhub.tui.hubtui.colors import ColorsMixin
from mixhub.tui.hubtui.draw import DrawMixin
from mixhub.tui.hubtui.footer import FooterMixin
from mixhub.tui.hubtui.header import HeaderMixin
from mixhub.tui.hubtui.home import HomeMixin
from mixhub.tui.hubtui.layout import PaneLayoutMixin
from mixhub.tui.hubtui.loop import EventLoopMixin
from mixhub.tui.hubtui.mouse import MouseMixin
from mixhub.tui.hubtui.navigation import NavigationMixin
from mixhub.tui.hubtui.popovers import PopoversMixin
from mixhub.tui.hubtui.prompt import PromptInputMixin
from mixhub.tui.hubtui.reader import ReaderMixin
from mixhub.tui.hubtui.refresh import RefreshMixin
from mixhub.tui.hubtui.relay import RelayMixin
from mixhub.tui.hubtui.selection_sync import SelectionSyncMixin
from mixhub.tui.hubtui.sidebar import SidebarListMixin
from mixhub.tui.hubtui.wordmark_row import WordmarkRowMixin
from mixhub.core.prefs import preferences_path
from mixhub.tui.widgets import PopoverManager
from mixhub.tui.widgets.wordmark import Wordmark


class HubTUI(
    AttentionMixin,
    ColorsMixin,
    SelectionSyncMixin,
    SidebarListMixin,
    RefreshMixin,
    HeaderMixin,
    PopoversMixin,
    WordmarkRowMixin,
    NavigationMixin,
    PaneLayoutMixin,
    FooterMixin,
    HomeMixin,
    ReaderMixin,
    RelayMixin,
    PromptInputMixin,
    SessionActionsMixin,
    DrawMixin,
    MouseMixin,
    EventLoopMixin,
):
    def __init__(
        self,
        stdscr,
        sidebar_only: bool = False,
        reader_only: bool = False,
        hub_instance=None,
        caf=None,
        initial_selected_id=None,
        seen_path: str | None = None,
        header_only: bool = False,
        header_target: tuple[str, str] | None = None,
    ):
        self.stdscr = stdscr
        self.sidebar_only = sidebar_only
        self.reader_only = reader_only
        self.header_only = header_only
        self.header_target = header_target
        self.hub = hub_instance or Hub(readonly_cache=header_only)
        self.state_root = os.path.abspath(getattr(self.hub, "root", _paths.ROOT))
        self._isolated_state = self.state_root != os.path.abspath(_paths.ROOT)
        self.caf = caf or _caffeinate.CaffeinateController()
        # Resolved at call time (not as a default arg) so tests can redirect SEEN_PATH
        self.seen_path = seen_path or (os.path.join(self.state_root, "data", "seen.json")
                                       if self._isolated_state else _paths.SEEN_PATH)
        self.unsent_drafts_path = (os.path.join(self.state_root, "data", "unsent_drafts.json")
                                  if self._isolated_state else _paths.UNSENT_DRAFTS_PATH)
        # Only `--sidebar` (or the standalone TUI) tracks `attn` transitions; `--reader` / `--header` hold stale
        # session snapshots, so they only contribute `seen` marks and adopt the owner's `attn` map.
        self._owns_seen_attn = not (reader_only or header_only)
        self._seen_mtime_ns = 0
        self.tool_filter = "all"
        self._provider_filter: str | None = None
        self.provider_dropdown_open: bool = False
        self._popover_opened_mono: float = 0.0
        self._popover_focus_probe_mono: float = 0.0
        self.query = ""
        self.show_temp = False
        self.expand_tools = False
        self.sidebar_collapsed = reader_only or header_only
        self._mini_rail = False
        self.selected_idx = -1
        self.hover_idx = -1
        self.hover_pos = (-1, -1)
        self._last_hover_ts: float = 0.0
        self.custom_left_w = None
        self._dragging_divider = False
        self.list_scroll = 0
        self.msg_scroll = 999999
        self.tail_step_idx = 0  # Index into TAIL_STEPS (0 = 128KB)
        self.is_tail_truncated = False
        self.status_msg = ""
        self.sessions = []
        self.counts = {}
        self.loaded_key = None
        self.rendered_lines = []
        self.user_turns = []  # list of (line_idx, single_line_prompt)
        self.render_cache = {}  # LRU dict: key -> (lines, user_turns, is_tail_truncated)
        self._last_sel_mtime = 0.0
        self._last_transcript_sig = None
        self._last_row_click = ("", 0.0)   # (session_id, monotonic_ts) for 400ms double-click enter
        self._last_wheel_ts: float = 0.0   # monotonic_ts of last wheel event to debounce trackpad post-scroll tap-to-click
        self._relay_menu: bool = False
        # 2-level Relay: "CLOSED" | "PICK_AGENT" | "PICK_CLAUDE_BACKEND" (`_relay_menu` mirrors `!= "CLOSED"`)
        self._relay_state: str = "CLOSED"
        self._relay_anchor_id: str | None = None
        self._relay_hits: dict[int, list[tuple[int, int, str]]] = {}
        self._new_session_menu: bool = False
        self._preview_action_zones: list[tuple[int, int, int, str, bool]] = []
        self._preview_focus_idx: int = 0
        self._preview_focus_anchor: tuple[str, str, bool] | None = None
        self._preview_flash_until: float = 0.0
        self._preview_pulse_until: float = 0.0
        self._preview_hint_until: float = 0.0
        self._last_cancelled_input: str = ""
        self._prev_agent_states: dict[str, tuple[str, float]] = {}
        self._last_ext_tty_poll: float = 0.0
        self._last_gc_poll: float = time.monotonic()
        self._start_mono = time.monotonic()
        self._last_reader_verify_ts: float = time.monotonic()
        self.sidebar_group_mode = "status"  # "status" (Pinned / Active / Recent) or "workspace" (Pinned + project folder like herdr)
        self._collapsed_sections: set[str] = set()
        self.ephemeral_toast: tuple[str, float] | None = None
        self.toast_dismissed_permanently: bool = False
        self.toast_feedback: str = ""
        self._seen_state = self._load_seen_state()
        # Modular TUI primitives (TmuxGateway, Theme, HitMap, PopoverManager, InputDecoder)
        self.tmux: TmuxGateway = DEFAULT_TMUX_GATEWAY
        self.theme: Theme = DEFAULT_THEME
        self.hitmap: HitMap = HitMap()
        self.popover_manager: PopoverManager = PopoverManager(on_focus_request=self._focus_sidebar_pane_async)
        self.input_decoder: InputDecoder = InputDecoder()
        # Top-left `mixhub` wordmark micro-interaction (0 token, mouse only, 0 wakeups while static)
        self.wordmark = Wordmark(prefs_path=os.path.join(self.state_root, "Preferences")
                                if self._isolated_state else preferences_path())
        # Navigation history (`‹  ›` on row 0); finalized below once the initial selection is resolved
        self._wordmark_home_nav_ts: float = -10.0
        self.nav_history: list[str] = ["__home__"]
        self.nav_pos: int = 0
        self._last_nav_was_key: bool = False
        self._force_full_repaint: bool = False
        self._last_mouse_was_click: bool = False
        self._dirty = True
        self._init_colors()
        if not (self.header_only or self.reader_only):
            self._get_cached_system_metrics(time.monotonic())
            try:
                _quota.get_cli_quota_metrics(home_dir=getattr(self.hub, "home", None))
            except Exception:
                pass
        if self.header_only and self.header_target and not self.sessions:
            ht_tool, ht_id = self.header_target
            if hasattr(self.hub, "get_lightweight_session_meta"):
                meta = self.hub.get_lightweight_session_meta(ht_tool, ht_id)
                if isinstance(meta, dict) and meta.get("id"):
                    self.sessions = [meta]
                    self.selected_idx = 0
                    self.counts = {"working": 0, "live": 1}
        startup_sel = self._read_active_selection() if (sidebar_only or reader_only) else None
        if not initial_selected_id and startup_sel and not startup_sel.get("empty"):
            initial_selected_id = startup_sel.get("id")
        if self.reader_only and not self.header_only:
            if initial_selected_id:
                self._adopt_reader_selection(dict(startup_sel or {}, id=initial_selected_id))
        elif not self.sessions:
            self.refresh_data(force=True, reorder=True)
        if header_target:
            ht_tool, ht_id = header_target
            ht_id = (ht_id or "").strip()
            ht_tool = (ht_tool or "").strip()
            if ht_id:
                exact = [
                    idx for idx, s in enumerate(self.sessions)
                    if s["id"] == ht_id and (not ht_tool or s["tool"] == ht_tool)
                ]
                if not exact and ht_tool:
                    exact = [idx for idx, s in enumerate(self.sessions) if s["id"] == ht_id]
                if exact:
                    self.selected_idx = exact[0]
                else:
                    pref = [
                        idx for idx, s in enumerate(self.sessions)
                        if s["id"].startswith(ht_id) and (not ht_tool or s["tool"] == ht_tool)
                    ]
                    if not pref and ht_tool:
                        pref = [idx for idx, s in enumerate(self.sessions) if s["id"].startswith(ht_id)]
                    if len(pref) == 1 or (pref and ht_tool):
                        self.selected_idx = pref[0]
            else:
                self.header_target = None
        elif initial_selected_id:
            for idx, s in enumerate(self.sessions):
                if s["id"] == initial_selected_id or s["id"].startswith(initial_selected_id):
                    self.selected_idx = idx
                    break
        # Cold start: the wordmark Easter egg only lives on Home; force static `mixhub` otherwise
        if self.selected_idx != -1 or self.sidebar_only:
            self.wordmark.set_mode("off")
        cur_init = self.current_session()
        self.nav_history = [cur_init["id"] if cur_init else "__home__"]
        self.nav_pos = 0
        self._load_persisted_nav_history(cur_init["id"] if cur_init else "__home__")
        if self.current_session():
            self._mark_session_seen(self.current_session())
        if self.sidebar_only:
            if not self._allow_tmux_side_effects():
                self._write_active_selection(self.current_session(), activate_reader=True)
            else:
                self._on_sidebar_selection_changed(activate_live=False)


def parse_header_args(args: list[str]) -> tuple[bool, tuple[str, str] | None]:
    if "--header" not in args:
        return False, None
    idx = args.index("--header")
    pos = [a for a in args[idx + 1 :] if a and not a.startswith("-")]
    if len(pos) >= 2:
        return True, (pos[0], pos[1])
    if len(pos) == 1:
        return True, ("", pos[0])
    return True, None


def main():
    args = list(sys.argv[1:])
    if "--on-exit" in args:
        idx = args.index("--on-exit")
        tool = args[idx + 1] if idx + 1 < len(args) else ""
        sid = args[idx + 2] if idx + 2 < len(args) else ""
        wname = args[idx + 3] if idx + 3 < len(args) else ""
        try:
            rc = int(args[idx + 4]) if idx + 4 < len(args) else 0
        except ValueError:
            rc = 0
        handle_cli_on_exit(tool, sid, wname, rc)
        return
    if "--workspace" in args or "workspace" in args:
        launch_tmux_workspace()
        return

    sidebar_only = "--sidebar" in args or "sidebar" in args
    reader_only = "--reader" in args or "reader" in args
    header_only, header_target = parse_header_args(args)
    if not (sidebar_only or reader_only or header_only):
        launch_tmux_workspace()
        return
    hub_instance = Hub(readonly_cache=header_only)
    if sidebar_only:
        # `ps`/`lsof` take 130ms+ (seconds under swap): keep them off the input loop
        hub_instance.start_background_proc_scan()
    caf = _caffeinate.CaffeinateController()
    last_selected_id = header_target[1] if header_target else None
    if sidebar_only and not last_selected_id:
        sel = _selection.read_active_selection()
        if sel and sel.get("id") and not sel.get("empty"):
            last_selected_id = str(sel["id"])
        else:
            try:
                env_nt = dict(os.environ)
                env_nt.pop("TMUX", None)
                w_out = subprocess.run(
                    ["tmux", "-L", "hubpool", "display-message", "-p", "-t", "pool", "#{window_name}"],
                    capture_output=True, text=True, env=env_nt
                ).stdout.strip()
                if w_out.startswith("w-"):
                    parts = w_out.split("-", 2)
                    if len(parts) == 3 and parts[2]:
                        last_selected_id = parts[2]
            except Exception:
                pass

    try:
        def _wrapped(stdscr):
            HubTUI(
                stdscr,
                sidebar_only=sidebar_only,
                reader_only=reader_only,
                hub_instance=hub_instance,
                caf=caf,
                initial_selected_id=last_selected_id,
                header_only=header_only,
                header_target=header_target,
            ).run()

        curses.wrapper(_wrapped)
    finally:
        caf.close()
