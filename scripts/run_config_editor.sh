#!/usr/bin/env bash
set -euo pipefail

# Ensures venv is available, activates it, and runs the config editor
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON:-python3}"
VENV_DIR="${VENV_DIR:-"$PROJECT_ROOT/.venv"}"
STAMP_FILE="$VENV_DIR/.requirements.installed"
REQUIREMENTS_FILE="$PROJECT_ROOT/requirements.txt"
EDITOR="$PROJECT_ROOT/tools/config_editor.py"

cd "$PROJECT_ROOT"

if [[ ! -d "$VENV_DIR" ]]; then
  echo "[info] Creating venv at: $VENV_DIR"
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip setuptools wheel >/dev/null

if [[ ! -f "$STAMP_FILE" || "$REQUIREMENTS_FILE" -nt "$STAMP_FILE" ]]; then
  echo "[info] Installing Python dependencies from requirements.txt"
  pip install -r "$REQUIREMENTS_FILE"
  touch "$STAMP_FILE"
fi

if [[ ! -f "$EDITOR" ]]; then
  echo "[error] Config editor not found at $EDITOR" >&2
  exit 1
fi

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-5080}"
export FLASK_DEBUG=${FLASK_DEBUG:-0}

echo "[info] Opening config editor at http://$HOST:$PORT"
exec python "$EDITOR" "$@"
