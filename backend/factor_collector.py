"""Persistent data pipeline for the A-share factor screener.

The pipeline deliberately stores reusable inputs (universe, daily market data,
dividends, financial statements and adjusted-close factors) separately from
factor outputs.  This lets later factors reuse the same audit trail rather
than requesting the same vendor data again.
"""
from __future__ import annotations

import argparse
import datetime as dt
import math
import os
import threading
import time
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

import pandas as pd
import tushare as ts
from apscheduler.schedulers.blocking import BlockingScheduler
from dotenv import dotenv_values

from database import BASE_DIR, connect, init_db

ENV_FILE = BASE_DIR / ".env"
HISTORY_DAYS = 365 * 3
LOOKBACK_TRADING_DAYS = 735
PORTFOLIO_SIZE = 100
MARKET_HISTORY_BATCH_DAYS = 12
FUNDAMENTAL_BATCH_SIZE = 15
FACTORS = ("market_cap_large", "market_cap_micro", "dividend_yield", "ebitda_cagr")
MARKET_CAP_FACTORS = {"market_cap_large", "market_cap_micro"}
LOCK = threading.Lock()


def stamp() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def iso_date(value: object) -> str:
    text = str(value or "").split(" ")[0]
    return f"{text[:4]}-{text[4:6]}-{text[6:]}" if len(text) == 8 and text.isdigit() else text


def number(value: object) -> float | None:
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def tushare_client():
    token = dotenv_values(ENV_FILE).get("TUSHARE_TOKEN") or os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise RuntimeError("未配置 TUSHARE_TOKEN")
    return ts.pro_api(token)


