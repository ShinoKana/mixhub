"""`mixhub.core.browser`: open a URL in the desktop browser (macOS, Linux, and the Windows browser from WSL)."""
import os
import shutil
import subprocess
import sys


def is_wsl() -> bool:
    if os.environ.get("WSL_DISTRO_NAME"):
        return True
    try:
        with open("/proc/sys/kernel/osrelease", encoding="ascii", errors="replace") as f:
            return "microsoft" in f.read().lower()
    except OSError:
        return False


def _openers() -> list[str]:
    if sys.platform == "darwin":
        return ["open"]
    if is_wsl():
        # wslview (wslu) when installed; explorer.exe hands the URL to the Windows default browser.
        return ["wslview", "explorer.exe", "xdg-open"]
    return ["xdg-open"]


def open_url(url: str, run=None, which=None) -> bool:
    """True when an opener took the URL; False means print it for the user instead."""
    run, which = run or subprocess.run, which or shutil.which
    for name in _openers():
        if not which(name):
            continue
        try:
            res = run([name, url], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL, timeout=5, check=False)
        except (OSError, subprocess.SubprocessError):
            continue
        if res.returncode == 0 or name == "explorer.exe":  # explorer.exe exits 1 even when it opened the page
            return True
    return False
