#!/usr/bin/env sh
# One-command Heliograph bootstrap for macOS / Linux.
#
#   1. Makes sure uv (https://docs.astral.sh/uv/) is installed - installs it with the
#      official installer after telling you and asking first.
#   2. Runs `uv sync` to create .venv with every dependency.
#   3. Runs `heliograph setup` (checks Chrome/Edge, ffmpeg, ...).
#
# Safe to run again at any time. No secrets are read, written or sent.
# Options:  -y | --yes          no prompts (install uv if missing, answer yes in setup)
#           --with-whisper      also pre-download the Whisper speech model (~500 MB)
set -eu

YES=0
WHISPER=0
for arg in "$@"; do
  case "$arg" in
    -y|--yes) YES=1 ;;
    --with-whisper) WHISPER=1 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
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

REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$REPO_ROOT"
echo "Heliograph setup in $REPO_ROOT"

# --- 1. uv -------------------------------------------------------------------------------
step "Checking for uv"
if ! command -v uv >/dev/null 2>&1; then
  warn "uv is not installed."
  echo "      It will be installed with the official Astral installer:"
  echo "        curl -LsSf https://astral.sh/uv/install.sh | sh"
  echo "      (installs to ~/.local/bin, no root needed)"
  if [ "$YES" -ne 1 ]; then
    printf "      Install uv now? [Y/n] "
    read -r answer || answer=""
    case "$answer" in
      ""|y|Y|yes|YES) ;;
      *) fail "uv is required. Install it yourself (https://docs.astral.sh/uv/) and re-run."; exit 1 ;;
    esac
  fi
  if command -v curl >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- https://astral.sh/uv/install.sh | sh
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
uv sync --extra ocr
ok "dependencies installed in .venv"

# --- 3. heliograph setup ---------------------------------------------------------------------
step "Checking this machine (heliograph setup)"
set -- run --quiet heliograph setup
[ "$YES" -eq 1 ] && set -- "$@" --yes
[ "$WHISPER" -eq 1 ] && set -- "$@" --with-whisper
if uv "$@"; then
  echo
  ok "Setup finished."
  echo "      Next: uv run heliograph login   then run  claude  in this folder."
else
  code=$?
  fail "Setup found a critical problem (see above). Fix it and re-run this script."
  exit "$code"
fi