def set_state(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute(
            """INSERT INTO factor_sync_state (key,value,updated_at) VALUES (?,?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
            (key, value, stamp()),
        )


def get_state(key: str) -> str | None:
    with connect() as conn:
        row = conn.execute("SELECT value FROM factor_sync_state WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def sync_trade_calendar(client) -> list[str]:
    """Cache enough open dates for a three-year backtest plus factor lookbacks."""
    first = (dt.date.today() - dt.timedelta(days=365 * 7)).strftime("%Y%m%d")
    last = dt.date.today().strftime("%Y%m%d")
    frame = client.trade_cal(exchange="SSE", start_date=first, end_date=last, is_open="1", fields="cal_date,is_open")
    dates = sorted({iso_date(value) for value in (frame["cal_date"] if frame is not None and not frame.empty else [])})
    if dates:
        with connect() as conn:
            conn.executemany("INSERT OR IGNORE INTO factor_trade_calendar (trade_date) VALUES (?)", [(value,) for value in dates])
        cached_trade_dates.cache_clear()
        previous_trade_date.cache_clear()
    return dates


@lru_cache(maxsize=1)
def cached_trade_dates() -> list[str]:
    with connect() as conn:
        return [row["trade_date"] for row in conn.execute("SELECT trade_date FROM factor_trade_calendar ORDER BY trade_date")]


@lru_cache(maxsize=10_000)
def previous_trade_date(date: str, offset: int) -> str | None:
    dates = cached_trade_dates()
    if not dates:
        return None
    prior = [item for item in dates if item <= date]
    index = len(prior) - 1 - offset
    return prior[index] if index >= 0 else None


def sync_stock_basic(client) -> int:
    frame = client.stock_basic(exchange="", list_status="L", fields="ts_code,symbol,name,industry,list_date")
    if frame is None or frame.empty:
        raise RuntimeError("Tushare 未返回上市股票列表")
    rows = []
    for _, item in frame.iterrows():
        code, symbol, name = str(item.get("ts_code", "")), str(item.get("symbol", "")), str(item.get("name", ""))
        # 沪深 A 股：排除北交所，排除 ST 与 *ST；保留科创板和创业板。
        if not code.endswith((".SH", ".SZ")) or not symbol or not name:
            continue
        is_st = int("ST" in name.upper())
        rows.append((code, symbol, name, str(item.get("industry") or "其他"), iso_date(item.get("list_date")), is_st, stamp()))
    with connect() as conn:
        conn.executemany(
            """INSERT INTO factor_stock_basic (ts_code,stock_code,stock_name,industry,list_date,is_st,updated_at)
               VALUES (?,?,?,?,?,?,?) ON CONFLICT(ts_code) DO UPDATE SET
               stock_code=excluded.stock_code,stock_name=excluded.stock_name,industry=excluded.industry,
               list_date=excluded.list_date,is_st=excluded.is_st,updated_at=excluded.updated_at""",
            rows,
        )
    set_state("stock_basic_status", "completed")
    return len(rows)


def _daily_frames(client, trade_date: str):
    compact = trade_date.replace("-", "")
    basic = client.daily_basic(trade_date=compact, fields="ts_code,trade_date,close,total_mv")
    daily = client.daily(trade_date=compact, fields="ts_code,trade_date,close,pct_chg")
    adj = client.adj_factor(trade_date=compact, fields="ts_code,trade_date,adj_factor")
    return basic, daily, adj


def store_daily_for_date(client, trade_date: str) -> int:
    basic, daily, adj = _daily_frames(client, trade_date)
    if basic is None or basic.empty:
        return 0
    market = {str(row["ts_code"]): (number(row.get("close")), number(row.get("pct_chg"))) for _, row in daily.iterrows()} if daily is not None else {}
    factors = {str(row["ts_code"]): number(row.get("adj_factor")) for _, row in adj.iterrows()} if adj is not None else {}
    rows = []
    for _, row in basic.iterrows():
        code = str(row.get("ts_code", ""))
        close = number(row.get("close")) or (market.get(code) or (None, None))[0]
        total_mv = number(row.get("total_mv"))
        if not code or close is None or total_mv is None:
            continue
        pct_chg = (market.get(code) or (None, None))[1]
        rows.append((code, trade_date, close, pct_chg, total_mv, factors.get(code), "Tushare", stamp()))
    with connect() as conn:
        conn.executemany(
            """INSERT INTO factor_stock_daily (ts_code,trade_date,close,pct_chg,total_mv,adj_factor,source,updated_at)
               VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(ts_code,trade_date) DO UPDATE SET
               close=excluded.close,pct_chg=excluded.pct_chg,total_mv=excluded.total_mv,
               adj_factor=excluded.adj_factor,source=excluded.source,updated_at=excluded.updated_at""",
            rows,
        )
    return len(rows)


def latest_remote_trade_date(client, dates: list[str]) -> str | None:
    # The date may be a weekend, holiday, or before post-close processing.  Try
    # the most recent ten exchange dates and use the first full daily snapshot.
    for trade_date in reversed([value for value in dates if value <= dt.date.today().isoformat()][-10:]):
        try:
            if store_daily_for_date(client, trade_date) > 1000:
                return trade_date
        except Exception as exc:
            print(f"[因子] {trade_date} 日行情暂不可用：{exc}", flush=True)
    return None


def _eligible_stock_count() -> int:
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM factor_stock_basic WHERE is_st=0 AND ts_code LIKE '%.SH' OR (is_st=0 AND ts_code LIKE '%.SZ')").fetchone()[0]


def sync_fundamental_batch(client, field: str, batch_size: int = FUNDAMENTAL_BATCH_SIZE) -> int:
    if field not in {"dividend", "financial"}:
        raise ValueError(field)
    synced_column = "dividend_synced_at" if field == "dividend" else "financial_synced_at"
    with connect() as conn:
        codes = [row["ts_code"] for row in conn.execute(
            f"""SELECT ts_code FROM factor_stock_basic
                WHERE is_st=0 AND (ts_code LIKE '%.SH' OR ts_code LIKE '%.SZ') AND {synced_column} IS NULL
                ORDER BY ts_code LIMIT ?""", (batch_size,)
        )]
    if not codes:
        set_state(f"{field}_status", "completed")
        return 0
    done = 0
    for code in codes:
        try:
            if field == "dividend":
                frame = client.dividend(ts_code=code, fields="ts_code,ann_date,record_date,ex_date,cash_div_tax")
                records = []
                if frame is not None and not frame.empty:
                    for _, row in frame.iterrows():
                        record_date, cash = iso_date(row.get("record_date")), number(row.get("cash_div_tax"))
                        if record_date and cash is not None and cash > 0:
                            records.append((code, iso_date(row.get("ann_date")), record_date, iso_date(row.get("ex_date")), cash, "Tushare", stamp()))
                with connect() as conn:
                    conn.executemany(
                        """INSERT INTO factor_dividend (ts_code,ann_date,record_date,ex_date,cash_div_tax,source,updated_at)
                           VALUES (?,?,?,?,?,?,?) ON CONFLICT(ts_code,record_date,ann_date) DO UPDATE SET
                           ex_date=excluded.ex_date,cash_div_tax=excluded.cash_div_tax,source=excluded.source,updated_at=excluded.updated_at""", records,
                    )
                    conn.execute("UPDATE factor_stock_basic SET dividend_synced_at=? WHERE ts_code=?", (stamp(), code))
            else:
                frame = client.fina_indicator(ts_code=code, fields="ts_code,ann_date,end_date,ebitda")
                records = []
                if frame is not None and not frame.empty:
                    for _, row in frame.iterrows():
                        ann_date, end_date, ebitda = iso_date(row.get("ann_date")), iso_date(row.get("end_date")), number(row.get("ebitda"))
                        if ann_date and end_date and ebitda is not None:
                            records.append((code, ann_date, end_date, ebitda, "Tushare", stamp()))
                with connect() as conn:
                    conn.executemany(
                        """INSERT INTO factor_financial (ts_code,ann_date,end_date,ebitda,source,updated_at)
                           VALUES (?,?,?,?,?,?) ON CONFLICT(ts_code,ann_date,end_date) DO UPDATE SET
                           ebitda=excluded.ebitda,source=excluded.source,updated_at=excluded.updated_at""", records,
                    )
                    conn.execute("UPDATE factor_stock_basic SET financial_synced_at=? WHERE ts_code=?", (stamp(), code))
            done += 1
        except Exception as exc:
            print(f"[因子] {field} {code} 获取失败，将稍后重试：{exc}", flush=True)
        # 2000 积分账号的单股财务接口有频控；慢速稳定优先于一次性爆发请求。
        time.sleep(0.7)
    return done


def universe_values(factor_name: str, signal_date: str) -> list[dict]:
    """Calculate a factor cross-section from persisted data as it was known then."""
    with connect() as conn:
        if factor_name in MARKET_CAP_FACTORS:
            rows = conn.execute(
                """SELECT d.ts_code,d.close,d.pct_chg,d.total_mv,d.adj_factor,b.industry,d.total_mv AS value
                   FROM factor_stock_daily d JOIN factor_stock_basic b USING(ts_code)
                   WHERE d.trade_date=? AND b.is_st=0 AND (b.ts_code LIKE '%.SH' OR b.ts_code LIKE '%.SZ')
                     AND d.total_mv>0 ORDER BY value DESC""", (signal_date,)
            ).fetchall()
        elif factor_name == "dividend_yield":
            first = previous_trade_date(signal_date, LOOKBACK_TRADING_DAYS) or (dt.date.fromisoformat(signal_date) - dt.timedelta(days=365 * 3)).isoformat()
            rows = conn.execute(
                """SELECT d.ts_code,d.close,d.pct_chg,d.total_mv,d.adj_factor,b.industry,
                       SUM(v.cash_div_tax) / 3.0 / d.close * 100.0 AS value
                   FROM factor_stock_daily d JOIN factor_stock_basic b USING(ts_code)
                   JOIN factor_dividend v ON v.ts_code=d.ts_code AND v.record_date BETWEEN ? AND ?
                   WHERE d.trade_date=? AND b.is_st=0 AND (b.ts_code LIKE '%.SH' OR b.ts_code LIKE '%.SZ') AND d.close>0
                   GROUP BY d.ts_code,d.close,d.pct_chg,d.total_mv,d.adj_factor,b.industry HAVING value>0 ORDER BY value DESC""",
                (first, signal_date, signal_date),
            ).fetchall()
        elif factor_name == "ebitda_cagr":
            # Use comparable annual reports.  The latest announcement available
            # on each signal date anchors a 735-trading-day lookback window.
            rows = []
            basic_rows = conn.execute(
                """SELECT d.ts_code,d.close,d.pct_chg,d.total_mv,d.adj_factor,b.industry
                   FROM factor_stock_daily d JOIN factor_stock_basic b USING(ts_code)
                   WHERE d.trade_date=? AND b.is_st=0 AND (b.ts_code LIKE '%.SH' OR b.ts_code LIKE '%.SZ')""", (signal_date,)
            ).fetchall()
            reports_by_code: dict[str, list] = defaultdict(list)
            report_rows = conn.execute(
                """SELECT f.ts_code,f.ann_date,f.ebitda FROM factor_financial f
                   JOIN factor_stock_basic b USING(ts_code)
                   WHERE f.ann_date<=? AND substr(f.end_date,6,5)='12-31' AND f.ebitda>0
                     AND b.is_st=0 AND (b.ts_code LIKE '%.SH' OR b.ts_code LIKE '%.SZ')
                   ORDER BY f.ts_code,f.ann_date""", (signal_date,)
            ).fetchall()
            for report in report_rows:
                reports_by_code[report["ts_code"]].append(report)
            for item in basic_rows:
                reports = reports_by_code.get(item["ts_code"], [])
                if len(reports) < 2:
                    continue
                latest_date = reports[-1]["ann_date"]
                first_date = previous_trade_date(latest_date, LOOKBACK_TRADING_DAYS)
                reports = [report for report in reports if first_date is None or report["ann_date"] >= first_date]
                if len(reports) < 2:
                    continue
                start, end = reports[0], reports[-1]
                years = max((dt.date.fromisoformat(end["ann_date"]) - dt.date.fromisoformat(start["ann_date"])).days / 365.25, 0)
                if years <= 0 or end["ebitda"] <= 0 or start["ebitda"] <= 0:
                    continue
                value = ((float(end["ebitda"]) / float(start["ebitda"])) ** (1 / years) - 1) * 100
                if math.isfinite(value):
                    rows.append({**dict(item), "value": value})
            return sorted((dict(row) for row in rows), key=lambda item: item["value"], reverse=True)
        else:
            raise ValueError(factor_name)
    return [dict(row) for row in rows]


def selected_values(factor_name: str, signal_date: str) -> list[dict]:
    """Select the fixed portfolio for factors whose direction is intrinsic."""
    values = universe_values(factor_name, signal_date)
    if factor_name == "market_cap_micro":
        return values[-PORTFOLIO_SIZE:]
    return values[:PORTFOLIO_SIZE]


def store_snapshot(factor_name: str, signal_date: str) -> int:
    values = universe_values(factor_name, signal_date)
    # Fundamental data arrives in small batches. Keep an earlier usable
    # snapshot if the newest batch has not produced any valid sample yet.
    if not values:
        return 0
    with connect() as conn:
        conn.execute("DELETE FROM factor_universe_snapshot WHERE factor_name=? AND signal_date=?", (factor_name, signal_date))
        total = len(values)
        conn.executemany(
            """INSERT INTO factor_universe_snapshot
               (factor_name,signal_date,ts_code,factor_value,factor_rank,universe_count,close,pct_chg,total_mv,industry,calculated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            [(factor_name, signal_date, item["ts_code"], item["value"], index, total, item["close"], item.get("pct_chg"), item.get("total_mv"), item.get("industry") or "其他", stamp())
             for index, item in enumerate(values, start=1)],
        )
    return len(values)


def snapshots_ready() -> dict[str, bool]:
    with connect() as conn:
        active = conn.execute("SELECT COUNT(*) FROM factor_stock_basic WHERE is_st=0 AND (ts_code LIKE '%.SH' OR ts_code LIKE '%.SZ')").fetchone()[0]
        dividend = conn.execute("SELECT COUNT(*) FROM factor_stock_basic WHERE is_st=0 AND (ts_code LIKE '%.SH' OR ts_code LIKE '%.SZ') AND dividend_synced_at IS NOT NULL").fetchone()[0]
        financial = conn.execute("SELECT COUNT(*) FROM factor_stock_basic WHERE is_st=0 AND (ts_code LIKE '%.SH' OR ts_code LIKE '%.SZ') AND financial_synced_at IS NOT NULL").fetchone()[0]
    return {
        "market_cap_large": active > 0,
        "market_cap_micro": active > 0,
        "dividend_yield": active > 0 and dividend >= active,
        "ebitda_cagr": active > 0 and financial >= active,
    }


def rebuild_latest_snapshots() -> None:
    with connect() as conn:
        latest = conn.execute("SELECT MAX(trade_date) FROM factor_stock_daily").fetchone()[0]
    if not latest:
        return
    for factor_name in FACTORS:
        # Publish each available partial cross-section immediately instead of
        # waiting for the multi-hour full-universe fundamental backfill.
        count = store_snapshot(factor_name, latest)
        if count:
            print(f"[因子] {factor_name} {latest} 快照 {count} 只。", flush=True)


def sync_current() -> bool:
    if not LOCK.acquire(blocking=False):
        return False
    try:
        init_db()
        client = tushare_client()
        dates = sync_trade_calendar(client)
        basic_count = sync_stock_basic(client)
        latest = latest_remote_trade_date(client, dates)
        if latest:
            store_snapshot("market_cap_large", latest)
            store_snapshot("market_cap_micro", latest)
        print(f"[因子] 当前股票池更新：{basic_count} 只，行情日 {latest or '--'}。", flush=True)
        return bool(latest)
    except Exception as exc:
        print(f"[因子] 当前数据更新失败：{exc}", flush=True)
        return False
    finally:
        LOCK.release()


def sync_market_history_batch() -> None:
    if not LOCK.acquire(blocking=False):
        return
    try:
        init_db(); client = tushare_client(); dates = sync_trade_calendar(client)
        cutoff = (dt.date.today() - dt.timedelta(days=HISTORY_DAYS)).isoformat()
        target = [value for value in dates if cutoff <= value <= dt.date.today().isoformat()]
        with connect() as conn:
            have = {row["trade_date"] for row in conn.execute("SELECT trade_date FROM factor_stock_daily WHERE trade_date>=? GROUP BY trade_date HAVING COUNT(*)>1000", (cutoff,))}
        missing = [value for value in target if value not in have][:MARKET_HISTORY_BATCH_DAYS]
        if not missing:
            set_state("market_history_status", "completed")
            rebuild_latest_snapshots()
            return
        for trade_date in missing:
            count = store_daily_for_date(client, trade_date)
            print(f"[因子] 历史行情 {trade_date}：{count} 只。", flush=True)
            time.sleep(0.25)
        set_state("market_history_status", f"backfilling:{len(have) + len(missing)}/{len(target)}")
    except Exception as exc:
        print(f"[因子] 历史行情回补失败：{exc}", flush=True)
    finally:
        LOCK.release()


def sync_fundamentals() -> None:
    if not LOCK.acquire(blocking=False):
        return
    try:
        init_db(); client = tushare_client()
        if not get_state("stock_basic_status"):
            sync_stock_basic(client)
        dividend_count = sync_fundamental_batch(client, "dividend")
        financial_count = sync_fundamental_batch(client, "financial")
        if dividend_count or financial_count:
            print(f"[因子] 基础面回补：分红 {dividend_count} 只，EBITDA {financial_count} 只。", flush=True)
        rebuild_latest_snapshots()
    except Exception as exc:
        print(f"[因子] 基础面回补失败：{exc}", flush=True)
    finally:
        LOCK.release()


def calculate_performance(factor_names: tuple[str, ...] = FACTORS) -> None:
    """Build equal-weight, next-trading-day forward-adjusted factor returns."""
    if not LOCK.acquire(blocking=False):
        return
    try:
        init_db()
        cutoff = (dt.date.today() - dt.timedelta(days=HISTORY_DAYS)).isoformat()
        with connect() as conn:
            dates = [row["trade_date"] for row in conn.execute(
                "SELECT trade_date FROM factor_stock_daily WHERE trade_date>=? GROUP BY trade_date HAVING COUNT(*)>1000 ORDER BY trade_date", (cutoff,)
            )]
        if len(dates) < 2:
            return
        for factor_name in factor_names:
            nav = 1.0
            with connect() as conn:
                conn.execute("DELETE FROM factor_portfolio_daily WHERE factor_name=? AND trade_date>=?", (factor_name, dates[1]))
                conn.execute("DELETE FROM factor_portfolio_members WHERE factor_name=? AND signal_date>=?", (factor_name, dates[0]))
            for signal_date, trade_date in zip(dates[:-1], dates[1:]):
                selected = selected_values(factor_name, signal_date)
                if not selected:
                    continue
                codes = [item["ts_code"] for item in selected]
                placeholders = ",".join("?" for _ in codes)
                with connect() as conn:
                    next_prices = {row["ts_code"]: row for row in conn.execute(
                        f"SELECT ts_code,close,adj_factor FROM factor_stock_daily WHERE trade_date=? AND ts_code IN ({placeholders})", (trade_date, *codes)
                    )}
                returns = []
                for item in selected:
                    next_row = next_prices.get(item["ts_code"])
                    current_adj = float(item["close"]) * (number(item.get("adj_factor")) or 1.0)
                    if next_row and current_adj > 0:
                        next_adj = float(next_row["close"]) * (number(next_row["adj_factor"]) or 1.0)
                        returns.append(next_adj / current_adj - 1)
                if not returns:
                    continue
                daily_return = sum(returns) / len(returns)
                nav *= 1 + daily_return
                with connect() as conn:
                    conn.executemany(
                        "INSERT OR REPLACE INTO factor_portfolio_members (factor_name,signal_date,ts_code,factor_value,factor_rank) VALUES (?,?,?,?,?)",
                        [(factor_name, signal_date, item["ts_code"], item["value"], index) for index, item in enumerate(selected, 1)],
                    )
                    conn.execute(
                        """INSERT OR REPLACE INTO factor_portfolio_daily
                           (factor_name,trade_date,signal_date,holding_count,daily_return_pct,nav,calculated_at)
                           VALUES (?,?,?,?,?,?,?)""",
                        (factor_name, trade_date, signal_date, len(returns), daily_return * 100, nav, stamp()),
                    )
            print(f"[因子] {factor_name} 三年组合收益已重算。", flush=True)
        market_complete = get_state("market_history_status") == "completed"
        readiness = snapshots_ready()
        set_state("performance_status", "completed" if market_complete and all(readiness.values()) else "backfilling")
    except Exception as exc:
        print(f"[因子] 收益率计算失败：{exc}", flush=True)
    finally:
        LOCK.release()


def start_scheduler() -> None:
    init_db()
    scheduler = BlockingScheduler(timezone="Asia/Shanghai", job_defaults={"coalesce": True, "misfire_grace_time": 86400})
    opts = {"coalesce": True, "misfire_grace_time": 86400, "max_instances": 1}
    scheduler.add_job(sync_current, "date", run_date=dt.datetime.now(), id="factor_start_current", **opts)
    scheduler.add_job(sync_current, "cron", day_of_week="mon-fri", hour="16", minute="20", id="factor_post_close", **opts)
    scheduler.add_job(sync_market_history_batch, "interval", minutes=5, id="factor_history", **opts)
    scheduler.add_job(sync_fundamentals, "interval", minutes=2, id="factor_fundamentals", **opts)
    scheduler.add_job(calculate_performance, "date", run_date=dt.datetime.now(), id="factor_start_performance", **opts)
    scheduler.add_job(calculate_performance, "interval", minutes=20, id="factor_performance", **opts)
    scheduler.start()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--current", action="store_true")
    parser.add_argument("--history-batch", action="store_true")
    parser.add_argument("--fundamentals", action="store_true")
    parser.add_argument("--performance", action="store_true")
    parser.add_argument("--performance-factors", default="", help="逗号分隔的需重算因子")
    args = parser.parse_args()
    if args.current:
        sync_current()
    elif args.history_batch:
        sync_market_history_batch()
    elif args.fundamentals:
        sync_fundamentals()
    elif args.performance or args.performance_factors:
        selected = tuple(item.strip() for item in args.performance_factors.split(",") if item.strip()) or FACTORS
        calculate_performance(selected)
    else:
        start_scheduler()
