#!/usr/bin/env bash
# 一条命令重启全部监控面板服务。
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

bash "$PROJECT_DIR/stop_dashboard.sh"
bash "$PROJECT_DIR/start_dashboard.sh"
