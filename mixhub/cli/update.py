"""`mixhub update`: upgrade MixHub the way it was installed, then restart what is running on the new code.

The install is recognised from where this interpreter lives: a git checkout (`git pull --ff-only`), a uv tool
(`uv tool upgrade`), a pipx venv (`pipx upgrade`), or any other venv such as install.sh's (`pip install --upgrade`
inside it). A system-wide pip install is left alone; the command to run is printed instead. After an upgrade the
sidebar, reader and session headers are reloaded and a running web daemon is restarted, both by a fresh process so
they load the new code. Agent sessions keep running.

Once a day the PyPI version is checked in the background and a one-line hint is shown when a newer one exists. It
never updates by itself. `"update_check": false` in Preferences or `MIXHUB_NO_UPDATE_CHECK=1` turns the check off;
checkouts skip it.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request

import mixhub
from mixhub.core.env import env
from mixhub.core.launch import CLI_MODULE, child_env, module_argv
from mixhub.core.paths import CODE_ROOT, DATA_DIR
from mixhub.i18n import t
from mixhub.tmux.gateway import OUTER_SOCKET

PACKAGE = "mixhub-cli"
PYPI_URL = f"https://pypi.org/pypi/{PACKAGE}/json"
CHECK_EVERY_SEC = 24 * 3600
CACHE_NAME = "update_check.json"


def install_method(prefix: str | None = None, code_root: str = CODE_ROOT) -> str:
    """"checkout", "uv", "pipx", "venv" or "pip" (a system / user-site install)."""
    if os.path.isdir(os.path.join(code_root, ".git")):
        return "checkout"
    prefix = os.path.realpath(prefix or sys.prefix)
    parts = prefix.replace(os.sep, "/").split("/")
    uv_dir = os.environ.get("UV_TOOL_DIR")
    if (uv_dir and prefix.startswith(os.path.realpath(uv_dir) + os.sep)) or ("uv" in parts and "tools" in parts):
        return "uv"
    pipx_home = os.environ.get("PIPX_HOME")
    if (pipx_home and prefix.startswith(os.path.realpath(pipx_home) + os.sep)) or ("pipx" in parts and "venvs" in parts):
        return "pipx"
    if prefix != os.path.realpath(sys.base_prefix):
        return "venv"
    return "pip"


def upgrade_command(method: str, code_root: str = CODE_ROOT) -> list[str] | None:
    """The command that upgrades this install; None when MixHub shouldn't run it itself (a system-wide pip)."""
    if method == "checkout":
        return ["git", "-C", code_root, "pull", "--ff-only"]
    if method == "uv":
        return ["uv", "tool", "upgrade", PACKAGE]
    if method == "pipx":
        return ["pipx", "upgrade", PACKAGE]
    if method == "venv":
        return [sys.executable, "-m", "pip", "install", "--upgrade", PACKAGE]
    return None


def version_key(version: str) -> tuple:
    """`0.1.10` > `0.1.9`; a pre-release (`0.2.0rc1`) sorts below its release."""
    m = re.match(r"\s*v?(\d+(?:\.\d+)*)(.*)", version or "")
    if not m:
        return ()
    nums = tuple(int(n) for n in m.group(1).split("."))
    nums += (0,) * (3 - len(nums))
    pre = bool(re.match(r"[.-]?(a|b|rc|dev)", m.group(2)))
    return nums + (0 if pre else 1,)


def is_newer(candidate: str, current: str) -> bool:
    return bool(candidate) and version_key(candidate) > version_key(current)


def _installed_version() -> str:
    """The version on disk now (this process still has the old one imported)."""
    argv = [sys.executable, "-c", "import mixhub; print(mixhub.__version__)"]
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=30, env=child_env())
        return out.stdout.strip() or mixhub.__version__
    except (OSError, subprocess.SubprocessError):
        return mixhub.__version__


