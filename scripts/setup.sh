#!/usr/bin/env sh
# One-command Heliograph bootstrap for macOS / Linux.
#
#   1. Makes sure uv (https://docs.astral.sh/uv/) is installed - installs it with the
#      official installer (pinned to uv 0.12.1) after telling you and asking first.
#   2. Runs `uv sync` to create .venv with every dependency.
#   3. Runs `heliograph setup` (checks Chrome/Edge, ffmpeg, ...).
# Usage: bash scripts/setup.sh [options]     (safe to re-run; no secrets read or sent)
# Options:  -y | --yes          no prompts (install uv if missing, answer yes in setup)
#           --no-input          no prompts, answer no (scripts/CI; uv must be installed)
#           --with-whisper      also pre-download the Whisper speech model (~500 MB)
set -eu

# The uv installer is pinned to a known release (not "latest"); bump deliberately.
UV_VERSION=0.12.1
UV_INSTALLER="https://astral.sh/uv/$UV_VERSION/install.sh"

YES=0
NO_INPUT=0
WHISPER=0
for arg in "$@"; do
  case "$arg" in
    -y|--yes) YES=1 ;;
    --no-input) NO_INPUT=1 ;;
    --with-whisper) WHISPER=1 ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$arg" >&2; exit 2 ;;
  esac
done

if [ -t 1 ]; then
  C_STEP='\033[36m'; C_OK='\033[32m'; C_WARN='\033[33m'; C_FAIL='\033[31m'; C_OFF='\033[0m'
else
  C_STEP=''; C_OK=''; C_WARN=''; C_FAIL=''; C_OFF=''
fi
step() { printf "${C_STEP}==> %s${C_OFF}\n" "$1"; }
ok()   { printf "${C_OK}  ok  %s${C_OFF}\n" "$1"; }
warn() { printf "${C_WARN}  !!  %s${C_OFF}\n" "$1"; }
fail() { printf "${C_FAIL}  xx  %s${C_OFF}\n" "$1"; }

REPO_ROOT=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
cd "$REPO_ROOT"
echo "Heliograph setup in $REPO_ROOT"

# --- 1. uv -------------------------------------------------------------------------------
step "Checking for uv"
if ! command -v uv >/dev/null 2>&1; then
  warn "uv is not installed."
  echo "      It will be installed with the official Astral installer (uv $UV_VERSION):"
  echo "        curl --proto '=https' --tlsv1.2 -LsSf $UV_INSTALLER | sh"
  echo "      (installs to ~/.local/bin, no root needed)"
  if [ "$NO_INPUT" -eq 1 ] && [ "$YES" -ne 1 ]; then
    fail "uv is required and --no-input was given. Install it (https://docs.astral.sh/uv/) and re-run."
    exit 1
  fi
  if [ "$YES" -ne 1 ]; then
    printf "      Install uv now? [Y/n] "
    read -r answer || answer=""
    case "$answer" in
      ""|y|Y|yes|YES) ;;
      *) fail "uv is required. Install it yourself (https://docs.astral.sh/uv/) and re-run."; exit 1 ;;
    esac
  fi
  if command -v curl >/dev/null 2>&1; then
    curl --proto '=https' --tlsv1.2 -LsSf "$UV_INSTALLER" | sh
  elif command -v wget >/dev/null 2>&1; then
    wget --https-only -qO- "$UV_INSTALLER" | sh
  else
    fail "Neither curl nor wget is available to download uv."; exit 1
  fi
  PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  export PATH
  if ! command -v uv >/dev/null 2>&1; then
    fail "uv was installed but is not on PATH yet. Open a new terminal and re-run."; exit 1
  fi
fi
ok "$(uv --version)"

# --- 2. dependencies -----------------------------------------------------------------------
step "Installing Python dependencies (uv sync, with on-screen OCR extra)"
uv sync --locked --extra ocr   # --locked: install exactly what uv.lock pins
ok "dependencies installed in .venv"

# --- 3. heliograph setup ---------------------------------------------------------------------
step "Checking this machine (heliograph setup)"
set -- run --quiet heliograph setup
[ "$YES" -eq 1 ] && set -- "$@" --yes
[ "$WHISPER" -eq 1 ] && set -- "$@" --with-whisper
[ "$NO_INPUT" -eq 1 ] && set -- "$@" --no-input
if uv "$@"; then
  echo
  ok "Setup finished."
  # Only suggest `login` when the dedicated profile has never been signed in (or was last
  # seen logged out); doctor --json reports that without opening any browser.
  if uv run --quiet heliograph doctor --json 2>/dev/null | grep -q '"needs_login": false'; then
    echo "      Browser profile already signed in. Next: run  claude  in this folder."
  else
    echo "      Next: uv run heliograph login   then run  claude  in this folder."
  fi
else
  code=$?
  fail "Setup found a critical problem (see above). Fix it and re-run this script."
  exit "$code"
fi
