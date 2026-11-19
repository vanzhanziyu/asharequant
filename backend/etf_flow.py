"""Selected broad-market ETF net creations, estimated from shares and same-day NAV.

This is an estimate of primary-market subscriptions, not an identification of
investors or secondary-market active buying. Missing dates must never become zero.
"""
from __future__ import annotations

import datetime as dt
import math
from contextlib import closing
from zoneinfo import ZoneInfo

from database import connect

START = "2024-09-23"
FUNDS = (
    ("510300.SH", "华泰柏瑞沪深300ETF", "沪深300"),
    ("510330.SH", "华夏沪深300ETF", "沪深300"),
    ("510310.SH", "易方达沪深300ETF", "沪深300"),
    ("159919.SZ", "嘉实沪深300ETF", "沪深300"),
    ("510050.SH", "华夏上证50ETF", "上证50"),
    ("510500.SH", "南方中证500ETF", "中证500"),
    ("159922.SZ", "嘉实中证500ETF", "中证500"),
    ("512100.SH", "南方中证1000ETF", "中证1000"),
    ("159845.SZ", "华夏中证1000ETF", "中证1000"),
    ("588000.SH", "华夏科创50ETF", "科创50"),
    ("159915.SZ", "易方达创业板ETF", "创业板"),
    ("159338.SZ", "国泰中证A500ETF", "中证A500"),
)


