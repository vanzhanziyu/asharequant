"""Tushare/AkShare collector for turnover, indices, margin balance and limit pools."""
from __future__ import annotations

import argparse
import datetime as dt
import multiprocessing as mp
import os
from queue import Empty
from io import StringIO
from pathlib import Path
from typing import Iterable

import akshare as ak
import pandas as pd
import requests
import tushare as ts
from apscheduler.schedulers.blocking import BlockingScheduler
from dotenv import dotenv_values

from database import connect, init_db

# Environment variables take precedence; ``backend/.env`` is for local and
# server-only configuration and is deliberately excluded from Git.
ENV_FILE = Path(__file__).with_name(".env")
INDEXES = {
    "000001.SH": "sh", "399107.SZ": "sz", "399102.SZ": "cyb",
    "000688.SH": "kc50", "000015.SH": "hl",
}
MACRO_SYMBOLS = ("USDCNH.FXCM", "USDOLLAR.FXCM", "XAUUSD.FXCM")
YAHOO_DXY_SYMBOL = "DX-Y.NYB"
FRED_DXY_COMPONENTS = ("DEXUSEU", "DEXJPUS", "DEXUSUK", "DEXCAUS", "DEXSDUS", "DEXSZUS")
FRED_FX_SERIES = {"USDCNH.FXCM": ("DEXCHUS", "FRED 美联储 USD/CNY（收盘）")}
FRANKFURTER_DXY_CURRENCIES = ("EUR", "JPY", "GBP", "CAD", "SEK", "CHF")
RUNTIME_LOG_DIR = Path(__file__).resolve().parents[1] / ".runtime-logs"
LIMIT_POOL_HEARTBEAT = RUNTIME_LOG_DIR / "limit_pool.heartbeat"
# AkShare's Eastmoney pool endpoints occasionally keep a socket open without a
# response.  A normal thread cannot be safely killed, so each pool fetch runs
# in a short-lived child process with a hard deadline.
LIMIT_POOL_TIMEOUT_SECONDS = 75


def now_text() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def normalize_date(value: object) -> str:
    text = str(value).split(" ")[0]
    return f"{text[:4]}-{text[4:6]}-{text[6:]}" if len(text) == 8 and text.isdigit() else text


def pro_api():
    # 直接传入 token，避免多个定时任务并发读写 Tushare 的全局 token 文件。
    token = os.getenv("TUSHARE_TOKEN") or dotenv_values(ENV_FILE).get("TUSHARE_TOKEN")
    if not token:
        raise RuntimeError("未配置 TUSHARE_TOKEN：请在 backend/.env 或环境变量中设置")
    return ts.pro_api(token)


def sync_history_turnover(days: int = 365) -> None:
    """Store index OHLC and turnover in yi yuan for the most recent period."""
    init_db()
    start = (dt.datetime.now() - dt.timedelta(days=days)).strftime("%Y%m%d")
    try:
        client = pro_api()
    except Exception as exc:
        print(f"[指数] Tushare 客户端初始化失败：{exc}")
        return
    data: dict[str, pd.DataFrame] = {}
    for code in INDEXES:
        try:
            data[code] = client.index_daily(ts_code=code, start_date=start)
        except Exception as exc:
            print(f"[指数] {code} 获取失败：{exc}")
            data[code] = pd.DataFrame()

    with connect() as conn:
        for code, frame in data.items():
            if frame.empty:
                continue
            for _, row in frame.iterrows():
                conn.execute(
                    """INSERT INTO index_kline_history
                    (index_code,trade_date,open,high,low,close,volume,amount)
                    VALUES (?,?,?,?,?,?,?,?)
                    ON CONFLICT(index_code,trade_date) DO UPDATE SET
                    open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,
                    volume=excluded.volume,amount=excluded.amount""",
                    (code, normalize_date(row["trade_date"]), float(row.get("open", 0)),
                     float(row.get("high", 0)), float(row.get("low", 0)), float(row.get("close", 0)),
                     float(row.get("vol", 0)), float(row.get("amount", 0))),
                )

        amounts: dict[str, dict[str, float]] = {}
        for code, prefix in INDEXES.items():
            frame = data[code]
            if frame.empty:
                continue
            for _, row in frame.iterrows():
                trade_date = normalize_date(row["trade_date"])
                amounts.setdefault(trade_date, {})[prefix] = round(float(row.get("amount", 0)) / 100000, 2)
        for trade_date, values in amounts.items():
            sh, sz = values.get("sh", 0), values.get("sz", 0)
            conn.execute(
                """INSERT INTO market_turnover_history
                (trade_date,total_amount,sh_amount,sz_amount,cyb_amount,kc50_amount,hl_amount,updated_at)
                VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(trade_date) DO UPDATE SET
                total_amount=excluded.total_amount,sh_amount=excluded.sh_amount,sz_amount=excluded.sz_amount,
                cyb_amount=excluded.cyb_amount,kc50_amount=excluded.kc50_amount,hl_amount=excluded.hl_amount,
                updated_at=excluded.updated_at""",
                (trade_date, round(sh + sz, 2), sh, sz, values.get("cyb", 0), values.get("kc50", 0), values.get("hl", 0), now_text()),
            )
    print("[完成] 常规指数与成交额已同步。")


