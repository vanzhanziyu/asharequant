from __future__ import annotations

import datetime as dt
from collections import Counter
from contextlib import closing

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from database import connect, init_db

init_db()
app = FastAPI(title="A 股行情监控 API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
VIX_SYMBOLS = {"510050": "50ETF", "510300": "沪深300ETF", "588080": "科创50ETF", "159915": "创业板ETF"}
FACTOR_LABELS = {"market_cap": "市值因子", "dividend_yield": "纯红利因子", "ebitda_cagr": "EBITDA 增速"}


def num(value: object) -> float:
    try: return float(value) if value is not None else 0.0
    except (TypeError, ValueError): return 0.0


@app.get("/api/health")
def health(): return {"status": "ok"}


@app.get("/api/margin")
def margin():
    with closing(connect()) as conn:
        rows = conn.execute("SELECT trade_date,rzrqye FROM margin_history WHERE rzrqye IS NOT NULL ORDER BY trade_date").fetchall()
    return [{"trade_date": row["trade_date"], "rzrqye": num(row["rzrqye"])} for row in rows]


@app.get("/api/turnover/overview")
def turnover_overview():
    fields = "trade_date,total_amount,sh_amount,sz_amount,cyb_amount,kc50_amount,hl_amount,wind_micro_amount"
    with closing(connect()) as conn:
        rows = conn.execute(f"SELECT {fields} FROM market_turnover_history WHERE total_amount IS NOT NULL ORDER BY trade_date DESC LIMIT 2").fetchall()
        wind = conn.execute("SELECT trade_date,wind_micro_amount FROM market_turnover_history WHERE wind_micro_amount>0 ORDER BY trade_date DESC LIMIT 2").fetchall()
    if not rows: return {"data": None}
    current, previous = rows[0], rows[1] if len(rows) > 1 else None
    data = {key: num(current[key]) for key in ("total_amount", "sh_amount", "sz_amount", "cyb_amount", "kc50_amount", "hl_amount")}
    data.update({"trade_date": current["trade_date"], "standard_date": current["trade_date"]})
    for key in ("total", "cyb", "kc50", "hl"):
        field = f"{key}_amount" if key != "total" else "total_amount"
        data[f"{key}_diff"] = round(data[field] - (num(previous[field]) if previous else 0), 2)
    data["wind_micro_amount"] = num(wind[0]["wind_micro_amount"]) if wind else 0
    data["wind_micro_date"] = wind[0]["trade_date"] if wind else current["trade_date"]
    data["wind_micro_diff"] = round(data["wind_micro_amount"] - num(wind[1]["wind_micro_amount"]), 2) if len(wind) > 1 else 0
    return {"data": data}


@app.get("/api/turnover/history")
def turnover_history(days: int = Query(365, ge=1, le=2000)):
    with closing(connect()) as conn:
        rows = conn.execute("""SELECT trade_date,total_amount,sh_amount,sz_amount,cyb_amount,kc50_amount,hl_amount,wind_micro_amount
            FROM market_turnover_history WHERE total_amount IS NOT NULL ORDER BY trade_date DESC LIMIT ?""", (days,)).fetchall()
    return {"list": [{key: (row["trade_date"] if key == "trade_date" else num(row[key])) for key in row.keys()} for row in reversed(rows)]}


@app.get("/api/limit_stocks")
def limit_stocks(limit_type: str = Query("limit_up", pattern="^limit_(up|down)$")):
    with closing(connect()) as conn:
        latest = conn.execute("SELECT MAX(trade_date) FROM limit_stocks").fetchone()[0]
        rows = conn.execute("""SELECT stock_code,stock_name,last_price,change_pct,limit_type,status,industry,first_limit_time,limit_num FROM limit_stocks
            WHERE trade_date=? AND limit_type=? ORDER BY limit_num DESC,first_limit_time""", (latest, limit_type)).fetchall() if latest else []
    stocks = [{**dict(row), "last_price": num(row["last_price"]), "change_pct": num(row["change_pct"]), "industry": row["industry"] or "其他"} for row in rows]
    counts = Counter(item["industry"] for item in stocks)
    return {"date": latest or "", "stocks": stocks, "industry_summary": [{"industry": key, "count": value} for key, value in counts.most_common()]}


@app.get("/api/index/kline")
def index_kline(code: str = "000001.SH", days: int = Query(365, ge=1, le=2000)):
    with closing(connect()) as conn:
        if code == "8841423.WI":
            rows = conn.execute("SELECT trade_date,open,high,low,close,volume,amount_yi AS amount FROM wind_kline_history ORDER BY trade_date DESC LIMIT ?", (days,)).fetchall()
        else:
            rows = conn.execute("SELECT trade_date,open,high,low,close,volume,amount FROM index_kline_history WHERE index_code=? ORDER BY trade_date DESC LIMIT ?", (code, days)).fetchall()
    return {"code": code, "list": [{key: row["trade_date"] if key == "trade_date" else num(row[key]) for key in row.keys()} for row in reversed(rows)]}


@app.get("/api/macro/treasury")
def macro_treasury(days: int = Query(1095, ge=1, le=2000)):
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    with closing(connect()) as conn:
        rows = conn.execute("""SELECT trade_date,m3,y2,y5,y10,y30 FROM us_treasury_history
            WHERE trade_date >= ? AND y2 IS NOT NULL AND y10 IS NOT NULL
            ORDER BY trade_date DESC LIMIT ?""", (cutoff, days * 2)).fetchall()
    records = []
    for row in reversed(rows):
        record = {key: row["trade_date"] if key == "trade_date" else num(row[key]) for key in row.keys()}
        record["spread_10_2"] = round(record["y10"] - record["y2"], 3)
        records.append(record)
    return {"list": records}


@app.get("/api/macro/price")
def macro_price(symbol: str = Query("USDCNH.FXCM"), days: int = Query(1095, ge=1, le=2000)):
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    with closing(connect()) as conn:
        rows = conn.execute("""SELECT trade_date,open,high,low,close,source FROM macro_price_history
            WHERE symbol=? AND trade_date >= ? ORDER BY trade_date DESC LIMIT ?""", (symbol, cutoff, days * 2)).fetchall()
    return {"symbol": symbol, "list": [{key: row[key] if key in ("trade_date", "source") else num(row[key]) for key in row.keys()} for row in reversed(rows)]}


def factor_progress(conn) -> dict:
    active = conn.execute("SELECT COUNT(*) FROM factor_stock_basic WHERE is_st=0 AND (ts_code LIKE '%.SH' OR ts_code LIKE '%.SZ')").fetchone()[0]
    dividend = conn.execute("SELECT COUNT(*) FROM factor_stock_basic WHERE is_st=0 AND (ts_code LIKE '%.SH' OR ts_code LIKE '%.SZ') AND dividend_synced_at IS NOT NULL").fetchone()[0]
    financial = conn.execute("SELECT COUNT(*) FROM factor_stock_basic WHERE is_st=0 AND (ts_code LIKE '%.SH' OR ts_code LIKE '%.SZ') AND financial_synced_at IS NOT NULL").fetchone()[0]
    rows = conn.execute("SELECT key,value,updated_at FROM factor_sync_state").fetchall()
    return {
        "universe": active, "dividend_ready": dividend, "financial_ready": financial,
        "states": {row["key"]: {"value": row["value"], "updated_at": row["updated_at"]} for row in rows},
    }


@app.get("/api/factors/status")
def factors_status():
    with closing(connect()) as conn:
        return factor_progress(conn)


@app.get("/api/factors/pool")
def factor_pool(factor: str = Query("market_cap"), rank: int = Query(100, ge=-5000, le=5000)):
    """Current non-ST沪深 factor pool. Positive rank means top N, negative bottom N."""
    if factor not in FACTOR_LABELS or rank == 0:
        return {"error": "不支持的因子或排名筛选", "stocks": [], "industry_summary": []}
    with closing(connect()) as conn:
        latest = conn.execute("SELECT MAX(signal_date) FROM factor_universe_snapshot WHERE factor_name=?", (factor,)).fetchone()[0]
        progress = factor_progress(conn)
        if not latest:
            return {"factor": factor, "name": FACTOR_LABELS[factor], "date": None, "rank_filter": rank, "stocks": [], "industry_summary": [], "progress": progress}
        universe = conn.execute("SELECT MAX(universe_count) FROM factor_universe_snapshot WHERE factor_name=? AND signal_date=?", (factor, latest)).fetchone()[0]
        clause, params = ("s.factor_rank<=?", (abs(rank),)) if rank > 0 else ("s.factor_rank>?", (max(0, int(universe) - abs(rank)),))
        rows = conn.execute(
            f"""SELECT b.stock_code,b.stock_name,s.close,s.pct_chg,s.total_mv,s.industry,s.factor_value,s.factor_rank,
                       mc.factor_rank AS market_cap_rank
                FROM factor_universe_snapshot s JOIN factor_stock_basic b USING(ts_code)
                LEFT JOIN factor_universe_snapshot mc ON mc.ts_code=s.ts_code AND mc.factor_name='market_cap' AND mc.signal_date=s.signal_date
                WHERE s.factor_name=? AND s.signal_date=? AND {clause}
                ORDER BY s.factor_rank""", (factor, latest, *params),
        ).fetchall()
    stocks = [{key: (row[key] if key in ("stock_code", "stock_name", "industry") else num(row[key])) for key in row.keys()} for row in rows]
    counts = Counter(item["industry"] or "其他" for item in stocks)
    return {
        "factor": factor, "name": FACTOR_LABELS[factor], "date": latest, "rank_filter": rank,
        "universe_count": universe, "stocks": stocks,
        "industry_summary": [{"industry": key, "count": value} for key, value in counts.most_common()],
        "progress": progress,
    }


@app.get("/api/factors/performance")
def factor_performance(factor: str = Query("market_cap"), days: int = Query(1095, ge=30, le=1200)):
    if factor not in FACTOR_LABELS:
        return {"error": "不支持的因子", "list": []}
    cutoff = (dt.date.today() - dt.timedelta(days=days + 15)).isoformat()
    with closing(connect()) as conn:
        rows = conn.execute(
            """SELECT trade_date,signal_date,holding_count,daily_return_pct,nav,calculated_at
               FROM factor_portfolio_daily WHERE factor_name=? AND trade_date>=? ORDER BY trade_date""", (factor, cutoff),
        ).fetchall()
        progress = factor_progress(conn)
    records = [{key: (row[key] if key in ("trade_date", "signal_date", "calculated_at") else num(row[key])) for key in row.keys()} for row in rows]
    return {"factor": factor, "name": FACTOR_LABELS[factor], "list": records, "progress": progress, "portfolio_size": 100}


@app.get("/api/vix")
def etf_vix(symbol: str = Query("510050"), days: int = Query(3000, ge=30, le=4000)):
    if symbol not in VIX_SYMBOLS:
        return {"error": "不支持的 ETF 期权标的", "list": []}
    cutoff = (dt.date.today() - dt.timedelta(days=days + 14)).isoformat()
    with closing(connect()) as conn:
        rows = conn.execute(
            """SELECT trade_date,underlying_close,standard_vix_pct,near_expiry,next_expiry,near_days,next_days,term_selection,calculated_at
            FROM etf_vix_daily WHERE symbol=? AND trade_date>=? AND standard_vix_pct IS NOT NULL
            ORDER BY trade_date""", (symbol, cutoff),
        ).fetchall()
        all_values = conn.execute(
            "SELECT standard_vix_pct FROM etf_vix_daily WHERE symbol=? AND standard_vix_pct IS NOT NULL", (symbol,)
        ).fetchall()
        state = conn.execute("SELECT value,updated_at FROM vix_sync_state WHERE key='history_status'").fetchone()
    records = [{key: row[key] if key in ("trade_date", "near_expiry", "next_expiry", "term_selection", "calculated_at") else num(row[key]) for key in row.keys()} for row in rows]
    values = [num(row["standard_vix_pct"]) for row in all_values]
    latest = records[-1] if records else None
    percentile = round(100 * sum(value <= num(latest["standard_vix_pct"]) for value in values) / len(values), 2) if latest and values else None
    return {
        "symbol": symbol, "name": VIX_SYMBOLS[symbol], "list": records,
        "stats": {
            "current": num(latest["standard_vix_pct"]) if latest else None,
            "current_date": latest["trade_date"] if latest else None,
            "maximum": max(values) if values else None, "minimum": min(values) if values else None,
            "percentile": percentile, "observations": len(values),
        },
        "history_status": state["value"] if state else "backfilling",
        "history_updated_at": state["updated_at"] if state else None,
    }


@app.get("/api/pcivd")
def etf_pcivd(symbol: str = Query("510050"), days: int = Query(3000, ge=30, le=4000)):
    if symbol not in VIX_SYMBOLS:
        return {"error": "不支持的 ETF 期权标的", "list": []}
    cutoff = (dt.date.today() - dt.timedelta(days=days + 14)).isoformat()
    with closing(connect()) as conn:
        rows = conn.execute(
            """SELECT trade_date,underlying_close,pcivd_pct,put_iv_pct,call_iv_pct,expiry_date,
            days_to_expiry,strike,put_option_id,call_option_id,calculated_at
            FROM etf_pcivd_daily WHERE symbol=? AND trade_date>=? AND pcivd_pct IS NOT NULL
            ORDER BY trade_date""", (symbol, cutoff),
        ).fetchall()
        all_values = conn.execute(
            "SELECT pcivd_pct FROM etf_pcivd_daily WHERE symbol=? AND pcivd_pct IS NOT NULL", (symbol,)
        ).fetchall()
        state = conn.execute("SELECT value,updated_at FROM vix_sync_state WHERE key='pcivd_history_source'").fetchone()
    string_columns = {"trade_date", "expiry_date", "put_option_id", "call_option_id", "calculated_at"}
    records = [{key: row[key] if key in string_columns else num(row[key]) for key in row.keys()} for row in rows]
    values = [num(row["pcivd_pct"]) for row in all_values]
    latest = records[-1] if records else None
    percentile = round(100 * sum(value <= num(latest["pcivd_pct"]) for value in values) / len(values), 2) if latest and values else None
    return {
        "symbol": symbol, "name": VIX_SYMBOLS[symbol], "list": records,
        "stats": {
            "current": num(latest["pcivd_pct"]) if latest else None,
            "current_date": latest["trade_date"] if latest else None,
            "maximum": max(values) if values else None, "minimum": min(values) if values else None,
            "percentile": percentile, "observations": len(values),
        },
        "history_status": "completed" if state and state["value"] else "backfilling",
        "history_updated_at": state["updated_at"] if state else None,
    }


@app.get("/api/iv-hv")
def etf_iv_hv(symbol: str = Query("510050"), days: int = Query(3000, ge=30, le=4000)):
    """Standard VIX less aligned 30-trading-day historical volatility."""
    if symbol not in VIX_SYMBOLS:
        return {"error": "不支持的 ETF 期权标的", "list": []}
    cutoff = (dt.date.today() - dt.timedelta(days=days + 14)).isoformat()
    with closing(connect()) as conn:
        rows = conn.execute(
            """SELECT trade_date,underlying_close,standard_vix_pct,historical_vol_pct,iv_hv_pct,calculated_at
            FROM etf_iv_hv_daily WHERE symbol=? AND trade_date>=? ORDER BY trade_date""",
            (symbol, cutoff),
        ).fetchall()
        all_values = conn.execute(
            "SELECT iv_hv_pct FROM etf_iv_hv_daily WHERE symbol=?", (symbol,)
        ).fetchall()
        state = conn.execute("SELECT value,updated_at FROM vix_sync_state WHERE key='iv_hv_history_source'").fetchone()
    string_columns = {"trade_date", "calculated_at"}
    records = [{key: row[key] if key in string_columns else num(row[key]) for key in row.keys()} for row in rows]
    values = [num(row["iv_hv_pct"]) for row in all_values]
    latest = records[-1] if records else None
    percentile = round(100 * sum(value <= num(latest["iv_hv_pct"]) for value in values) / len(values), 2) if latest and values else None
    return {
        "symbol": symbol, "name": VIX_SYMBOLS[symbol], "list": records,
        "stats": {
            "current": num(latest["iv_hv_pct"]) if latest else None,
            "current_date": latest["trade_date"] if latest else None,
            "maximum": max(values) if values else None, "minimum": min(values) if values else None,
            "percentile": percentile, "observations": len(values),
        },
        "history_status": "completed" if state and state["value"] else "backfilling",
        "history_updated_at": state["updated_at"] if state else None,
    }


@app.get("/api/iv-curves")
def etf_iv_curves(symbol: str = Query("510050"), days: int = Query(45, ge=30, le=90)):
    """Return stored daily Skew and Cone shapes for the date-slider UI.

    Skew uses the first (current) expiry and only out-of-the-money legs.  Cone
    chooses the strike closest to spot which has both a call and a put for each
    expiry month, so both lines are a like-for-like ATM comparison.
    """
    if symbol not in VIX_SYMBOLS:
        return {"error": "不支持的 ETF 期权标的", "dates": [], "curves": {}}
    cutoff = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    with closing(connect()) as conn:
        rows = conn.execute(
            """SELECT trade_date,underlying_close,expiry_date,days_to_expiry,strike,option_type,option_id,iv_pct
            FROM etf_option_iv_surface WHERE symbol=? AND trade_date>=?
            ORDER BY trade_date,expiry_date,strike,option_type""",
            (symbol, cutoff),
        ).fetchall()
        state = conn.execute("SELECT value,updated_at FROM vix_sync_state WHERE key='iv_surface_history_source'").fetchone()
    by_day: dict[str, list[dict]] = {}
    for row in rows:
        by_day.setdefault(row["trade_date"], []).append({
            "underlying_close": num(row["underlying_close"]), "expiry_date": row["expiry_date"],
            "days_to_expiry": int(row["days_to_expiry"]), "strike": num(row["strike"]),
            "option_type": row["option_type"], "option_id": row["option_id"], "iv_pct": num(row["iv_pct"]),
        })
    curves: dict[str, dict] = {}
    for trade_date, observations in by_day.items():
        spot = observations[0]["underlying_close"]
        expiries = sorted({item["expiry_date"] for item in observations})
        front_expiry = expiries[0] if expiries else None
        front = [item for item in observations if item["expiry_date"] == front_expiry]
        puts = [{"strike": item["strike"], "iv_pct": item["iv_pct"]} for item in front
                if item["option_type"] == "认沽" and item["strike"] < spot]
        calls = [{"strike": item["strike"], "iv_pct": item["iv_pct"]} for item in front
                 if item["option_type"] == "认购" and item["strike"] > spot]
        cone = []
        for expiry in expiries:
            pairs: dict[float, dict[str, dict]] = {}
            for item in observations:
                if item["expiry_date"] == expiry:
                    pairs.setdefault(item["strike"], {})[item["option_type"]] = item
            valid = [(abs(strike - spot), strike, pair) for strike, pair in pairs.items()
                     if "认购" in pair and "认沽" in pair]
            if not valid:
                continue
            _, strike, pair = min(valid, key=lambda item: item[0])
            cone.append({
                "expiry_date": expiry, "label": expiry[:7], "days_to_expiry": pair["认购"]["days_to_expiry"],
                "strike": strike, "call_iv_pct": pair["认购"]["iv_pct"], "put_iv_pct": pair["认沽"]["iv_pct"],
            })
        curves[trade_date] = {
            "underlying_close": spot,
            "skew": {
                "expiry_date": front_expiry, "days_to_expiry": front[0]["days_to_expiry"] if front else None,
                "puts": sorted(puts, key=lambda item: item["strike"]),
                "calls": sorted(calls, key=lambda item: item["strike"]),
            },
            "cone": cone,
        }
    dates = sorted(curves)
    return {
        "symbol": symbol, "name": VIX_SYMBOLS[symbol], "dates": dates,
        "latest_date": dates[-1] if dates else None, "curves": curves,
        "history_status": "completed" if state and state["value"] else "backfilling",
        "history_updated_at": state["updated_at"] if state else None,
    }


@app.post("/api/vix/refresh")
def refresh_etf_vix():
    """Queue a fresh source pull; vix_collector consumes it within one minute."""
    with connect() as conn:
        conn.execute("INSERT INTO vix_refresh_requests (requested_at,status) VALUES (?, 'pending')", (dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),))
        request_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    return {"status": "queued", "request_id": request_id, "message": "已排队，VIX 采集器将在一分钟内开始刷新。"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000)
