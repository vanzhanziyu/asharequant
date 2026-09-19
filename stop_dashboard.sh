#!/usr/bin/env bash
# 停止由 start_dashboard.sh 启动的项目服务。
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$PROJECT_DIR/.runtime-logs/dashboard.pids"

if [[ ! -f "$PID_FILE" ]]; then
  echo "未找到正在运行的一键启动服务。"
  exit 0
fi

while read -r name pid; do
  if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid"
    echo "已停止 ${name}。"
  fi
done < "$PID_FILE"

rm -f "$PID_FILE"
