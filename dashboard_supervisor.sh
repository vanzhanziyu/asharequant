#!/usr/bin/env bash
# User-session supervisor for the A-share market monitor.
# launchd keeps this parent process alive after the starting terminal exits.
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$PROJECT_DIR/backend"
FRONTEND_DIR="$PROJECT_DIR/frontend"
LOG_DIR="$PROJECT_DIR/.runtime-logs"
PYTHON_BIN="$BACKEND_DIR/.venv/bin/python"
NODE_BIN="/Users/lihuixue/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"

mkdir -p "$LOG_DIR"
children=()

stop_children() {
  for pid in "${children[@]:-}"; do
    kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap stop_children EXIT INT TERM

start() {
  "$PYTHON_BIN" -u "$BACKEND_DIR/main.py" >>"$LOG_DIR/api.log" 2>&1 & children+=("$!")
  "$PYTHON_BIN" -u "$BACKEND_DIR/collector.py" >>"$LOG_DIR/collector.log" 2>&1 & children+=("$!")
  "$PYTHON_BIN" -u "$BACKEND_DIR/wind_collector.py" >>"$LOG_DIR/wind_collector.log" 2>&1 & children+=("$!")
  "$PYTHON_BIN" -u "$BACKEND_DIR/vix_collector.py" >>"$LOG_DIR/vix_collector.log" 2>&1 & children+=("$!")
  "$PYTHON_BIN" -u "$BACKEND_DIR/factor_collector.py" >>"$LOG_DIR/factor_collector.log" 2>&1 & children+=("$!")
  "$NODE_BIN" "$FRONTEND_DIR/node_modules/next/dist/bin/next" dev "$FRONTEND_DIR" >>"$LOG_DIR/frontend.log" 2>&1 & children+=("$!")
}

while true; do
  children=()
  start
  wait || true
  stop_children
  children=()
  sleep 3
done