def sync_margin_history(days: int = 365) -> None:
    init_db()
    start = (dt.datetime.now() - dt.timedelta(days=days)).strftime("%Y%m%d")
    try:
        frame = pro_api().margin(start_date=start, end_date=dt.datetime.now().strftime("%Y%m%d"))
        if frame is None or frame.empty:
            return
        frame["trade_date"] = frame["trade_date"].map(normalize_date)
        frame["rzrqye"] = pd.to_numeric(frame["rzrqye"], errors="coerce").fillna(0)
        grouped = frame.groupby("trade_date", as_index=False)["rzrqye"].sum()
        with connect() as conn:
            for _, row in grouped.iterrows():
                conn.execute("""INSERT INTO margin_history (trade_date,rzrqye,updated_at) VALUES (?,?,?)
                    ON CONFLICT(trade_date) DO UPDATE SET rzrqye=excluded.rzrqye,updated_at=excluded.updated_at""",
                    (row["trade_date"], round(float(row["rzrqye"]) / 1e8, 2), now_text()))
        print("[完成] 两融余额已同步。")
    except Exception as exc:
        print(f"[两融] 同步失败：{exc}")


def sync_macro_history(days: int = 1095) -> None:
    """同步并持久化最近三年的全球宏观日线，Tushare 数据优先。"""
    init_db()
    start = (dt.datetime.now() - dt.timedelta(days=days)).strftime("%Y%m%d")
    end = dt.datetime.now().strftime("%Y%m%d")
    try:
        client = pro_api()
    except Exception as exc:
        print(f"[宏观] Tushare 客户端初始化失败：{exc}")
        return
    stamp = now_text()

    try:
        yields = client.us_tycr(start_date=start, end_date=end)
        if yields is not None and not yields.empty:
            with connect() as conn:
                for _, row in yields.iterrows():
                    conn.execute(
                        """INSERT INTO us_treasury_history (trade_date,m3,y2,y5,y10,y30,updated_at)
                        VALUES (?,?,?,?,?,?,?) ON CONFLICT(trade_date) DO UPDATE SET
                        m3=excluded.m3,y2=excluded.y2,y5=excluded.y5,y10=excluded.y10,
                        y30=excluded.y30,updated_at=excluded.updated_at""",
                        (normalize_date(row.get("date", "")), _as_float(row.get("m3")), _as_float(row.get("y2")),
                         _as_float(row.get("y5")), _as_float(row.get("y10")), _as_float(row.get("y30")), stamp),
                    )
            print(f"[完成] 美债收益率同步 {len(yields)} 条。")
    except Exception as exc:
        print(f"[宏观] 美债收益率同步失败：{exc}")

    def save_prices(
        symbol: str,
        records: Iterable[tuple[str, float, float, float, float]],
        source: str,
        *,
        overwrite: bool = True,
    ) -> int:
        rows = list(records)
        if not rows:
            return 0
        conflict = """ON CONFLICT(symbol,trade_date) DO UPDATE SET
            open=excluded.open,high=excluded.high,low=excluded.low,close=excluded.close,
            source=excluded.source,updated_at=excluded.updated_at""" if overwrite else "ON CONFLICT(symbol,trade_date) DO NOTHING"
        with connect() as conn:
            for trade_date, open_, high, low, close in rows:
                conn.execute(
                    """INSERT INTO macro_price_history (symbol,trade_date,open,high,low,close,source,updated_at)
                    VALUES (?,?,?,?,?,?,?,?) """ + conflict,
                    (symbol, trade_date, open_, high, low, close, source, stamp),
                )
        return len(rows)

    def yahoo_dxy_records() -> list[tuple[str, float, float, float, float]]:
        response = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{YAHOO_DXY_SYMBOL}",
            params={"interval": "1d", "range": "2y", "includePrePost": "false", "events": "div,splits"},
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/122 Safari/537.36", "Accept": "application/json"},
            timeout=20,
        )
        response.raise_for_status()
        chart = response.json().get("chart", {}).get("result") or []
        if not chart:
            return []
        result = chart[0]
        quote = (result.get("indicators", {}).get("quote") or [{}])[0]
        records = []
        for timestamp, open_, high, low, close in zip(result.get("timestamp") or [], quote.get("open") or [], quote.get("high") or [], quote.get("low") or [], quote.get("close") or []):
            if None not in (open_, high, low, close):
                trade_date = dt.datetime.fromtimestamp(timestamp, tz=dt.timezone.utc).date().isoformat()
                records.append((trade_date, float(open_), float(high), float(low), float(close)))
        return records

    def fred_records(series_id: str) -> list[tuple[str, float, float, float, float]]:
        """Read a public FRED daily series as close-only historical records."""
        response = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv", params={"id": series_id}, timeout=30)
        response.raise_for_status()
        frame = pd.read_csv(StringIO(response.text), na_values=".")
        if series_id not in frame:
            return []
        frame[series_id] = pd.to_numeric(frame[series_id], errors="coerce")
        cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
        frame = frame.dropna(subset=[series_id])
        frame = frame[frame["observation_date"].astype(str) >= cutoff]
        return [(str(row["observation_date"]), float(row[series_id]), float(row[series_id]), float(row[series_id]), float(row[series_id])) for _, row in frame.iterrows()]

    def fred_dxy_records() -> list[tuple[str, float, float, float, float]]:
        """Use the Fed's public FX reference rates to reconstruct the standard DXY formula."""
        response = requests.get("https://fred.stlouisfed.org/graph/fredgraph.csv", params={"id": ",".join(FRED_DXY_COMPONENTS)}, timeout=30)
        response.raise_for_status()
        merged = pd.read_csv(StringIO(response.text), na_values=".")
        for series_id in FRED_DXY_COMPONENTS:
            merged[series_id] = pd.to_numeric(merged[series_id], errors="coerce")
        cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
        merged = merged.dropna()
        merged = merged[merged["observation_date"].astype(str) >= cutoff]
        merged["dxy"] = 50.14348112 * (merged["DEXUSEU"] ** -0.576) * (merged["DEXJPUS"] ** 0.136) * (merged["DEXUSUK"] ** -0.119) * (merged["DEXCAUS"] ** 0.091) * (merged["DEXSDUS"] ** 0.042) * (merged["DEXSZUS"] ** 0.036)
        return [(str(row["observation_date"]), float(row["dxy"]), float(row["dxy"]), float(row["dxy"]), float(row["dxy"])) for _, row in merged.iterrows()]

    def frankfurter_dxy_records() -> list[tuple[str, float, float, float, float]]:
        """Reconstruct standard DXY from the free ECB/Frankfurter daily FX feed.

        Frankfurter reports how many foreign-currency units one USD buys.  Its
        coverage is generally available on the following European business day,
        which makes it a useful fallback when Yahoo rate-limits the server and
        FRED is temporarily unavailable.
        """
        start_date = (dt.date.today() - dt.timedelta(days=days)).isoformat()
        end_date = dt.date.today().isoformat()
        response = requests.get(
            f"https://api.frankfurter.dev/v1/{start_date}..{end_date}",
            params={"base": "USD", "symbols": ",".join(FRANKFURTER_DXY_CURRENCIES)},
            timeout=30,
        )
        response.raise_for_status()
        dated_rates = response.json().get("rates") or {}
        records = []
        for trade_date, rates in dated_rates.items():
            if not all(currency in rates for currency in FRANKFURTER_DXY_CURRENCIES):
                continue
            # Convert USD-base ECB quotations into the conventional DXY inputs.
            eur_usd = 1 / float(rates["EUR"])
            gbp_usd = 1 / float(rates["GBP"])
            dxy = (
                50.14348112
                * (eur_usd ** -0.576)
                * (float(rates["JPY"]) ** 0.136)
                * (gbp_usd ** -0.119)
                * (float(rates["CAD"]) ** 0.091)
                * (float(rates["SEK"]) ** 0.042)
                * (float(rates["CHF"]) ** 0.036)
            )
            records.append((str(trade_date), dxy, dxy, dxy, dxy))
        return records

    for symbol in MACRO_SYMBOLS:
        try:
            frame = client.fx_daily(ts_code=symbol, start_date=start, end_date=end)
            latest = pd.to_datetime(frame["trade_date"].astype(str), errors="coerce").max() if frame is not None and not frame.empty else pd.NaT
            stale = pd.isna(latest) or (dt.datetime.now().date() - latest.date()).days > 7
            if symbol == "USDOLLAR.FXCM" and stale:
                try:
                    count = save_prices(symbol, yahoo_dxy_records(), "Yahoo Finance")
                    if count:
                        print(f"[完成] {symbol} 通过 Yahoo Finance 同步 {count} 条。")
                        continue
                    raise RuntimeError("Yahoo 未返回有效 K 线")
                except Exception as yahoo_error:
                    print(f"[宏观] Yahoo DXY 回退失败：{yahoo_error}；尝试 Frankfurter/ECB 复合 DXY。")
                    try:
                        # Preserve previously stored observations so the fallback
                        # only fills the stale tail and does not revise history.
                        count = save_prices(symbol, frankfurter_dxy_records(), "Frankfurter / ECB 复合 DXY（收盘）", overwrite=False)
                        if count:
                            print(f"[完成] {symbol} 通过 Frankfurter / ECB 复合 DXY 补齐 {count} 条。")
                            continue
                        raise RuntimeError("Frankfurter 未返回有效数据")
                    except Exception as frankfurter_error:
                        print(f"[宏观] Frankfurter DXY 回退失败：{frankfurter_error}；尝试 FRED 复合 DXY。")
                        try:
                            count = save_prices(symbol, fred_dxy_records(), "FRED 复合 DXY（收盘）")
                            if count:
                                print(f"[完成] {symbol} 通过 FRED 复合 DXY 同步 {count} 条。")
                                continue
                            raise RuntimeError("FRED 未返回有效数据")
                        except Exception as fred_error:
                            print(f"[宏观] FRED DXY 回退失败：{fred_error}；保留 Tushare 最后可用数据。")
                            frame = client.fx_daily(ts_code=symbol)
            if frame is None or frame.empty:
                print(f"[宏观] {symbol} 暂无可用日线数据。")
                continue
            records = [(normalize_date(row.get("trade_date", "")), _as_float(row.get("bid_open")), _as_float(row.get("bid_high")), _as_float(row.get("bid_low")), _as_float(row.get("bid_close"))) for _, row in frame.iterrows()]
            count = save_prices(symbol, records, "Tushare")
            print(f"[完成] {symbol} 通过 Tushare 同步 {count} 条。")
            if symbol in FRED_FX_SERIES:
                series_id, source = FRED_FX_SERIES[symbol]
                fallback_count = save_prices(symbol, fred_records(series_id), source, overwrite=False)
                if fallback_count:
                    print(f"[完成] {symbol} 通过 {source} 补齐最近三年历史 {fallback_count} 条。")
        except Exception as exc:
            print(f"[宏观] {symbol} 同步失败：{exc}")


