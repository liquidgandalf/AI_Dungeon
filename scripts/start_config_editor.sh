#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$PROJECT_ROOT/.run"
PID_FILE="$RUN_DIR/config_editor.pid"
PORT="${PORT:-5080}"
HOST="${HOST:-127.0.0.1}"

mkdir -p "$RUN_DIR"

# If a PID file exists and the process is alive, do not start a duplicate
if [[ -f "$PID_FILE" ]]; then
  OLD_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "${OLD_PID:-}" && -d "/proc/$OLD_PID" ]]; then
    echo "[warn] Config editor already running (PID $OLD_PID)."
    echo "       URL: http://$HOST:$PORT (or whatever it was started with)"
    exit 0
  else
    rm -f "$PID_FILE"
  fi
fi

# Start editor in background using the existing runner script
# Inherit HOST/PORT from env or defaults above
(
  cd "$PROJECT_ROOT"
  HOST="$HOST" PORT="$PORT" nohup scripts/run_config_editor.sh >/dev/null 2>&1 &
  echo $! > "$PID_FILE"
)

NEW_PID="$(cat "$PID_FILE")"
echo "[ok] Config editor started (PID $NEW_PID)"
echo "[info] URL: http://$HOST:$PORT"
