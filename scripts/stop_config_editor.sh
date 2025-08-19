#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$PROJECT_ROOT/.run"
PID_FILE="$RUN_DIR/config_editor.pid"
PORT="${PORT:-}" # optional hint for fuser fallback

stop_pid() {
  local pid="$1"
  if [[ -n "$pid" && -d "/proc/$pid" ]]; then
    echo "[info] Stopping config editor (PID $pid)"
    kill "$pid" 2>/dev/null || true
    # wait up to 3s
    for i in {1..30}; do
      [[ ! -d "/proc/$pid" ]] && break
      sleep 0.1
    done
    if [[ -d "/proc/$pid" ]]; then
      echo "[warn] Force killing PID $pid"
      kill -9 "$pid" 2>/dev/null || true
    fi
  fi
}

if [[ -f "$PID_FILE" ]]; then
  PID="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "${PID:-}" ]]; then
    stop_pid "$PID"
  fi
  rm -f "$PID_FILE"
  echo "[ok] Stopped via PID file"
else
  echo "[info] No PID file found at $PID_FILE, attempting pattern/port-based stop"
  # Try pattern-based stop
  pkill -f tools/config_editor.py 2>/dev/null || true
  # If a port hint provided, try fuser
  if [[ -n "$PORT" ]]; then
    fuser -k "${PORT}/tcp" 2>/dev/null || true
  fi
  echo "[ok] Stop commands issued"
fi
