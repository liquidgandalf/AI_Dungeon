#!/usr/bin/env bash
set -euo pipefail

# Run script for AI_Dungeon
# - Ensures Python venv exists
# - Activates venv if needed
# - Installs/updates deps if requirements.txt changed
# - Runs main.py (forwards any args)

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON:-python3}"
VENV_DIR="${VENV_DIR:-"$PROJECT_ROOT/.venv"}"
REQUIREMENTS_FILE="$PROJECT_ROOT/requirements.txt"
MAIN_FILE="$PROJECT_ROOT/main.py"
STAMP_FILE="$VENV_DIR/.requirements.installed"

cd "$PROJECT_ROOT"

echo "[info] Project root: $PROJECT_ROOT"

# 1) Create venv if missing
if [[ ! -d "$VENV_DIR" ]]; then
  echo "[info] Creating venv at: $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

# 2) Activate venv if not already active
# shellcheck disable=SC1091
if [[ -z "${VIRTUAL_ENV:-}" || "$VIRTUAL_ENV" != "$VENV_DIR" ]]; then
  echo "[info] Activating venv..."
  # shellcheck disable=SC1091
  source "$VENV_DIR/bin/activate"
fi

# 3) Ensure pip tooling is up-to-date at least once
python -m pip install --upgrade pip setuptools wheel >/dev/null

# 4) Install/refresh deps when requirements.txt changed
if [[ ! -f "$REQUIREMENTS_FILE" ]]; then
  echo "[error] requirements.txt not found at $REQUIREMENTS_FILE" >&2
  exit 1
fi
if [[ ! -f "$STAMP_FILE" || "$REQUIREMENTS_FILE" -nt "$STAMP_FILE" ]]; then
  echo "[info] Installing Python dependencies from requirements.txt"
  pip install -r "$REQUIREMENTS_FILE"
  touch "$STAMP_FILE"
fi

# 5) Run the app
if [[ ! -f "$MAIN_FILE" ]]; then
  echo "[error] main.py not found at $MAIN_FILE" >&2
  exit 1
fi

echo "[info] Launching app..."
exec python "$MAIN_FILE" "$@"
