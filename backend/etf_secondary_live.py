"""Same-day broad ETF secondary-market order-flow snapshots from Eastmoney.

These estimates have a different classification from the Sina historical
series. Keep them in a separate table and never append them to that series.
"""
from __future__ import annotations

import datetime as dt
import json
from contextlib import closing
from zoneinfo import ZoneInfo

from database import connect
from etf_flow import FUNDS, complete_sum, finite

URL = "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"
BATCH_URLS = (
    "https://push2.eastmoney.com/api/qt/ulist.np/get",
    "https://91.push2.eastmoney.com/api/qt/ulist.np/get",
)
READER_URL = "https://r.jina.ai/" + BATCH_URLS[0]
SOURCE = "东方财富"
FIELDS = ("main", "small", "medium", "large", "super_large")
SHANGHAI = ZoneInfo("Asia/Shanghai")
MAX_DIRECT_FALLBACK = 3


def init_live_db():
    with connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS broad_etf_secondary_live (
          ts_code TEXT NOT NULL, trade_date TEXT NOT NULL,
          main REAL, small REAL, medium REAL, large REAL, super_large REAL,
          checked_at TEXT NOT NULL,
          PRIMARY KEY(ts_code,trade_date));
        CREATE TABLE IF NOT EXISTS broad_etf_secondary_live_history (
          ts_code TEXT NOT NULL, trade_date TEXT NOT NULL, checked_at TEXT NOT NULL,
          main REAL, small REAL, medium REAL, large REAL, super_large REAL,
          PRIMARY KEY(ts_code,checked_at));
        CREATE INDEX IF NOT EXISTS idx_broad_etf_secondary_live_history_day
          ON broad_etf_secondary_live_history(ts_code,trade_date,checked_at);
        CREATE TABLE IF NOT EXISTS broad_etf_secondary_live_sync (
          ts_code TEXT PRIMARY KEY, checked_at TEXT, success_at TEXT, error TEXT);
        """)
        # Retain the latest pre-upgrade value as the first available history point.
        conn.execute("""INSERT OR IGNORE INTO broad_etf_secondary_live_history
            (ts_code,trade_date,checked_at,main,small,medium,large,super_large)
            SELECT ts_code,trade_date,checked_at,main,small,medium,large,super_large
            FROM broad_etf_secondary_live""")


def parse_live_payload(payload, code, today):
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise ValueError("今日资金流向尚未提供")
    expected_market = 1 if code.endswith(".SH") else 0
    if data.get("code") != code.split(".")[0] or data.get("market") != expected_market:
        raise ValueError("返回的 ETF 代码或市场不匹配")
    lines = data.get("klines")
    if not isinstance(lines, list) or not lines:
        raise ValueError("今日资金流向尚未提供")
    parts = str(lines[-1]).split(",")
    if len(parts) != 6:
        raise ValueError("资金流向字段数量错误")
    date = dt.date.fromisoformat(parts[0][:10])
    if date != today:
        raise ValueError(f"数据停留在 {date.isoformat()}")
    values = [finite(value) for value in parts[1:]]
    if any(value is None for value in values):
        raise ValueError("资金流向数值缺失")
    # Eastmoney f52-f56: main, small, medium, large, super-large in yuan.
    main, small, medium, large, super_large = values
    if abs(main - (large + super_large)) > max(100, abs(main) * 0.0001):
        raise ValueError("主力净额与大单分项不一致")
    return {"trade_date": date.isoformat(), **dict(zip(FIELDS, (v / 1e8 for v in values)))}


def parse_batch_payload(payload, code, today, now=None):
    """Read one ETF from a single batched Eastmoney quote response."""
    data = payload.get("data") if isinstance(payload, dict) else None
    rows = data.get("diff") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError("批量资金流向暂无记录")
    number = code.split(".")[0]
    market = 1 if code.endswith(".SH") else 0
    matches = [row for row in rows if isinstance(row, dict) and str(row.get("f12")) == number
               and row.get("f13") == market]
    if len(matches) != 1:
        raise ValueError("批量资金流向缺少或重复 ETF 代码")
    row = matches[0]
    try:
        quote_at = dt.datetime.fromtimestamp(int(row["f124"]), SHANGHAI)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError("批量资金流向时间戳无效") from exc
    quote_date = quote_at.date()
    if quote_date != today:
        raise ValueError(f"批量资金流向停留在 {quote_date.isoformat()}")
    if now is not None and dt.time(9, 30) <= now.time() <= dt.time(15, 0):
        age = now - quote_at
        if age > dt.timedelta(minutes=15) or age < -dt.timedelta(minutes=2):
            raise ValueError("批量资金流向报价超过盘中时效限制")
    # Eastmoney f62/f66/f72/f78/f84: main/super-large/large/medium/small, in yuan.
    amounts = [finite(row.get(field)) for field in ("f62", "f66", "f72", "f78", "f84")]
    if any(value is None for value in amounts):
        raise ValueError("批量资金流向金额缺失")
    main, super_large, large, medium, small = amounts
    if abs(main - (super_large + large)) > max(100, abs(main) * 0.0001):
        raise ValueError("批量主力净额与大单分项不一致")
    return {"trade_date": quote_date.isoformat(), "main": main / 1e8,
            "small": small / 1e8, "medium": medium / 1e8,
            "large": large / 1e8, "super_large": super_large / 1e8}


def live_snapshot(group="全部"):
    selected = [fund for fund in FUNDS if group == "全部" or fund[2] == group]
    today = dt.datetime.now(SHANGHAI).date().isoformat()
    codes = [fund[0] for fund in selected]
    with closing(connect()) as conn:
        rows = {row["ts_code"]: dict(row) for row in conn.execute(
            """SELECT l.*,
              (SELECT h.main FROM broad_etf_secondary_live_history h
               WHERE h.ts_code=l.ts_code AND h.trade_date=l.trade_date AND h.checked_at<l.checked_at
               ORDER BY h.checked_at DESC LIMIT 1) AS previous_main,
              (SELECT h.checked_at FROM broad_etf_secondary_live_history h
               WHERE h.ts_code=l.ts_code AND h.trade_date=l.trade_date AND h.checked_at<l.checked_at
               ORDER BY h.checked_at DESC LIMIT 1) AS previous_checked_at
               FROM broad_etf_secondary_live l WHERE l.trade_date=?""", (today,)) if row["ts_code"] in codes}
        sync = [dict(row) for row in conn.execute("SELECT * FROM broad_etf_secondary_live_sync") if row["ts_code"] in codes]
    funds = []
    for code, name, category in selected:
        row = rows.get(code, {})
        previous_main = row.get("previous_main")
        funds.append({"ts_code": code, "name": name, "group": category,
                      **{field: row.get(field) for field in FIELDS},
                      "change_5m": round(row["main"] - previous_main, 4)
                      if row.get("main") is not None and previous_main is not None else None,
                      "previous_checked_at": row.get("previous_checked_at"),
                      "checked_at": row.get("checked_at")})
    values = [fund["main"] for fund in funds]
    available = [value for value in values if value is not None]
    groups = []
    for category in dict.fromkeys(fund[2] for fund in selected):
        category_values = [fund["main"] for fund in funds if fund["group"] == category]
        groups.append({"name": category, "main": complete_sum(category_values),
                       "covered": sum(value is not None for value in category_values),
                       "expected": len(category_values)})
    return {"date": today if available else None, "source": SOURCE,
            "summary": {"main": complete_sum(values),
                        "available_main": round(sum(available), 4) if available else None,
                        "covered": len(available), "expected": len(codes)},
            "funds": funds, "groups": groups,
            "checked_at": max((row["checked_at"] for row in rows.values()), default=None),
            "success_at": max((row["success_at"] for row in sync if row["success_at"]), default=None),
            "errors": [{"ts_code": row["ts_code"], "error": row["error"]} for row in sync if row["error"]]}


def live_history(ts_code):
    fund = next((fund for fund in FUNDS if fund[0] == ts_code), None)
    if fund is None:
        raise ValueError("ETF 不在固定样本中")
    today = dt.datetime.now(SHANGHAI).date().isoformat()
    with closing(connect()) as conn:
        points = [dict(row) for row in conn.execute("""SELECT checked_at,main,small,medium,large,super_large
            FROM broad_etf_secondary_live_history WHERE ts_code=? AND trade_date=?
            ORDER BY checked_at""", (ts_code, today))]
    return {"ts_code": ts_code, "name": fund[1], "trade_date": today, "points": points}


def sync_live():
    import requests

    init_live_db()
    now = dt.datetime.now(SHANGHAI)
    stamp = now.isoformat(timespec="seconds")
    successes = 0
    with requests.Session() as session:
        session.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"})
        secids = ",".join(("1" if code.endswith(".SH") else "0") + "." + code.split(".")[0]
                          for code, _, _ in FUNDS)
        batch_payload, batch_error = None, None
        for batch_url in (*BATCH_URLS, READER_URL):
            try:
                response = session.get(batch_url, params={
                    "fltt": 2, "secids": secids,
                    "fields": "f12,f13,f62,f66,f72,f78,f84,f124",
                }, headers={"X-No-Cache": "true", "X-Engine": "curl"} if batch_url == READER_URL else None,
                    timeout=(5, 25) if batch_url == READER_URL else (5, 10))
                response.raise_for_status()
                if batch_url == READER_URL:
                    _, marker, body = response.text.partition("Markdown Content:\n")
                    if not marker:
                        raise ValueError("转发接口未返回资金流向内容")
                    payload = json.loads(body.strip())
                else:
                    payload = response.json()
                if not isinstance(payload, dict) or not isinstance((payload.get("data") or {}).get("diff"), list):
                    raise ValueError("批量接口返回格式错误")
                batch_payload = payload
                print(f"[今日二级资金] 批量快照来源：{'Jina Reader 转发东方财富' if batch_url == READER_URL else '东方财富直连'}", flush=True)
                break
            except Exception as exc:
                batch_error = f"{type(exc).__name__}: {str(exc)[:160]}"
        # A failed batch must not trigger another 12 rapid requests from this IP.
        # Only fill a few missing symbols individually when the batch itself worked.
        direct_attempts = 0
        for code, name, _ in FUNDS:
            error = None
            try:
                if batch_payload is None:
                    raise ValueError(f"批量接口不可用：{batch_error}")
                try:
                    row = parse_batch_payload(batch_payload, code, now.date(), now)
                except ValueError:
                    if direct_attempts >= MAX_DIRECT_FALLBACK:
                        raise
                    direct_attempts += 1
                    response = session.get(URL, params={
                        "lmt": 1, "klt": 101,
                        "secid": ("1" if code.endswith(".SH") else "0") + "." + code.split(".")[0],
                        "fields1": "f1,f2,f3,f7",
                        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63",
                    }, timeout=(5, 10))
                    response.raise_for_status()
                    row = parse_live_payload(response.json(), code, now.date())
                with connect() as conn:
                    conn.execute("""INSERT INTO broad_etf_secondary_live_history
                        (ts_code,trade_date,checked_at,main,small,medium,large,super_large)
                        VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(ts_code,checked_at) DO UPDATE SET
                        main=excluded.main,small=excluded.small,medium=excluded.medium,
                        large=excluded.large,super_large=excluded.super_large""",
                        (code, row["trade_date"], stamp, *(row[field] for field in FIELDS)))
                    conn.execute("""INSERT INTO broad_etf_secondary_live
                        (ts_code,trade_date,main,small,medium,large,super_large,checked_at)
                        VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(ts_code,trade_date) DO UPDATE SET
                        main=excluded.main,small=excluded.small,medium=excluded.medium,
                        large=excluded.large,super_large=excluded.super_large,
                        checked_at=excluded.checked_at""",
                        (code, row["trade_date"], *(row[field] for field in FIELDS), stamp))
                successes += 1
                print(f"[今日二级资金] {name}: {row['trade_date']}", flush=True)
            except Exception as exc:
                error = f"{type(exc).__name__}: {str(exc)[:180]}"
                print(f"[今日二级资金] {name}: {error}", flush=True)
            with connect() as conn:
                conn.execute("""INSERT INTO broad_etf_secondary_live_sync VALUES (?,?,?,?)
                    ON CONFLICT(ts_code) DO UPDATE SET checked_at=excluded.checked_at,
                    success_at=COALESCE(excluded.success_at,broad_etf_secondary_live_sync.success_at),
                    error=excluded.error""", (code, stamp, stamp if error is None else None, error))
    if not successes:
        raise RuntimeError("全部 ETF 今日快照采集失败；历史数据已保留")


if __name__ == "__main__":
    sync_live()
