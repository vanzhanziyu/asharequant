import os
import sqlite3
from collections import Counter
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "market_data.db")

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def safe_float(val, default=0.0):
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


@app.get("/api/margin")
def get_margin():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT trade_date, rzrqye FROM margin_history ORDER BY trade_date ASC")
    rows = cursor.fetchall()
    conn.close()
    return [{"trade_date": r["trade_date"], "rzrqye": safe_float(r["rzrqye"])} for r in rows]


@app.get("/api/turnover/overview")
def get_turnover_overview():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(market_turnover_history)")
    columns = [col[1] for col in cursor.fetchall()]
    has_wind = "wind_micro_amount" in columns

    select_fields = "trade_date, total_amount, sh_amount, sz_amount, cyb_amount, kc50_amount, hl_amount"
    if has_wind: select_fields += ", wind_micro_amount"

    cursor.execute(f"SELECT {select_fields} FROM market_turnover_history ORDER BY trade_date DESC LIMIT 10")
    rows = cursor.fetchall()
    conn.close()

    if not rows: return {"data": None}

    latest, prev = rows[0], (rows[1] if len(rows) > 1 else None)
    total_amt, sh_amt, sz_amt = safe_float(latest["total_amount"]), safe_float(latest["sh_amount"]), safe_float(latest["sz_amount"])
    cyb_amt, kc50_amt, hl_amt = safe_float(latest["cyb_amt"]), safe_float(latest["kc50_amt"]), safe_float(latest["hl_amount"])

    wind_micro_amt, wind_micro_diff = 0.0, 0.0
    if has_wind:
        valid_winds = [float(r["wind_micro_amount"]) for r in rows if r["wind_micro_amount"] and 0 < float(r["wind_micro_amount"]) < 1e5]
        if valid_winds: wind_micro_amt = round(valid_winds[0], 2)
        if len(valid_winds) >= 2: wind_micro_diff = round(valid_winds[0] - valid_winds[1], 2)

    return {
        "data": {
            "trade_date": latest["trade_date"],
            "total_amount": total_amt, "sh_amount": sh_amt, "sz_amount": sz_amt,
            "cyb_amount": cyb_amt, "kc50_amount": kc50_amt, "hl_amount": hl_amt,
            "wind_micro_amount": wind_micro_amt,
            "total_diff": round(total_amt - safe_float(prev["total_amount"]), 2) if prev else 0.0,
            "cyb_diff": round(cyb_amt - safe_float(prev["cyb_amt"]), 2) if prev else 0.0,
            "kc50_diff": round(kc50_amt - safe_float(prev["kc50_amt"]), 2) if prev else 0.0,
            "hl_diff": round(hl_amt - safe_float(prev["hl_amt"]), 2) if prev else 0.0,
            "wind_micro_diff": wind_micro_diff,
        }
    }


@app.get("/api/turnover/history")
def get_turnover_history(days: int = 365):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(market_turnover_history)")
    has_wind = "wind_micro_amount" in [col[1] for col in cursor.fetchall()]
    select_fields = "trade_date, total_amount, sh_amount, sz_amount, cyb_amount, kc50_amount, hl_amount"
    if has_wind: select_fields += ", wind_micro_amount"

    cursor.execute(f"SELECT {select_fields} FROM market_turnover_history ORDER BY trade_date DESC LIMIT ?", (days,))
    rows = cursor.fetchall()
    conn.close()
    rows.reverse()

    list_data = []
    for r in rows:
        w_val = None
        if has_wind and r["wind_micro_amount"]:
            try:
                f_w = float(r["wind_micro_amount"])
                if 0 < f_w < 1e5: w_val = round(f_w, 2)
            except: pass
        list_data.append({
            "trade_date": r["trade_date"],
            "total_amount": safe_float(r["total_amount"]),
            "sh_amount": safe_float(r["sh_amount"]),
            "sz_amount": safe_float(r["sz_amount"]),
            "cyb_amount": safe_float(r["cyb_amount"]),
            "kc50_amount": safe_float(r["kc50_amount"]),
            "hl_amount": safe_float(r["hl_amount"]),
            "wind_micro_amount": w_val,
        })
    return {"list": list_data}


@app.get("/api/index/kline")
def get_index_kline(code: str = "000001.SH"):
    conn = get_db()
    cursor = conn.cursor()
    
    if code == "8841431.WI":
        cursor.execute("""
            SELECT trade_date, open, high, low, close, volume, amount_yi as amount
            FROM wind_kline_history
            ORDER BY trade_date ASC
        """)
    else:
        cursor.execute("""
            SELECT trade_date, open, high, low, close, volume, amount
            FROM index_kline_history
            WHERE index_code = ?
            ORDER BY trade_date ASC
        """, (code,))
        
    rows = cursor.fetchall()
    conn.close()

    data = []
    for r in rows:
        data.append({
            "trade_date": r["trade_date"],
            "open": safe_float(r["open"]),
            "high": safe_float(r["high"]),
            "low": safe_float(r["low"]),
            "close": safe_float(r["close"]),
            "volume": safe_float(r["volume"]),
            "amount": safe_float(r["amount"]),
        })
    return {"code": code, "list": data}


@app.get("/api/limit_stocks")
def get_limit_stocks(limit_type: str = "limit_up"):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT MAX(trade_date) FROM limit_stocks")
    latest_date = (cursor.fetchone() or [""])[0]
    if not latest_date:
        conn.close()
        return {"date": "", "stocks": [], "industry_summary": []}

    cursor.execute("""
        SELECT stock_code, stock_name, last_price, change_pct, limit_type, status, industry, first_limit_time, limit_num
        FROM limit_stocks WHERE trade_date=? AND limit_type=?
        ORDER BY limit_num DESC, first_limit_time ASC
    """, (latest_date, limit_type))
    rows = cursor.fetchall()
    conn.close()

    stocks, industries = [], []
    for r in rows:
        ind = r["industry"] if r["industry"] else "其他"
        industries.append(ind)
        stocks.append({
            "stock_code": r["stock_code"], "stock_name": r["stock_name"],
            "last_price": safe_float(r["last_price"]), "change_pct": safe_float(r["change_pct"]),
            "limit_type": r["limit_type"], "status": r["status"], "industry": ind,
            "first_limit_time": r["first_limit_time"], "limit_num": r["limit_num"],
        })

    ind_counts = Counter(industries)
    return {
        "date": latest_date,
        "stocks": stocks,
        "industry_summary": [{"industry": k, "count": v} for k, v in sorted(ind_counts.items(), key=lambda x: x[1], reverse=True)],
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
