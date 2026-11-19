"""Sina ETF secondary-market money flows, independent of fund creations.

Sina's netamount is all actively classified net trade value; r0_net is the
provider's large-ticket/major-order subset. Source amounts are yuan; database
and API amounts are 100m yuan. The source usually publishes ETF rows with a lag.
"""
from __future__ import annotations

import datetime as dt
from contextlib import closing
from zoneinfo import ZoneInfo

from database import connect
from etf_flow import FUNDS, complete_sum, finite, init_flow_db

FIELDS = ("main", "net_amount")
SOURCE = "新浪财经"
SINA_URL = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/MoneyFlow.ssl_qsfx_zjlrqs"


def init_secondary_db():
    init_flow_db()
    with connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS broad_etf_secondary_daily (
          ts_code TEXT NOT NULL, trade_date TEXT NOT NULL,
          main REAL, small REAL, medium REAL, large REAL, super_large REAL,
          main_pct REAL, close REAL, change_pct REAL, checked_at TEXT NOT NULL,
          PRIMARY KEY(ts_code,trade_date));
        CREATE TABLE IF NOT EXISTS broad_etf_secondary_sync (
          ts_code TEXT PRIMARY KEY, checked_at TEXT, success_at TEXT, error TEXT);
        """)
        # This table was first deployed with Eastmoney-only columns. Its first
        # collection failed, so adding the Sina total preserves the live schema.
        if not any(row[1] == "net_amount" for row in conn.execute("PRAGMA table_info(broad_etf_secondary_daily)")):
            conn.execute("ALTER TABLE broad_etf_secondary_daily ADD COLUMN net_amount REAL")


def parse_payload(payload, code, today):
    if not isinstance(payload, list) or not payload:
        raise ValueError("数据源暂无资金流向记录")
    records = {}
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("资金流向字段格式错误")
        date = dt.date.fromisoformat(str(item.get("opendate", "")))
        if date > today:
            raise ValueError("数据日期晚于当前日期")
        main = finite(item.get("r0_net"))
        total = finite(item.get("netamount"))
        if main is None or total is None:
            continue
        ratio = finite(item.get("r0_ratio"))
        change = finite(item.get("changeratio"))
        records[date.isoformat()] = (
            main / 1e8, ratio * 100 if ratio is not None else None,
            finite(item.get("trade")), change * 100 if change is not None else None,
            total / 1e8,
        )
    if not records:
        raise ValueError("资金流向数值均缺失")
    return records


def snapshot(group="全部"):
    selected = [f for f in FUNDS if group == "全部" or f[2] == group]
    codes = [f[0] for f in selected]
    today = dt.datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    with closing(connect()) as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM broad_etf_secondary_daily ORDER BY trade_date") if r["ts_code"] in codes and r["trade_date"] <= today]
        calendar = [r[0] for r in conn.execute("SELECT trade_date FROM broad_etf_calendar WHERE trade_date<=? ORDER BY trade_date", (today,))]
        sync = [dict(r) for r in conn.execute("SELECT * FROM broad_etf_secondary_sync") if r["ts_code"] in codes]
    lookup = {(r["ts_code"], r["trade_date"]): r for r in rows}
    latest = max((r["trade_date"] for r in rows), default=None)
    earliest = min((r["trade_date"] for r in rows), default=None)
    dates = sorted({r["trade_date"] for r in rows} | {d for d in calendar if earliest and earliest <= d <= latest})

    def total(selected_codes, date, field="main"):
        return complete_sum([lookup.get((c, date), {}).get(field) for c in selected_codes])

    def window(selected_codes, count):
        return complete_sum([total(selected_codes, d) for d in dates[-count:]]) if len(dates) >= count else None

    daily = []
    for date in dates:
        available = [lookup[c, date]["main"] for c in codes if (c, date) in lookup]
        daily.append(dict(trade_date=date, **{field: total(codes, date, field) for field in FIELDS},
                          covered=len(available), expected=len(codes), available_main=complete_sum(available)))
    funds = []
    for code, name, category in selected:
        row = lookup.get((code, latest), {})
        last = max((r["trade_date"] for r in rows if r["ts_code"] == code), default=None)
        funds.append(dict(ts_code=code, name=name, group=category, data_date=last,
            **{field: row.get(field) for field in FIELDS}, main_pct=row.get("main_pct"),
            close=row.get("close"), change_pct=row.get("change_pct"), checked_at=row.get("checked_at"),
            flow_5=window([code], 5), flow_20=window([code], 20)))
    expected_date = max(calendar + ([latest] if latest else []), default=None)
    return dict(date=latest, expected_date=expected_date, stale=bool(expected_date and (not latest or latest < expected_date)),
        complete_date=next((d["trade_date"] for d in reversed(daily) if d["main"] is not None), None),
        checked_at=max((r["checked_at"] for r in sync), default=None),
        success_at=max((r["success_at"] for r in sync if r["success_at"]), default=None),
        summary=dict(main=window(codes, 1), flow_5=window(codes, 5), flow_20=window(codes, 20),
                     covered=daily[-1]["covered"] if daily else 0, expected=len(codes)),
        categories=list(dict.fromkeys(f[2] for f in FUNDS)), funds=funds, daily=daily,
        groups=[dict(name=category, main=window([f[0] for f in selected if f[2] == category], 1)) for category in dict.fromkeys(f[2] for f in selected)],
        errors=[dict(ts_code=r["ts_code"], error=r["error"]) for r in sync if r["error"]], source=SOURCE)


def sync_history():
    import requests
    init_secondary_db()
    now = dt.datetime.now(ZoneInfo("Asia/Shanghai"))
    stamp = now.isoformat(timespec="seconds")
    successes = 0
    with requests.Session() as session:
        session.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"})
        for code, name, _ in FUNDS:
            error = None
            try:
                symbol = ("sh" if code.endswith(".SH") else "sz") + code.split(".")[0]
                response = session.get(SINA_URL, params={
                    "page": 1, "num": 240, "sort": "opendate", "asc": 0, "daima": symbol,
                }, timeout=(8, 20))
                response.raise_for_status()
                records = parse_payload(response.json(), code, now.date())
                with connect() as conn:
                    conn.executemany("""INSERT INTO broad_etf_secondary_daily
                        (ts_code,trade_date,main,main_pct,close,change_pct,net_amount,checked_at)
                        VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(ts_code,trade_date) DO UPDATE SET
                        main=excluded.main,main_pct=excluded.main_pct,close=excluded.close,
                        change_pct=excluded.change_pct,net_amount=excluded.net_amount,
                        checked_at=excluded.checked_at""",
                        [(code, date, *values, stamp) for date, values in records.items()])
                successes += 1
                print(f"[二级资金] {name}: {len(records)} 条，最新 {max(records)}", flush=True)
            except Exception as exc:
                error = f"{type(exc).__name__}: {str(exc)[:180]}"
                print(f"[二级资金] {name}: {error}", flush=True)
            with connect() as conn:
                conn.execute("""INSERT INTO broad_etf_secondary_sync VALUES (?,?,?,?)
                    ON CONFLICT(ts_code) DO UPDATE SET checked_at=excluded.checked_at,
                    success_at=COALESCE(excluded.success_at,broad_etf_secondary_sync.success_at),error=excluded.error""",
                    (code, stamp, stamp if error is None else None, error))
    if not successes:
        raise RuntimeError("全部 ETF 采集失败，历史数据已保留")


if __name__ == "__main__":
    from etf_secondary_live import sync_live

    try:
        sync_live()
    except Exception as exc:
        print(f"[今日二级资金] {type(exc).__name__}: {exc}", flush=True)
    sync_history()