def init_flow_db():
    with connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS broad_etf_daily (
          ts_code TEXT NOT NULL, trade_date TEXT NOT NULL,
          shares REAL, nav REAL, close REAL, amount REAL, updated_at TEXT,
          PRIMARY KEY(ts_code, trade_date));
        CREATE TABLE IF NOT EXISTS broad_etf_calendar (trade_date TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS broad_etf_sync (
          ts_code TEXT PRIMARY KEY, checked_at TEXT, error TEXT);
        """)


def finite(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def estimate_flow(previous, current):
    """Input shares in 10,000 units, NAV in yuan; output in 100 million yuan."""
    if not previous or not current:
        return None, "缺少相邻交易日份额"
    before, after, nav = (finite(previous.get("shares")), finite(current.get("shares")), finite(current.get("nav")))
    if before is None or after is None or before <= 0 or after <= 0:
        return None, "缺少有效份额"
    if nav is None or nav <= 0:
        return None, "当日净值待发布"
    prev_nav = finite(previous.get("nav"))
    if prev_nav and prev_nav > 0:
        share_ratio, nav_ratio = after / before, nav / prev_nav
        if (share_ratio > 1.5 or share_ratio < 2 / 3) and abs(share_ratio * nav_ratio - 1) < 0.15 and abs(nav_ratio - 1) > 0.25:
            return None, "疑似份额折算，暂不计入"
    return (after - before) * nav / 10000, "完整"


def complete_sum(values):
    return round(sum(values), 4) if values and all(v is not None for v in values) else None


def snapshot(group="全部"):
    selected = [fund for fund in FUNDS if group == "全部" or fund[2] == group]
    with closing(connect()) as conn:
        raw = [dict(row) for row in conn.execute("SELECT * FROM broad_etf_daily ORDER BY trade_date")]
        calendar = [row[0] for row in conn.execute("SELECT trade_date FROM broad_etf_calendar ORDER BY trade_date")]
        sync = [dict(row) for row in conn.execute("SELECT * FROM broad_etf_sync")]
    lookup = {(r["ts_code"], r["trade_date"]): r for r in raw}
    latest = max((r["trade_date"] for r in raw if r["shares"] is not None and r["ts_code"] in {f[0] for f in selected}), default=None)
    dates = [d for d in calendar if d >= START and latest and d <= latest]
    previous_dates = dict(zip(calendar[1:], calendar))
    flows, statuses = {}, {}
    for code, _, _ in selected:
        for date in dates:
            flow, status = estimate_flow(lookup.get((code, previous_dates.get(date))), lookup.get((code, date)))
            flows[code, date], statuses[code, date] = flow, status

    def window(codes, size):
        if len(dates) < size:
            return None
        return complete_sum([flows[code, date] for date in dates[-size:] for code in codes])

    daily = []
    for date in dates:
        values = [flows[code, date] for code, _, _ in selected]
        valid = [v for v in values if v is not None]
        daily.append(dict(trade_date=date, net_flow=complete_sum(values), available_flow=round(sum(valid), 4) if valid else None, covered=len(valid), expected=len(selected)))
    # Cumulative curve starts only once a complete sample is available. A gap
    # then invalidates the subsequent cumulative points until backfilled.
    cumulative, started, broken = 0.0, False, False
    for day in daily:
        if day["net_flow"] is None:
            broken = broken or started
            day["cumulative"] = None
        elif broken:
            day["cumulative"] = None
        else:
            started = True
            cumulative += day["net_flow"]
            day["cumulative"] = round(cumulative, 4)
    funds = []
    for code, name, category in selected:
        row = lookup.get((code, latest), {})
        nav, shares = row.get("nav"), row.get("shares")
        amount_history = [lookup.get((code, date), {}).get("amount") for date in dates[-21:-1]]
        amount_avg = sum(amount_history) / 20 if len(amount_history) == 20 and all(v is not None and v > 0 for v in amount_history) else None
        funds.append(dict(ts_code=code, name=name, group=category,
            net_flow=window([code], 1), flow_5=window([code], 5), flow_20=window([code], 20),
            nav=nav, shares=shares, assets=shares * nav / 10000 if shares and nav else None,
            amount=row.get("amount"), volume_multiple=row["amount"] / amount_avg if row.get("amount") and amount_avg else None,
            status=statuses.get((code, latest), "等待采集")))
    groups = []
    for category in dict.fromkeys(f[2] for f in selected):
        codes = [f[0] for f in selected if f[2] == category]
        groups.append(dict(name=category, net_flow=window(codes, 1), flow_5=window(codes, 5), flow_20=window(codes, 20)))
    codes = [f[0] for f in selected]
    last_complete = next((d["trade_date"] for d in reversed(daily) if d["net_flow"] is not None), None)
    return dict(date=latest, complete_date=last_complete, groups=groups, funds=funds, daily=daily,
        summary=dict(net_flow=window(codes, 1), flow_5=window(codes, 5), flow_20=window(codes, 20),
                     covered=daily[-1]["covered"] if daily else 0, expected=len(selected)),
        categories=list(dict.fromkeys(f[2] for f in FUNDS)),
        checked_at=max((s["checked_at"] for s in sync), default=None),
        errors=[s for s in sync if s["error"]],
        method="估算净申购（亿元）=（当日份额－前一交易日份额）（万份）×当日单位净值÷10000；非买方身份识别。")


def sync_history():
    from collector import pro_api, normalize_date
    init_flow_db()
    now = dt.datetime.now(ZoneInfo("Asia/Shanghai"))
    end = now.strftime("%Y%m%d")
    stamp = now.isoformat(timespec="seconds")
    client = pro_api()
    # Explicit calendar prevents turning a multi-day gap into one day's flow.
    cal = client.trade_cal(exchange="SSE", start_date="20240901", end_date=end, is_open="1")
    if cal is None or cal.empty:
        raise RuntimeError("交易日历为空，未改动已有份额数据")
    with connect() as conn:
        conn.executemany("INSERT OR IGNORE INTO broad_etf_calendar VALUES (?)", [(normalize_date(x),) for x in cal["cal_date"]])
    for code, name, _ in FUNDS:
        with closing(connect()) as conn:
            count = conn.execute("SELECT COUNT(*) FROM broad_etf_daily WHERE ts_code=? AND shares IS NOT NULL AND nav IS NOT NULL", (code,)).fetchone()[0]
        start = (now.date() - dt.timedelta(days=35)).strftime("%Y%m%d") if count >= 20 else "20240901"
        combined, errors = {}, []
        for api, date_field, mapping in (
            ("fund_share", "trade_date", {"fd_share": "shares"}),
            ("fund_nav", "nav_date", {"unit_nav": "nav"}),
            ("fund_daily", "trade_date", {"close": "close", "amount": "amount"}),
        ):
            try:
                frame = getattr(client, api)(ts_code=code, start_date=start, end_date=end)
                if frame is None or frame.empty:
                    raise ValueError("无返回记录")
                if len(frame) >= 2000:
                    raise ValueError("达到分页上限，需缩短查询区间")
                if "update_flag" in frame.columns:
                    frame = frame.sort_values("update_flag", ascending=False)
                frame = frame.drop_duplicates(subset=[date_field], keep="first")
                for _, row in frame.iterrows():
                    date = normalize_date(row[date_field])
                    record = combined.setdefault(date, {})
                    for source, target in mapping.items():
                        value = finite(row.get(source))
                        # ETF daily amount is in thousand yuan.
                        record[target] = value / 100000 if target == "amount" and value is not None else value
            except Exception as exc:
                errors.append(f"{api}: {str(exc)[:180]}")
        with connect() as conn:
            for date, values in combined.items():
                conn.execute("""INSERT INTO broad_etf_daily VALUES (?,?,?,?,?,?,?)
                    ON CONFLICT(ts_code,trade_date) DO UPDATE SET
                    shares=COALESCE(excluded.shares,broad_etf_daily.shares),
                    nav=COALESCE(excluded.nav,broad_etf_daily.nav),
                    close=COALESCE(excluded.close,broad_etf_daily.close),
                    amount=COALESCE(excluded.amount,broad_etf_daily.amount), updated_at=excluded.updated_at""",
                    (code, date, values.get("shares"), values.get("nav"), values.get("close"), values.get("amount"), stamp))
            conn.execute("INSERT OR REPLACE INTO broad_etf_sync VALUES (?,?,?)", (code, stamp, "; ".join(errors)))
        print(f"[宽基] {name}: {len(combined)} 条; {('; '.join(errors)) or '同步成功'}", flush=True)


if __name__ == "__main__":
    sync_history()
