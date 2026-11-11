#!/usr/bin/env python3
"""`mixhub.tmux.workspace`: Outer dual-pane workspace: session name allocation, layout, mouse / copy bindings,
and the keep-alive hint printed on detach.
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys

from mixhub.core.launch import code_command
from mixhub.core.textwidth import disp_trunc
from mixhub.i18n import t
from mixhub.tmux import commands as _commands
from mixhub.tmux import outer as _outer
from mixhub.hub.shell import resume_cwd
from mixhub.tmux.commands import interactive_resume_cmd
from mixhub.tmux.outer import record_outer_window
from mixhub.tui.settings import IS_MAC
from mixhub.tmux.gateway import OUTER_SOCKET


def clipboard_copy_cmd() -> str:
    """Return platform-appropriate clipboard copy command for tmux MouseDragEnd1Pane."""
    if IS_MAC:
        return "pbcopy"
    if shutil.which("clip.exe"):
        # clip.exe reads stdin in the console code page, so UTF-8 CJK arrives garbled; UTF-16LE survives.
        return "iconv -f utf-8 -t utf-16le | clip.exe" if shutil.which("iconv") else "clip.exe"
    if shutil.which("wl-copy"):
        return "wl-copy"
    return "xclip -selection clipboard"


def _sanitize_tmux_terminal_overrides(base: list[str], env: dict | None = None) -> list[int]:
    """
    Remove corrupted `terminal-overrides[N]` server options containing `am@`, `smcup`, `rmcup`, or double-escaped `\\\\E`
    so `tmux` never disables right-margin guards or sends broken alternate-screen sequences to `Apple_Terminal`.
    """
    removed: list[int] = []
    try:
        res = subprocess.run(
            [*base, "show-options", "-s", "terminal-overrides"],
            capture_output=True,
            text=True,
            env=env,
        )
        raw = res.stdout if isinstance(getattr(res, "stdout", None), str) else ""
        bad_indices: list[int] = []
        for ln in raw.splitlines():
            m = re.match(r"^\s*terminal-overrides\[(\d+)\]\s+(.*)$", ln)
            if not m:
                continue
            idx = int(m.group(1))
            val = m.group(2)
            if "am@" in val or "smcup" in val or "rmcup" in val or "\\E" in val:
                bad_indices.append(idx)
        for idx in sorted(set(bad_indices), reverse=True):
            subprocess.run(
                [*base, "set-option", "-su", f"terminal-overrides[{idx}]"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
            )
            removed.append(idx)
    except Exception:
        pass
    return removed


def configure_tmux_mouse_and_copy(sock_name: str | None, env: dict | None = None, show_status: bool = False, session_target: str | None = None):
    """Configure mouse scrollback, fixed pane borders, and cross-platform drag-to-clipboard on a tmux server
    (`sock_name=None`: the outer workspace server)."""
    base = ["tmux", "-L", sock_name or OUTER_SOCKET]
    _sanitize_tmux_terminal_overrides(base, env=env)
    t_args = ["-t", session_target] if session_target else ["-g"]
    copy_cmd = clipboard_copy_cmd()
    # Outer `hub` (sock_name is None) forwards MouseDrag1Pane via `send-keys -M` without entering copy-mode
    # so double-layer tmux mouse drag never fights; inner `hubpool` handles `copy-mode -M`!
    if sock_name:
        drag_binding = [
            "bind-key", "-T", "root", "MouseDrag1Pane",
            "if-shell", "-F", "#{||:#{pane_in_mode},#{mouse_any_flag}}",
            "send-keys -M",
            "copy-mode -M",
        ]
        wheel_up_binding = [
            "bind-key", "-T", "root", "WheelUpPane",
            "if-shell", "-F", "-t=", "#{||:#{alternate_on},#{pane_in_mode},#{mouse_any_flag}}",
            "send-keys -t= -M",
            "copy-mode -e -t= ; send-keys -t= -M",
        ]
        wheel_down_binding = [
            "bind-key", "-T", "root", "WheelDownPane",
            "if-shell", "-F", "-t=", "#{||:#{alternate_on},#{pane_in_mode},#{mouse_any_flag}}",
            "send-keys -t= -M",
            "send-keys -t= -M",
        ]
    else:
        drag_binding = [
            "bind-key", "-T", "root", "MouseDrag1Pane",
            "if-shell", "-F", "#{pane_in_mode}",
            "send-keys -M",
            "send-keys -M",
        ]
        # Outer `hub` right pane (`#{pane_left} > 0`) runs `tmux -L hubpool attach`:
        # never enter outer copy-mode on `%1`; automatically select `%1` if inactive and forward wheel to `hubpool`.
        wheel_up_binding = [
            "bind-key", "-T", "root", "WheelUpPane",
            "if-shell", "-F", "-t=", "#{&&:#{pane_left},#{==:#{pane_active},0}}",
            "select-pane -t= ; send-keys -t= -M",
            "send-keys -t= -M",
        ]
        wheel_down_binding = [
            "bind-key", "-T", "root", "WheelDownPane",
            "if-shell", "-F", "-t=", "#{&&:#{pane_left},#{==:#{pane_active},0}}",
            "select-pane -t= ; send-keys -t= -M",
            "send-keys -t= -M",
        ]
    cmds = [
        ["set-option", "-s", "exit-unattached", "off"],
        ["set-option", "-g", "destroy-unattached", "off"],
        ["set-option", "-g", "window-size", "latest"],
        ["set-option", "-g", "aggressive-resize", "on"],
        ["set-window-option", "-g", "aggressive-resize", "on"],
        ["set-option", "-g", "set-titles", "on"],
        ["set-window-option", "-g", "allow-rename", "off"],
        ["set-window-option", "-g", "automatic-rename", "off"],
        ["set-option", "-g", "pane-border-style", "fg=colour237"],
        ["set-option", "-g", "pane-active-border-style", "fg=colour237"],
        ["set-option", "-g", "escape-time", "0"],
        ["set-option", "-g", "history-limit", "50000"],
        ["set-option", "-g", "mouse", "on"],
        ["set-option", "-g", "focus-events", "on"],
        ["set-option", "-g", "bell-action", "any"],
        ["set-option", "-g", "visual-bell", "off"],
        ["set-option", *t_args, "status", "on" if show_status else "off"],
        ["set-option", "-g", "mode-style", "bg=colour238,fg=colour255"],
        ["unbind-key", "-T", "root", "MouseDrag1Border"],
        ["bind-key", "-T", "copy-mode-vi", "MouseDown1Pane", "select-pane", ";", "send-keys", "-X", "cancel"],
        ["bind-key", "-T", "copy-mode", "MouseDown1Pane", "select-pane", ";", "send-keys", "-X", "cancel"],
        drag_binding,
        # Drag selection automatically copies to system clipboard without flickering
        ["bind-key", "-T", "copy-mode-vi", "MouseDragEnd1Pane", "send-keys", "-X", "copy-pipe-and-cancel", copy_cmd],
        ["bind-key", "-T", "copy-mode", "MouseDragEnd1Pane", "send-keys", "-X", "copy-pipe-and-cancel", copy_cmd],
        # Wheel up/down forwards SGR wheel events cleanly in split-pane workspace and enters copy-mode on inner CLI panes
        wheel_up_binding,
        wheel_down_binding,
    ]
    for c in cmds:
        subprocess.run([*base, *c], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)


def ensure_outer_dual_pane_layout(session_name: str = "hub", pool_session: str = "pool") -> None:
    """
    Ensure the `hub` (or `hub-N`) tmux session exists and has EXACTLY 2 panes:
      - Left pane (32 cols): `python -m mixhub.tui --sidebar`
      - Right pane (remaining width): `env -u TMUX tmux -L hubpool attach-session -t {pool_session}`
    Immune to `base-index 1` / `pane-base-index 1` in `~/.tmux.conf` by using `pane_id` (`%N`) and `{left}`/`{right}`.
    """
    sidebar_env = None
    if session_name != "hub" or pool_session != "pool":
        sidebar_env = {"HUB_OUTER_SESSION": session_name, "HUB_POOL_SESSION": pool_session}
    sidebar_cmd = _commands._sidebar_cmd(sidebar_env)
    reader_cmd = _commands._reader_cmd()
    right_cmd = f"exec env -u TMUX tmux -L hubpool attach-session -t {shlex.quote(pool_session)}" if pool_session != "pool" else "exec env -u TMUX tmux -L hubpool attach-session -t pool"

    term_sz = shutil.get_terminal_size((160, 48))
    term_cols = max(80, term_sz.columns)
    term_lines = max(24, term_sz.lines)
    right_cols = max(48, term_cols - 33)

    pool_env = dict(os.environ)
    pool_env.pop("TMUX", None)
    has_pool = subprocess.run(
        ["tmux", "-L", "hubpool", "has-session", "-t", "pool"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=pool_env,
    ).returncode == 0
    if not has_pool:
        subprocess.run(
            ["tmux", "-L", "hubpool", "new-session", "-d", "-s", "pool", "-x", str(right_cols), "-y", str(term_lines), "-n", "reader", reader_cmd],
            check=False,
            env=pool_env,
        )
    if pool_session != "pool":
        has_grouped = subprocess.run(
            ["tmux", "-L", "hubpool", "has-session", "-t", pool_session],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=pool_env,
        ).returncode == 0
        if not has_grouped:
            subprocess.run(
                ["tmux", "-L", "hubpool", "new-session", "-d", "-t", "pool", "-s", pool_session],
                check=False,
                env=pool_env,
            )
        subprocess.run(
            ["tmux", "-L", "hubpool", "set-hook", "-t", pool_session, "client-detached", f"kill-session -t {pool_session}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=pool_env,
        )
    configure_tmux_mouse_and_copy("hubpool", env=pool_env, show_status=False)

    has_session = subprocess.run(
        ["tmux", "-L", OUTER_SOCKET, "has-session", "-t", session_name],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=pool_env,
    ).returncode == 0

    if not has_session:
        subprocess.run(
            ["tmux", "-L", OUTER_SOCKET, "new-session", "-d", "-s", session_name, "-x", str(term_cols), "-y", str(term_lines), sidebar_cmd],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=pool_env,
        )
    if session_name != "hub":
        subprocess.run(
            ["tmux", "-L", OUTER_SOCKET, "set-hook", "-t", session_name, "client-detached", f"kill-session -t {session_name}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=pool_env,
        )

    # Inspect current panes of `hub` using `#{pane_id}` so `base-index` / `pane-base-index` never matter
    panes_raw = subprocess.run(
        ["tmux", "-L", OUTER_SOCKET, "list-panes", "-t", session_name, "-F", "#{pane_id}|#{pane_left}|#{pane_current_command}"],
        capture_output=True,
        text=True,
        env=pool_env,
    ).stdout.strip().splitlines()

    if len(panes_raw) == 1:
        pane_id, _, p_cmd = panes_raw[0].split("|", 2)
        if p_cmd == "tmux":
            subprocess.run(
                ["tmux", "-L", OUTER_SOCKET, "split-window", "-h", "-b", "-l", "32", "-t", pane_id, sidebar_cmd],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=pool_env,
            )
        else:
            subprocess.run(
                ["tmux", "-L", OUTER_SOCKET, "split-window", "-h", "-l", str(right_cols), "-t", pane_id, right_cmd],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=pool_env,
            )
            subprocess.run(
                ["tmux", "-L", OUTER_SOCKET, "resize-pane", "-t", pane_id, "-x", "32"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=pool_env,
            )
        panes_raw = subprocess.run(
            ["tmux", "-L", OUTER_SOCKET, "list-panes", "-t", session_name, "-F", "#{pane_id}|#{pane_left}|#{pane_current_command}"],
            capture_output=True,
            text=True,
            env=pool_env,
        ).stdout.strip().splitlines()

    left_pane_id, right_pane_id = _outer.get_outer_hub_pane_ids(session_name)
    if left_pane_id:
        subprocess.run(
            ["tmux", "-L", OUTER_SOCKET, "resize-pane", "-t", left_pane_id, "-x", "32"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=pool_env,
        )
        subprocess.run(
            ["tmux", "-L", OUTER_SOCKET, "select-pane", "-t", left_pane_id],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=pool_env,
        )

    configure_tmux_mouse_and_copy(None, env=pool_env, show_status=False, session_target=session_name)
    left_ref = left_pane_id or "{left}"
    subprocess.run(
        ["tmux", "-L", OUTER_SOCKET, "set-window-option", "-t", session_name, "main-pane-width", "32"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=pool_env,
    )
    subprocess.run(
        ["tmux", "-L", OUTER_SOCKET, "select-layout", "-t", session_name, "main-vertical"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=pool_env,
    )
    resize_hook_cmd = (
        f"if-shell -F '#{{==:#{{window_zoomed_flag}},0}}' "
        f"'set-window-option -t {session_name} main-pane-width 32 ; "
        f"select-layout -t {session_name} main-vertical ; refresh-client -S'"
    )
    win_resize_hook = resize_hook_cmd
    cli_resize_hook = resize_hook_cmd
    subprocess.run(
        ["tmux", "-L", OUTER_SOCKET, "set-hook", "-w", "-t", session_name, "window-resized", win_resize_hook],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=pool_env,
    )
    subprocess.run(
        ["tmux", "-L", OUTER_SOCKET, "set-hook", "-t", session_name, "client-resized", cli_resize_hook],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=pool_env,
    )
    left_target_cmd = [
        "if-shell", "-F", "#{window_zoomed_flag}",
        f"resize-pane -Z ; select-pane -t {left_ref}",
        f"select-pane -t {left_ref}",
    ]
    right_target_cmd = ["select-pane", "-t", right_pane_id] if right_pane_id else ["select-pane", "-R"]
    toggle_py_cmd = shlex.join(code_command(
        "import sys; from mixhub.tmux.outer import toggle_outer_sidebar_zoom; toggle_outer_sidebar_zoom(sys.argv[1])",
        session_name,
    ))
    toggle_sidebar_tmux_cmd = ["run-shell", "-b", toggle_py_cmd]
    for opt in (
        ["set-option", "-t", session_name, "status", "off"],
        ["set-option", "-t", session_name, "status-position", "top"],
        ["set-option", "-t", session_name, "status-style", "bg=default,fg=colour242"],
        ["set-option", "-t", session_name, "window-status-format", ""],
        ["set-option", "-t", session_name, "window-status-current-format", ""],
        ["set-option", "-t", session_name, "pane-border-status", "off"],
        ["set-option", "-t", session_name, "status-left-length", "24"],
        ["set-option", "-t", session_name, "status-right-length", "12"],
        ["set-option", "-t", session_name, "status-left", ""],
        [
            "set-option", "-t", session_name, "status-right",
            " #[range=user|quit_hub]#[fg=colour244]✕#[default]#[norange] ",
        ],
        ["set-option", "-t", session_name, "pane-border-style", "fg=colour237"],
        ["set-option", "-t", session_name, "pane-active-border-style", "fg=colour242"],
        [
            "bind-key", "-T", "root", "MouseDown1StatusLeft",
            *toggle_sidebar_tmux_cmd,
        ],
        ["bind-key", "-T", "root", "MouseDown1StatusRight", "kill-session", "-t", session_name],
        ["bind-key", "-T", "root", "MouseDown1Status", *left_target_cmd],
        ["bind-key", "-n", "C-\\", *left_target_cmd],
        ["bind-key", "-n", "C-g", *left_target_cmd],
        ["unbind-key", "-n", "M-Left"],
        ["bind-key", "-n", "M-Right", *right_target_cmd],
        ["bind-key", "-n", "C-]", *toggle_sidebar_tmux_cmd],
        ["bind-key", "-n", "M-b", *toggle_sidebar_tmux_cmd],
        ["bind-key", "-n", "∫", *toggle_sidebar_tmux_cmd],
    ):
        subprocess.run(["tmux", "-L", OUTER_SOCKET, *opt], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=pool_env)
    _outer.set_outer_client_autowrap(session_name, enabled=False)


def format_exit_keepalive_hint(hub_instance=None, pool_windows: list[str] | None = None) -> str:
    """
    Build a concise 2~4 line dim-gray Exit Receipt when leaving MixHub (`✕` or `q`).
    Strictly distinguishes:
      - Sessions STILL ALIVE in `hubpool`: show `hub` (or `tmux -L hubpool attach -t pool:<wname>`),
        NEVER `--resume` (which would collide with the live lock/process!).
      - Hibernated / non-resident sessions: show `cd <cwd> && <cli> --resume <id>`.
    """
    if pool_windows is None:
        pool_env = dict(os.environ)
        pool_env.pop("TMUX", None)
        try:
            res = subprocess.run(
                ["tmux", "-L", "hubpool", "list-windows", "-t", "pool", "-F", "#{window_name}"],
                capture_output=True,
                text=True,
                env=pool_env,
            )
            raw_out = res.stdout if isinstance(getattr(res, "stdout", None), str) else ""
            pool_windows = [w.strip() for w in raw_out.splitlines() if w.strip().startswith("w-")]
        except Exception:
            pool_windows = []

    sessions_list = []
    if hub_instance is not None:
        try:
            if not getattr(hub_instance, "sessions", None):
                hub_instance.scan_all(force=False, fast_only=True)
            sessions_list = sorted(
                hub_instance.sessions.values(),
                key=lambda x: x.get("updatedAt", 0),
                reverse=True,
            )
        except Exception:
            sessions_list = []

    live_win_set = set(pool_windows or [])
    dim = "\033[38;5;242m"
    acc = "\033[38;5;108m"
    cmd_col = "\033[38;5;248m"
    rst = "\033[0m"
    hub_cmd = "mixhub"

    lines = []
    if live_win_set:
        lines.append(
            dim + t("exit.kept", n=len(live_win_set), cmd=f"{acc}{hub_cmd}{dim}") + rst
        )
        shown_live = 0
        for wname in pool_windows:
            if shown_live >= 2:
                break
            lines.append(
                dim + t("exit.running", window=wname, cmd=f"{cmd_col}{hub_cmd}{dim}",
                        attach=f"{cmd_col}tmux -L hubpool attach -t pool:{wname}") + rst
            )
            shown_live += 1
    else:
        lines.append(dim + t("exit.closed", cmd=f"{acc}{hub_cmd}") + rst)

    for s in sessions_list:
        wname = f"w-{s.get('tool')}-{str(s.get('id', ''))[:12]}"
        if wname in live_win_set or s.get("isLive"):
            continue
        short_cwd = resume_cwd(s).replace(os.path.expanduser("~"), "~", 1)
        argv = interactive_resume_cmd(s)
        cmd_str = " ".join(shlex.quote(x) for x in argv)
        title = disp_trunc(s.get("title") or s.get("id", "")[:8], 26)
        lines.append(
            dim + t("exit.asleep", title=title, resume=f"{cmd_col}cd {shlex.quote(short_cwd)} && {cmd_str}") + rst
        )
        break

    return "\n".join(lines[:4])


def print_exit_keepalive_hint(hub_instance=None) -> None:
    try:
        msg = format_exit_keepalive_hint(hub_instance=hub_instance)
        from mixhub.cli.update import update_notice

        notice = update_notice()
        if notice:
            msg = (msg + "\n" if msg else "") + notice
        if msg:
            sys.stdout.write("\n" + msg + "\n")
            sys.stdout.flush()
    except Exception:
        pass


def allocate_workspace_session_names() -> tuple[str, str]:
    """
    Return `(outer_session_name, pool_session_name)` for `launch_tmux_workspace()`:
      - If `"hub"` has no attached clients, returns `("hub", "pool")`.
      - If `"hub"` already has an attached client in another Terminal window, allocates `("hub-2", "pool-view-2")`
        (or next free index) so multiple Terminal windows can view different sessions concurrently without fighting!
    """
    env = dict(os.environ)
    env.pop("TMUX", None)
    try:
        res = subprocess.run(
            ["tmux", "-L", OUTER_SOCKET, "list-clients", "-t", "hub", "-F", "#{client_name}"],
            capture_output=True,
            text=True,
            env=env,
        )
        clients = [ln.strip() for ln in (res.stdout or "").splitlines() if ln.strip()]
        if not clients:
            return "hub", "pool"
    except Exception:
        return "hub", "pool"
    for idx in range(2, 16):
        cand_outer = f"hub-{idx}"
        cand_pool = f"pool-view-{idx}"
        try:
            r = subprocess.run(
                ["tmux", "-L", OUTER_SOCKET, "list-clients", "-t", cand_outer, "-F", "#{client_name}"],
                capture_output=True,
                text=True,
                env=env,
            )
            c_list = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
            if not c_list:
                return cand_outer, cand_pool
        except Exception:
            return cand_outer, cand_pool
    return "hub-2", "pool-view-2"


def outer_attach(session_name: str) -> tuple[list[str], dict]:
    """tmux arguments (after the binary) and environment that put this terminal on the outer workspace: switch the
    client when already inside MixHub's server, otherwise attach, nested when inside the user's own tmux so their
    client stays on their server."""
    tmux_env = os.environ.get("TMUX", "")
    if tmux_env and os.path.basename(tmux_env.split(",")[0]) == OUTER_SOCKET:
        return ["-L", OUTER_SOCKET, "switch-client", "-t", session_name], dict(os.environ)
    env = {k: v for k, v in os.environ.items() if k != "TMUX"}
    return ["-L", OUTER_SOCKET, "attach-session", "-t", session_name], env


def launch_tmux_workspace():
    """Launch or attach to the Dual-Pane In-Place Live Workspace (`hub` or `hub-N`)."""
    session_name, pool_session = allocate_workspace_session_names()
    ensure_outer_dual_pane_layout(session_name, pool_session)
    if os.environ.get("TMUX"):
        args, env = outer_attach(session_name)
        os.execvpe("tmux", ["tmux", *args], env)
    else:
        try:
            sys.stdout.write("\x1b[?7l\x1b[2J\x1b[3J\x1b[H")
            sys.stdout.flush()
        except Exception:
            pass
        record_outer_window(session_name)
        subprocess.run(["tmux", "-L", OUTER_SOCKET, "attach-session", "-t", session_name], check=False)
        try:
            sys.stdout.write("\x1b[?7h")
            sys.stdout.flush()
        except Exception:
            pass
        print_exit_keepalive_hint()
