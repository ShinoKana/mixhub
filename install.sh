#!/bin/sh
# MixHub installer: curl -fsSL https://raw.githubusercontent.com/ShinoKana/mixhub/main/install.sh | sh
#
# Installs the `mixhub-cli` package with the first tool it finds: uv, then pipx, then a private venv under
# ~/.local/share/mixhub/venv. It never uses sudo; missing system packages (Python 3.10+, tmux) are reported with
# the command to install them.
#
# Environment:
#   MIXHUB_SPEC     what to install (default: mixhub-cli); a version pin, a git URL or a local path also work
#   MIXHUB_BIN_DIR  where the venv fallback links `mixhub` (default: ~/.local/bin)
#   MIXHUB_VENV     venv location for the fallback (default: ~/.local/share/mixhub/venv)
set -eu

SPEC="${MIXHUB_SPEC:-mixhub-cli}"
BIN_DIR="${MIXHUB_BIN_DIR:-$HOME/.local/bin}"
VENV="${MIXHUB_VENV:-${XDG_DATA_HOME:-$HOME/.local/share}/mixhub/venv}"

say() { printf '%s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
has() { command -v "$1" >/dev/null 2>&1; }

os="$(uname -s 2>/dev/null || echo unknown)"
case "$os" in
  Darwin | Linux) ;;
  MINGW* | MSYS* | CYGWIN*)
    die "native Windows is not supported; run this inside WSL (wsl --install, then open Ubuntu)" ;;
  *) warn "untested platform: $os" ;;
esac

pkg_hint() {
  # $1 = Homebrew formula, $2 = apt package
  if [ "$os" = Darwin ]; then
    echo "brew install $1"
  elif has apt-get; then
    echo "sudo apt-get install -y $2"
  elif has dnf; then
    echo "sudo dnf install -y $2"
  elif has pacman; then
    echo "sudo pacman -S $2"
  else
    echo "install $2 with your package manager"
  fi
}

# Python 3.10+ (the newest one on PATH)
PYTHON=""
for cand in python3.14 python3.13 python3.12 python3.11 python3.10 python3; do
  if has "$cand" && "$cand" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    PYTHON="$(command -v "$cand")"
    break
  fi
done
[ -n "$PYTHON" ] || die "MixHub needs Python 3.10 or newer: $(pkg_hint python python3)"
say "Python: $PYTHON ($("$PYTHON" -c 'import platform; print(platform.python_version())'))"

if has tmux; then
  say "tmux:   $(tmux -V)"
else
  warn "tmux not found; the workspace (\`mixhub\`) needs it, \`mixhub web\` works without it: $(pkg_hint tmux tmux)"
fi

installed_with=""
path_hint="export PATH=\"$BIN_DIR:\$PATH\" in your shell profile"
if has uv; then
  say "Installing $SPEC with uv..."
  uv tool install --force --python "$PYTHON" "$SPEC"
  installed_with="uv (uv tool uninstall mixhub-cli to remove)"
  path_hint="uv tool update-shell"
elif has pipx; then
  say "Installing $SPEC with pipx..."
  pipx install --force --python "$PYTHON" "$SPEC"
  installed_with="pipx (pipx uninstall mixhub-cli to remove)"
  path_hint="pipx ensurepath"
else
  say "Installing $SPEC into $VENV..."
  "$PYTHON" -m venv "$VENV" 2>/dev/null \
    || die "could not create a venv (on Debian/Ubuntu: $(pkg_hint python python3-venv)), or install uv: https://docs.astral.sh/uv/"
  "$VENV/bin/python" -m pip install --quiet --upgrade pip
  "$VENV/bin/python" -m pip install --quiet --upgrade "$SPEC"
  mkdir -p "$BIN_DIR"
  ln -sf "$VENV/bin/mixhub" "$BIN_DIR/mixhub"
  installed_with="a venv (rm -rf \"$VENV\" \"$BIN_DIR/mixhub\" to remove)"
fi

say ""
say "MixHub installed with $installed_with."

if ! has mixhub; then
  warn "the install directory is not on PATH yet; run: $path_hint (then open a new shell)"
fi

say ""
say "Next:"
say "  mixhub doctor   check agents, tmux and terminal settings"
say "  mixhub          open the workspace"
say "  mixhub web      open the web workbench at http://127.0.0.1:8765"
say "  mixhub update   upgrade later (or run this script again)"
