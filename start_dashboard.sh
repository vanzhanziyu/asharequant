#!/usr/bin/env bash
# 一键启动：API、行情采集器、VIX 采集器和 Next.js 前端（关闭 Terminal 后仍持续运行）。
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$PROJECT_DIR/backend"
FRONTEND_DIR="$PROJECT_DIR/frontend"
LOG_DIR="$PROJECT_DIR/.runtime-logs"
PYTHON_BIN="$BACKEND_DIR/.venv/bin/python"
FALLBACK_NODE="/Users/lihuixue/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"
PID_FILE="$LOG_DIR/dashboard.pids"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "未找到 Python 虚拟环境：$PYTHON_BIN"
  echo "请先在 backend 目录创建 .venv 并安装依赖。"
  exit 1
fi

mkdir -p "$LOG_DIR"

if [[ -f "$PID_FILE" ]]; then
  while read -r name pid; do
    if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
      echo "监控面板已在运行（${name}，PID ${pid}）。"
      echo "如需重启，请先执行：bash stop_dashboard.sh"
      exit 0
    fi
  done < "$PID_FILE"
fi

: > "$PID_FILE"

start_service() {
  local name="$1"
  shift
  nohup "$@" >"$LOG_DIR/$name.log" 2>&1 < /dev/null &
  local pid="$!"
  echo "${name} ${pid}" >> "$PID_FILE"
  echo "已启动 ${name}（日志：.runtime-logs/${name}.log）"
}

start_service "api" "$PYTHON_BIN" -u "$BACKEND_DIR/main.py"
start_service "collector" "$PYTHON_BIN" -u "$BACKEND_DIR/collector.py"
start_service "wind_collector" "$PYTHON_BIN" -u "$BACKEND_DIR/wind_collector.py"
start_service "vix_collector" "$PYTHON_BIN" -u "$BACKEND_DIR/vix_collector.py"

if command -v node >/dev/null 2>&1 && command -v pnpm >/dev/null 2>&1; then
  start_service "frontend" bash -lc "cd '$FRONTEND_DIR' && pnpm dev"
elif [[ -x "$FALLBACK_NODE" ]]; then
  start_service "frontend" "$FALLBACK_NODE" "$FRONTEND_DIR/node_modules/next/dist/bin/next" dev "$FRONTEND_DIR"
else
  echo "未找到 Node.js，前端未启动。请安装 Node.js 后重新运行此脚本。"
  exit 1
fi

echo
echo "监控面板正在启动： http://localhost:3000"
echo "服务已转入后台，关闭 Terminal 也会继续运行。"
echo "如需停止全部服务，请执行：bash stop_dashboard.sh"