def _as_float(value: object) -> float:
    try:
        return float(value) if pd.notna(value) else 0.0
    except (TypeError, ValueError):
        return 0.0


def fetch_and_save_limit_stocks(limit_type: str) -> int:
    today = dt.datetime.now().strftime("%Y%m%d")
    try:
        if limit_type == "limit_up":
            frame = ak.stock_zt_pool_em(date=today)
        else:
            try:
                frame = ak.stock_zt_pool_dtgc_em(date=today)
            except AttributeError:
                frame = ak.stock_dt_pool_em(date=today)
    except Exception as exc:
        print(f"[涨跌停] {limit_type} 获取失败：{exc}")
        return 0
    trade_date, stamp = normalize_date(today), now_text()
    with connect() as conn:
        conn.execute("DELETE FROM limit_stocks WHERE trade_date=? AND limit_type=?", (trade_date, limit_type))
        if frame is None or frame.empty:
            return 0
        for _, row in frame.iterrows():
            conn.execute("""INSERT INTO limit_stocks
                (trade_date,stock_code,stock_name,last_price,change_pct,limit_type,status,industry,first_limit_time,limit_num,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (trade_date, str(row.get("代码", "")), str(row.get("名称", "")), _as_float(row.get("最新价")),
                 _as_float(row.get("涨跌幅")), limit_type, 1, str(row.get("所属行业", "其他")),
                 str(row.get("首次封板时间", row.get("最后封板时间", ""))), int(_as_float(row.get("连板数", 1)) or 1), stamp))
    return len(frame)


def _limit_pool_worker(limit_type: str, result_queue: mp.Queue) -> None:
    """Child-process entry point so a stuck AkShare request is terminable."""
    try:
        result_queue.put(("ok", fetch_and_save_limit_stocks(limit_type)))
    except BaseException as exc:
        result_queue.put(("error", str(exc)))


def fetch_limit_pool_with_timeout(limit_type: str) -> int:
    context = mp.get_context("fork")
    result_queue = context.Queue(maxsize=1)
    worker = context.Process(target=_limit_pool_worker, args=(limit_type, result_queue), daemon=True)
    worker.start()
    worker.join(LIMIT_POOL_TIMEOUT_SECONDS)
    if worker.is_alive():
        worker.terminate()
        worker.join(5)
        print(f"[涨跌停] {limit_type} 请求超过 {LIMIT_POOL_TIMEOUT_SECONDS} 秒，已终止；下一轮会自动重试。")
        result_queue.close()
        return 0
    try:
        # The process may have exited just before its queue feeder flushes.  Give
        # it a brief chance instead of treating a successful collection as empty.
        status, payload = result_queue.get(timeout=2)
    except Empty:
        print(f"[涨跌停] {limit_type} 子进程未返回结果（退出码 {worker.exitcode}）。")
        result_queue.close()
        return 0
    result_queue.close()
    if status == "error":
        print(f"[涨跌停] {limit_type} 子进程失败：{payload}")
        return 0
    return int(payload)


def realtime_job() -> None:
    # 无论数据源是否暂时返回空数据，都记录本轮任务已实际执行；
    # 唤醒后的健康检查据此判断采集器是否需要自动重启。
    try:
        up = fetch_limit_pool_with_timeout("limit_up")
        down = fetch_limit_pool_with_timeout("limit_down")
        print(f"[完成] 涨停 {up} 家，跌停 {down} 家。")
    finally:
        try:
            RUNTIME_LOG_DIR.mkdir(parents=True, exist_ok=True)
            LIMIT_POOL_HEARTBEAT.write_text(f"{now_text()}\n", encoding="utf-8")
        except Exception as exc:
            print(f"[健康检查] 写入涨跌停任务心跳失败：{exc}")


def sync_recent_market_history() -> None:
    """盘中只回补近两周，避免每小时重复拉取全年历史。"""
    sync_history_turnover(days=14)


def sync_post_close_market_data() -> None:
    """收盘后再次写入最终成交额、指数和涨跌停池快照。"""
    sync_history_turnover(days=30)
    realtime_job()


def sync_recent_margin_history() -> None:
    sync_margin_history(days=14)


def sync_recent_macro_history() -> None:
    sync_macro_history(days=14)


def run_once() -> None:
    init_db(); sync_history_turnover(); sync_margin_history(); sync_macro_history(); realtime_job()


def sync_initial_history() -> None:
    """服务启动后后台回补历史，不能阻塞盘中定时任务。"""
    init_db()
    sync_history_turnover()
    sync_margin_history()
    sync_macro_history()


def start_scheduler(sync_on_start: bool = True) -> None:
    # 电脑睡眠或周末恢复后，合并错过的任务并在 24 小时内补跑一次。
    scheduler = BlockingScheduler(
        timezone="Asia/Shanghai",
        job_defaults={"coalesce": True, "misfire_grace_time": 86400},
    )
    job_options = {"coalesce": True, "misfire_grace_time": 86400, "max_instances": 1}
    scheduler.add_job(realtime_job, "cron", day_of_week="mon-fri", hour="9-15", minute="*/5", **job_options)
    scheduler.add_job(sync_recent_market_history, "cron", day_of_week="mon-fri", hour="9-22", minute="0", **job_options)
    scheduler.add_job(sync_post_close_market_data, "cron", day_of_week="mon-fri", hour="16", minute="5", **job_options)
    scheduler.add_job(sync_recent_margin_history, "cron", day_of_week="mon-fri", hour="9,22", minute="0", **job_options)
    scheduler.add_job(sync_recent_macro_history, "cron", day_of_week="mon-fri", hour="7", minute="10", **job_options)
    # 服务启动即抓一次涨跌停池；随后让 APScheduler 立即接管后续任务。
    scheduler.add_job(realtime_job, "date", run_date=dt.datetime.now(), id="startup_limit_pool", **job_options)
    if sync_on_start:
        # 历史回补在独立 worker 中执行，避免阻塞 5 分钟涨跌停刷新。
        scheduler.add_job(sync_initial_history, "date", run_date=dt.datetime.now(), id="startup_history", **job_options)
    scheduler.start()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--scheduler-only", action="store_true")
    args = parser.parse_args()
    run_once() if args.once else start_scheduler(sync_on_start=not args.scheduler_only)
