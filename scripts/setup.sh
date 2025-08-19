python3 main.py#!/usr/bin/env bash
set -euo pipefail

# Setup script for AI_Dungeon
# - Creates/refreshes a Python virtual environment
# - Installs Python dependencies from requirements.txt
# - Optionally installs OS build dependencies (Debian/Ubuntu or Fedora)
# - Optionally runs the app

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON:-python3}"
VENV_DIR="${VENV_DIR:-"$PROJECT_ROOT/.venv"}"
REQUIREMENTS_FILE="$PROJECT_ROOT/requirements.txt"
MAIN_FILE="$PROJECT_ROOT/main.py"

WITH_BUILD_DEPS=false
RUN_AFTER=false
FRESH=false

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Options:
  --with-build-deps   Install OS build dependencies (apt/dnf) for pygame/netifaces
  --run               Run the app (python main.py) after installing deps
  --fresh             Recreate the virtual environment from scratch
  -h, --help          Show this help and exit

Env vars:
  PYTHON=python3      Python interpreter to use (default: python3)
  VENV_DIR=.venv      Virtual environment directory (default: .venv at project root)
EOF
}

for arg in "$@"; do
  case "$arg" in
    --with-build-deps) WITH_BUILD_DEPS=true ;;
    --run) RUN_AFTER=true ;;
    --fresh) FRESH=true ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $arg"; usage; exit 1 ;;
  esac
done

install_build_deps() {
  echo "[info] Installing OS build dependencies..."
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get install -y \
      python3-dev build-essential \
      libsdl2-dev libfreetype6-dev libportmidi-dev \
      libjpeg-dev zlib1g-dev
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf groupinstall -y "Development Tools" || true
    sudo dnf install -y \
      python3-devel SDL2-devel freetype-devel \
      portmidi-devel libjpeg-turbo-devel zlib-devel || true
  else
    echo "[warn] Unsupported package manager. Please install build tools manually for your distro."
  fi
}

create_or_refresh_venv() {
  if [[ "$FRESH" == true && -d "$VENV_DIR" ]]; then
    echo "[info] Removing existing venv: $VENV_DIR"
    rm -rf "$VENV_DIR"
  fi
  if [[ ! -d "$VENV_DIR" ]]; then
    echo "[info] Creating venv at: $VENV_DIR"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
  else
    echo "[info] Using existing venv at: $VENV_DIR"
  fi
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
  python -m pip install --upgrade pip setuptools wheel
}

install_python_deps() {
  if [[ ! -f "$REQUIREMENTS_FILE" ]]; then
    echo "[error] requirements.txt not found at $REQUIREMENTS_FILE" >&2
    exit 1
  fi
  echo "[info] Installing Python dependencies from requirements.txt"
  pip install -r "$REQUIREMENTS_FILE"
}

run_app() {
  if [[ ! -f "$MAIN_FILE" ]]; then
    echo "[error] main.py not found at $MAIN_FILE" >&2
    exit 1
  fi
  echo "[info] Launching app..."
  python "$MAIN_FILE"
}

main() {
  cd "$PROJECT_ROOT"
  echo "[info] Project root: $PROJECT_ROOT"

  if [[ "$WITH_BUILD_DEPS" == true ]]; then
    install_build_deps
  fi

  create_or_refresh_venv
  install_python_deps

  echo "[ok] Setup complete. To activate the environment later:"
  echo "     source \"$VENV_DIR/bin/activate\""

  if [[ "$RUN_AFTER" == true ]]; then
    run_app
  else
    echo "[hint] Run the app with: python main.py"
  fi
}

main "$@"
