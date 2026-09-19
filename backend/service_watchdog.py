"""Restart the dashboard service when a wake-from-sleep leaves polling stale."""
from __future__ import annotations

import datetime as dt
import os
import subprocess
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
RUNTIME_LOG_DIR = PROJECT_DIR / ".runtime-logs"
HEARTBEAT_PATH = RUNTIME_LOG_DIR / "limit_pool.heartbeat"
LOG_PATH = RUNTIME_LOG_DIR / "watchdog.log"
SERVICE_LABEL = "com.lihuixue.ashare-market-monitor"
STALE_AFTER_SECONDS = 12 * 60


def log(message: str) -> None:
    RUNTIME_LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(f"[{stamp}] {message}\n")


def is_polling_window(now: dt.datetime) -> bool:
    """Only enforce the heartbeat when weekday limit-pool polling should be active."""
    if now.weekday() >= 5:
        return False
    clock = now.time()
    return dt.time(9, 0) <= clock <= dt.time(16, 10)


def heartbeat_is_stale(now: dt.datetime) -> bool:
    try:
        age = now.timestamp() - HEARTBEAT_PATH.stat().st_mtime
    except FileNotFoundError:
        return True
    return age > STALE_AFTER_SECONDS


def restart_dashboard() -> None:
    target = f"gui/{os.getuid()}/{SERVICE_LABEL}"
    result = subprocess.run(
        ["launchctl", "kickstart", "-k", target],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    if result.returncode == 0:
        log("涨跌停轮询心跳停滞，已请求自动重启监控面板服务。")
    else:
        detail = (result.stderr or result.stdout).strip()
        log(f"自动重启请求失败（{result.returncode}）：{detail}")


def main() -> None:
    now = dt.datetime.now()
    if not is_polling_window(now):
        return
    if heartbeat_is_stale(now):
        restart_dashboard()


if __name__ == "__main__":
    main()
