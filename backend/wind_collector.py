"""Wind micro-cap index collector; its K-line and turnover share the same SQLite database."""
from __future__ import annotations

import argparse
import datetime as dt

import requests
from apscheduler.schedulers.blocking import BlockingScheduler

from database import connect, init_db

WIND_URL = "https://indexapi.wind.com.cn/indicesWebsite/api/Kline"
INDEX_ID = "1a073179a3f0bebf923a0259cee963e4"
MAX_VALID_VALUE = 1e12


def _number(value: object) -> float:
    try: return float(value or 0)
    except (TypeError, ValueError): return 0.0


def _date(value: object) -> str:
    text = str(value or "").split(" ")[0]
    return f"{text[:4]}-{text[4:6]}-{text[6:]}" if len(text) == 8 and text.isdigit() else text


def fetch_and_sync_wind_full_kline() -> int:
    init_db()
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.windindices.com/", "Origin": "https://www.windindices.com"}
    try:
        response = requests.get(WIND_URL, params={"indexId": INDEX_ID, "period": "1Y", "lan": "cn"}, headers=headers, timeout=15)
        response.raise_for_status(); payload = response.json()
        result = payload.get("Result") or payload.get("result") or {}
        records = result.get("data") or result.get("list") or payload.get("data") or [] if isinstance(result, dict) else result
        valid = []
        for item in records:
            open_, close, amount = _number(item.get("open") or item.get("o")), _number(item.get("close") or item.get("c") or item.get("price")), _number(item.get("amount") or item.get("turnover") or item.get("val"))
            if not _date(item.get("tradeDate") or item.get("date") or item.get("time")) or max(open_, close, amount) >= MAX_VALID_VALUE:
                continue
            valid.append((_date(item.get("tradeDate") or item.get("date") or item.get("time")), open_, _number(item.get("high") or item.get("hight") or item.get("h")), _number(item.get("low") or item.get("l")), close, _number(item.get("volume") or item.get("vol") or item.get("v")), round(amount / 1e8, 2)))
        with connect() as conn:
            for record in valid:
                conn.execute("""INSERT INTO wind_kline_history VALUES (?,?,?,?,?,?,?) ON CONFLICT(trade_date) DO UPDATE SET
                    open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,volume=excluded.volume,amount_yi=excluded.amount_yi""", record)
                conn.execute("""INSERT INTO market_turnover_history (trade_date,wind_micro_amount) VALUES (?,?)
                    ON CONFLICT(trade_date) DO UPDATE SET wind_micro_amount=excluded.wind_micro_amount""", (record[0], record[-1]))
        print(f"[完成] Wind 微盘指数同步 {len(valid)} 条。")
        return len(valid)
    except Exception as exc:
        print(f"[Wind] 同步失败：{exc}"); return 0


def start_scheduler() -> None:
    # 避免电脑休眠、网络短暂中断后错过整点同步而直接跳过。
    scheduler = BlockingScheduler(
        timezone="Asia/Shanghai",
        job_defaults={"coalesce": True, "misfire_grace_time": 86400},
    )
    job_options = {"coalesce": True, "misfire_grace_time": 86400, "max_instances": 1}
    scheduler.add_job(fetch_and_sync_wind_full_kline, "cron", minute="0", **job_options)
    # 启动后立即抓取，不能因初始下载而错过整点任务。
    scheduler.add_job(fetch_and_sync_wind_full_kline, "date", run_date=dt.datetime.now(), id="startup_wind", **job_options)
    scheduler.start()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--once", action="store_true")
    args = parser.parse_args(); fetch_and_sync_wind_full_kline() if args.once else start_scheduler()