def _checkout_head(root: str) -> str:
    try:
        return subprocess.run(["git", "-C", root, "rev-parse", "HEAD"], capture_output=True, text=True,
                              timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _restart_running(runner=subprocess.run) -> list[str]:
    """Reload the workspace panes and restart the web daemon, each through a new `mixhub` process."""
    from mixhub.cli.daemon import health

    done = []
    cli = lambda *args: runner(module_argv(CLI_MODULE, *args), env=child_env(), check=False)  # noqa: E731
    if shutil.which("tmux") and runner(["tmux", "-L", OUTER_SOCKET, "has-session", "-t", "hub"], capture_output=True, check=False,
                                       env={k: v for k, v in os.environ.items() if k != "TMUX"}).returncode == 0:
        cli("reload")
        done.append("workspace")
    if health():
        cli("stop")
        cli("start")
        done.append("web")
    return done


def run_update(argv: list[str], runner=subprocess.run) -> int:
    check_only = "--check" in argv
    current = mixhub.__version__
    if check_only:
        latest = refresh_cache()
        if latest and is_newer(latest, current):
            print(t("update.available", latest=latest, current=current))
        else:
            print(t("update.current", version=current))
        return 0
    method = install_method()
    cmd = upgrade_command(method)
    if cmd is None:
        print(t("update.manual", cmd=f"{sys.executable} -m pip install --upgrade {PACKAGE}"), file=sys.stderr)
        return 1
    if not shutil.which(cmd[0]) and not os.path.isfile(cmd[0]):
        raise RuntimeError(t("update.missing_tool", tool=cmd[0]))
    print(t("update.running", method=method, cmd=" ".join(cmd)), flush=True)
    head = _checkout_head(CODE_ROOT) if method == "checkout" else ""
    rc = runner(cmd, check=False).returncode
    if rc != 0:
        return rc
    new = _installed_version()
    unchanged = head == _checkout_head(CODE_ROOT) if method == "checkout" else new == current
    if unchanged:
        print(t("update.current", version=current))
        return 0
    print(t("update.done", old=current, new=new) if new != current else t("update.pulled", version=new))
    restarted = _restart_running(runner)
    if restarted:
        print(t("update.restarted", what=", ".join(restarted)))
    return 0


# ---- once-a-day check -------------------------------------------------------------------------------------------

def _cache_path() -> str:
    return os.path.join(DATA_DIR, CACHE_NAME)


def _read_cache() -> dict:
    try:
        with open(_cache_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def check_disabled() -> bool:
    from mixhub.core.prefs import load_preferences

    if env("NO_UPDATE_CHECK") == "1" or install_method() == "checkout":
        return True
    return load_preferences().get("update_check") is False


def refresh_cache(urlopen=urllib.request.urlopen) -> str:
    """Ask PyPI for the latest version and remember when; "" when it can't be reached."""
    latest = ""
    try:
        with urlopen(PYPI_URL, timeout=5) as resp:
            latest = str(json.load(resp).get("info", {}).get("version") or "")
    except (OSError, ValueError):
        pass
    doc = {"checked_at": time.time(), "latest": latest or _read_cache().get("latest", "")}
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = f"{_cache_path()}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(doc, f)
        os.replace(tmp, _cache_path())
    except OSError:
        pass
    return latest


def update_notice(now: float | None = None, spawn=subprocess.Popen) -> str:
    """The "a newer MixHub exists" line from the last check, starting a background check when that is a day old."""
    if check_disabled():
        return ""
    cache = _read_cache()
    now = time.time() if now is None else now
    if now - float(cache.get("checked_at") or 0) > CHECK_EVERY_SEC:
        try:
            spawn(module_argv("mixhub.cli.update", "--refresh"), env=child_env(), stdin=subprocess.DEVNULL,
                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            pass
    latest = str(cache.get("latest") or "")
    return t("update.available", latest=latest, current=mixhub.__version__) if is_newer(latest, mixhub.__version__) else ""


if __name__ == "__main__":
    if sys.argv[1:] == ["--refresh"]:
        refresh_cache()
