"""Persistent 30-day VIX-style ETF option index collector.

SSE/SZSE public contract ledgers identify expired contracts; Tushare opt_daily
supplies the historical closing-price input.  It writes only derived results
and audit inputs to the dashboard SQLite database, so the API never needs to
recalculate an option chain on a page request.
"""
from __future__ import annotations

import argparse
import bisect
import datetime as dt
import io
import json
import math
import os
import re
import threading
import time
import urllib.request
from urllib.error import HTTPError
import xml.etree.ElementTree as etree
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from apscheduler.schedulers.blocking import BlockingScheduler
from dotenv import dotenv_values
import tushare as ts

from database import BASE_DIR, connect, init_db

RATE = 0.015
TARGET_DAYS = 30
HV_WINDOW_DAYS = 30
# All persisted volatility histories share this fixed inception date.  It is
# intentionally not a rolling window: percentile statistics stay comparable.
HISTORY_START_DATE = dt.date(2019, 1, 1)
CONTRACT_LOOKBACK_DAYS = 430
PCIVD_ROLL_DAYS = 8
IV_SURFACE_DAYS = 45
# Cone needs the complete listed term structure, including the next seasonal
# quarter.  220 days covers the March expiry when viewed in September.
FUTURE_CONTRACT_DAYS = 220
CACHE = BASE_DIR / ".cache" / "vix"
LOCK = threading.Lock()
ETFS = {
    "510050": {"name": "50ETF", "exchange": "SSE", "sina": "sh510050"},
    "510300": {"name": "沪深300ETF", "exchange": "SSE", "sina": "sh510300"},
    # 该品种与此前验证的易方达科创50ETF期权保持一致。
    "588080": {"name": "科创50ETF", "exchange": "SSE", "sina": "sh588080"},
    "159915": {"name": "创业板ETF", "exchange": "SZSE", "sina": "sz159915"},
}
SSE_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.sse.com.cn/disclosure/optioninfo/preinfo/"}
SZSE_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.szse.cn/option/quotation/contract/contractchange/index.html"}
SINA_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://stock.finance.sina.com.cn/"}
TUSHARE_ENV_FILE = BASE_DIR / ".env"
TUSHARE_HISTORY_VERSION = "tushare_opt_daily_from_2019_v2"
# Chinese retail option terminals conventionally include the expiry date in
# the IV time fraction.  E.g. after 2026-09-15 close, a 2026-09-23 contract
# uses 9/365, rather than 8/365.  This aligns displayed IV with their quotes.
PCIVD_HISTORY_VERSION = "pcivd_near_atm_inclusive_expiry_v2"
IV_SURFACE_HISTORY_VERSION = "iv_surface_recent_month_inclusive_expiry_v2"
IV_HV_HISTORY_VERSION = "iv_hv_30_trading_day_v1"


def stamp() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def date_range(first: dt.date, last: dt.date):
    while first <= last:
        yield first
        first += dt.timedelta(days=1)


def date_chunks(first: dt.date, last: dt.date, *, calendar_days: int = 15):
    """Small date ranges stay comfortably below Tushare's 15,000-row limit."""
    cursor = first
    while cursor <= last:
        end = min(last, cursor + dt.timedelta(days=calendar_days - 1))
        yield cursor, end
        cursor = end + dt.timedelta(days=1)


def tushare_client():
    """Load a local secret without ever placing it in logs or the database."""
    token = dotenv_values(TUSHARE_ENV_FILE).get("TUSHARE_TOKEN") or os.environ.get("TUSHARE_TOKEN")
    if not token:
        raise RuntimeError(f"未配置 Tushare Token：请在 {TUSHARE_ENV_FILE} 设置 TUSHARE_TOKEN")
    return ts.pro_api(token)


def tushare_option_id(ts_code: object) -> str:
    """10012345.SH / 90012345.SZ -> exchange contract code used by our master table."""
    return str(ts_code).split(".", 1)[0]


def request_bytes(url: str, cache: Path | None = None, *, headers: dict[str, str], refresh: bool = False, attempts: int = 4) -> bytes:
    if cache and cache.exists() and not refresh:
        return cache.read_bytes()
    failure: Exception | None = None
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(request, timeout=75) as response:
                result = response.read()
            if cache:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_bytes(result)
            return result
        except Exception as exc:  # retry transient public-source blocks politely
            failure = exc
            # A delisted contract can permanently return this response; retrying
            # it only delays every other contract in the historical batch.
            if isinstance(exc, HTTPError) and exc.code in (404, 456):
                break
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"请求失败：{url} ({failure})")


def request_text(url: str, cache: Path | None = None, *, headers: dict[str, str], refresh: bool = False) -> str:
    return request_bytes(url, cache, headers=headers, refresh=refresh).decode("utf-8", errors="replace")


def parse_jsonp(text: str) -> dict:
    left, right = text.find("("), text.rfind(")")
    return json.loads(text[left + 1:right] if left >= 0 and right > left else text)


def discover_sse_contracts(symbol: str, first: dt.date, last: dt.date) -> list[dict]:
    folder = CACHE / symbol / "sse-listings"

    def one(day: dt.date) -> list[dict]:
        url = (
            "https://query.sse.com.cn/commonQuery.do?jsonCallBack=cb&isPagination=false"
            "&sqlId=SSE_ZQPZ_YSP_OPTZSXT_ADJUST_INFO_HYXG_SEARCH_L"
            f"&adjustDate={day:%Y%m%d}&securityCode={symbol}"
        )
        return parse_jsonp(request_text(url, folder / f"{day:%Y%m%d}.json", headers=SSE_HEADERS)).get("result") or []

    records: list[dict] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(one, day) for day in date_range(first, last)]
        for future in as_completed(futures):
            records.extend(future.result())
    return list({row["SECURITY_ID"]: row for row in records}.values())


def xlsx_rows(data: bytes) -> list[dict[str, str]]:
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(io.BytesIO(data)) as book:
        root = etree.fromstring(book.read("xl/worksheets/sheet1.xml"))
    table: list[dict[str, str]] = []
    for row in root.findall(f".//{ns}row"):
        cells = {}
        for cell in row.findall(f"{ns}c"):
            match = re.match(r"[A-Z]+", cell.attrib.get("r", ""))
            if match:
                cells[match.group()] = "".join(cell.itertext()).strip()
        if cells:
            table.append(cells)
    if not table:
        return []
    headings = table[0]
    return [{headings.get(column, column): value for column, value in row.items()} for row in table[1:]]


def discover_szse_contracts(symbol: str, first: dt.date, last: dt.date) -> list[dict]:
    """Read public SZSE contract-listing reports in yearly slices."""
    records: list[dict] = []
    cursor = first
    while cursor <= last:
        segment_end = min(last, dt.date(cursor.year, 12, 31))
        cache = CACHE / symbol / "szse-listings" / f"{cursor:%Y%m%d}_{segment_end:%Y%m%d}.xlsx"
        url = (
            "https://www.szse.cn/api/report/ShowReport?SHOWTYPE=xlsx&CATALOGID=option_hybg&TABKEY=tab1"
            f"&txtKsrq={cursor:%Y-%m-%d}&txtZzrq={segment_end:%Y-%m-%d}"
        )
        for row in xlsx_rows(request_bytes(url, cache, headers=SZSE_HEADERS)):
            if not row.get("合约代码", "").startswith(symbol):
                continue
            try:
                records.append({
                    "SECURITY_ID": row["合约编码"], "CONTRACT_ID": row["合约代码"],
                    "CALL_OR_PUT": "认购" if row.get("类型") == "认购" else "认沽",
                    "EXERCISE_PRICE": row["行权价"], "START_DATE": row["挂牌日期"].replace("-", ""),
                    "EXPIRE_DATE": dt.datetime.strptime(row["到期日"], "%Y-%m-%d").strftime("%Y%m%d"),
                })
            except (KeyError, ValueError):
                continue
        cursor = segment_end + dt.timedelta(days=1)
    return list({row["SECURITY_ID"]: row for row in records}.values())


def discover_tushare_contracts(symbol: str, first: dt.date, last: dt.date) -> list[dict]:
    """Read the complete historical contract master in one Tushare request.

    Unlike an exchange's daily listing bulletin, opt_basic includes delisted
    contracts.  It is used for the 2019 backfill only; normal post-close
    updates continue to query the exchanges' public current-listing feeds.
    """
    exchange = ETFS[symbol]["exchange"]
    frame = tushare_client().opt_basic(exchange=exchange)
    records: list[dict] = []
    for row in frame.to_dict("records"):
        trade_code = str(row.get("symbol", ""))
        if not trade_code.startswith(symbol):
            continue
        try:
            listed = str(row["list_date"])
            expiry = str(row["maturity_date"])
            expiry_day = dt.datetime.strptime(expiry, "%Y%m%d").date()
            if len(listed) != 8 or not (first <= expiry_day <= last):
                continue
            records.append({
                "SECURITY_ID": tushare_option_id(row["ts_code"]), "CONTRACT_ID": trade_code,
                "CALL_OR_PUT": "认购" if row.get("call_put") == "C" else "认沽",
                "EXERCISE_PRICE": row["exercise_price"], "START_DATE": listed, "EXPIRE_DATE": expiry,
            })
        except (KeyError, TypeError, ValueError):
            continue
    return list({row["SECURITY_ID"]: row for row in records}.values())


def store_contracts(symbol: str, items: list[dict]) -> None:
    exchange = ETFS[symbol]["exchange"]
    with connect() as conn:
        for item in items:
            conn.execute(
                """INSERT INTO vix_option_contracts
                (symbol,option_id,exchange,trade_code,option_type,strike,listed_date,expiry_date,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(symbol,option_id) DO UPDATE SET
                trade_code=excluded.trade_code,option_type=excluded.option_type,strike=excluded.strike,
                listed_date=excluded.listed_date,expiry_date=excluded.expiry_date,updated_at=excluded.updated_at""",
                (symbol, item["SECURITY_ID"], exchange, item.get("CONTRACT_ID"), item["CALL_OR_PUT"],
                 float(item["EXERCISE_PRICE"]), item["START_DATE"], item["EXPIRE_DATE"], stamp()),
            )


def option_bars(symbol: str, option_id: str, *, refresh: bool = False) -> list[dict]:
    url = "https://stock.finance.sina.com.cn/futures/api/openapi.php/StockOptionDaylineService.getSymbolInfo?symbol=CON_OP_" + option_id
    cache = CACHE / symbol / "option-bars" / f"{option_id}.json"
    payload = json.loads(request_text(url, cache, headers=SINA_HEADERS, refresh=refresh))
    return (payload.get("result") or {}).get("data") or []


def store_option_bars(symbol: str, contract_ids: list[str], *, refresh: bool = False) -> None:
    """Fetch public option daylines without allowing one delisted contract to halt a backfill."""
    def one(option_id: str) -> tuple[str, list[dict]]:
        return option_id, option_bars(symbol, option_id, refresh=refresh)

    downloaded: list[tuple[str, list[dict]]] = []
    failures = 0
    # Four concurrent requests is fast enough for the initial backfill while
    # remaining below the public endpoint's practical rate limit.
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(one, option_id) for option_id in contract_ids]
        for future in as_completed(futures):
            try:
                downloaded.append(future.result())
            except Exception:
                # Some long-expired contracts are no longer served by Sina.
                # Other strikes/expiries still provide the VIX calculation, so
                # record the omission and continue the resumable batch.
                failures += 1
    # Do not retain a write lock while waiting for remote option-bar requests.
    with connect() as conn:
        for option_id, bars in downloaded:
            for bar in bars:
                close = float(bar.get("c", 0) or 0)
                if close <= 0:
                    continue
                conn.execute(
                    """INSERT INTO vix_option_prices (option_id,trade_date,close,volume,updated_at)
                    VALUES (?,?,?,?,?) ON CONFLICT(option_id,trade_date) DO UPDATE SET
                    close=excluded.close,volume=excluded.volume,updated_at=excluded.updated_at""",
                    (option_id, bar["d"], close, float(bar.get("v", 0) or 0), stamp()),
                )
    if failures:
        print(f"[VIX] {symbol} 有 {failures} 张失效合约未返回日线，已跳过。", flush=True)


def store_tushare_option_daily(first: dt.date, last: dt.date, symbols: tuple[str, ...] | None = None) -> int:
    """Store closing prices one exchange-day at a time from Tushare opt_daily.

    Tushare retains historical rows for expired contracts, unlike the public
    per-contract endpoint.  We keep the exchange master data for strikes,
    call/put and expiries, then join by the numeric option identifier.
    """
    selected_symbols = symbols or tuple(ETFS)
    placeholders = ",".join("?" for _ in selected_symbols)
    with connect() as conn:
        rows = conn.execute(
            f"SELECT option_id,exchange FROM vix_option_contracts WHERE symbol IN ({placeholders})",
            selected_symbols,
        ).fetchall()
    ids_by_exchange: dict[str, set[str]] = {"SSE": set(), "SZSE": set()}
    for row in rows:
        if row["exchange"] in ids_by_exchange:
            ids_by_exchange[row["exchange"]].add(str(row["option_id"]))
    pro = tushare_client()
    stored = 0
    for exchange, valid_ids in ids_by_exchange.items():
        if not valid_ids:
            continue
        for chunk_start, chunk_end in date_chunks(first, last):
            frame = pro.opt_daily(
                exchange=exchange,
                start_date=chunk_start.strftime("%Y%m%d"),
                end_date=chunk_end.strftime("%Y%m%d"),
            )
            payload: list[tuple[str, str, float, float, str, str]] = []
            for row in frame.to_dict("records"):
                option_id = tushare_option_id(row.get("ts_code", ""))
                if option_id not in valid_ids:
                    continue
                try:
                    close = float(row.get("close"))
                    volume = float(row.get("vol") or 0)
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(close) or close <= 0:
                    continue
                day = str(row.get("trade_date", ""))
                if len(day) != 8:
                    continue
                payload.append((option_id, f"{day[:4]}-{day[4:6]}-{day[6:]}", close, volume, "tushare", stamp()))
            if payload:
                with connect() as conn:
                    conn.executemany(
                        """INSERT INTO vix_option_prices (option_id,trade_date,close,volume,source,updated_at)
                        VALUES (?,?,?,?,?,?) ON CONFLICT(option_id,trade_date) DO UPDATE SET
                        close=excluded.close,volume=excluded.volume,source=excluded.source,updated_at=excluded.updated_at""",
                        payload,
                    )
                stored += len(payload)
            print(f"[VIX] Tushare {exchange} {chunk_start} 至 {chunk_end}：写入 {len(payload)} 条。", flush=True)
    return stored


def reset_vix_backfill() -> None:
    """Replace incomplete public-source observations with a clean Tushare rebuild."""
    with connect() as conn:
        conn.execute("DELETE FROM vix_option_prices")
        conn.execute("DELETE FROM etf_vix_daily")
        conn.execute("DELETE FROM etf_pcivd_daily")
        conn.execute("DELETE FROM etf_iv_hv_daily")
        conn.execute("DELETE FROM etf_option_iv_surface")
        conn.execute("DELETE FROM vix_sync_state WHERE key IN ('pcivd_history_source','iv_surface_history_source','iv_hv_history_source')")
        conn.execute(
            """INSERT INTO vix_sync_state (key,value,updated_at) VALUES ('history_status',?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
            ("backfilling", stamp()),
        )


def sync_underlying(symbol: str, *, refresh: bool = False) -> list[str]:
    sina_symbol = ETFS[symbol]["sina"]
    url = ("https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20data="
           f"/CN_MarketDataService.getKLineData?symbol={sina_symbol}&scale=240&ma=no&datalen=1300")
    text = request_text(url, CACHE / symbol / "underlying.json", headers=SINA_HEADERS, refresh=refresh)
    match = re.search(r"var data=\((.*)\);?\s*$", text, flags=re.S)
    if not match:
        raise RuntimeError(f"{symbol} 标的日线返回格式异常")
    rows = json.loads(match.group(1))
    with connect() as conn:
        for row in rows:
            conn.execute(
                """INSERT INTO vix_underlying_history (symbol,trade_date,close,updated_at)
                VALUES (?,?,?,?) ON CONFLICT(symbol,trade_date) DO UPDATE SET close=excluded.close,updated_at=excluded.updated_at""",
                (symbol, row["day"], float(row["close"]), stamp()),
            )
    return [row["day"] for row in rows]


def store_tushare_underlying_history(symbol: str, first: dt.date, last: dt.date) -> int:
    """Backfill ETF closes beyond Sina's rolling daily-bar limit.

    Sina's endpoint only exposes about 1,300 recent sessions, which is not
    enough for a 2019 inception.  Tushare fund_daily supplies the older ETF
    closes used by HV and the audit fields alongside VIX/PCIVD.
    """
    exchange_suffix = "SH" if ETFS[symbol]["exchange"] == "SSE" else "SZ"
    frame = tushare_client().fund_daily(
        ts_code=f"{symbol}.{exchange_suffix}",
        start_date=first.strftime("%Y%m%d"), end_date=last.strftime("%Y%m%d"),
    )
    payload: list[tuple[str, str, float, str]] = []
    for row in frame.to_dict("records"):
        day = str(row.get("trade_date", ""))
        try:
            close = float(row.get("close"))
        except (TypeError, ValueError):
            continue
        if len(day) == 8 and close > 0 and math.isfinite(close):
            payload.append((symbol, f"{day[:4]}-{day[4:6]}-{day[6:]}", close, stamp()))
    if payload:
        with connect() as conn:
            conn.executemany(
                """INSERT INTO vix_underlying_history (symbol,trade_date,close,updated_at)
                VALUES (?,?,?,?) ON CONFLICT(symbol,trade_date) DO UPDATE SET
                close=excluded.close,updated_at=excluded.updated_at""",
                payload,
            )
    print(f"[VIX] Tushare {symbol} ETF 日线：写入 {len(payload)} 条。", flush=True)
    return len(payload)


def load_contracts_and_prices(symbol: str) -> list[dict]:
    with connect() as conn:
        contracts = [dict(row) for row in conn.execute("SELECT * FROM vix_option_contracts WHERE symbol=?", (symbol,))]
        prices = conn.execute(
            """SELECT p.option_id,p.trade_date,p.close FROM vix_option_prices p
            JOIN vix_option_contracts c ON c.option_id=p.option_id AND c.symbol=?""", (symbol,)
        ).fetchall()
    by_id: dict[str, dict[str, float]] = {}
    for row in prices:
        by_id.setdefault(row["option_id"], {})[row["trade_date"]] = float(row["close"])
    for item in contracts:
        item["prices"] = by_id.get(item["option_id"], {})
        item["price_dates"] = sorted(item["prices"])
    return contracts


def term_variance(contracts: list[dict], day: dt.date) -> dict | None:
    expiry = dt.datetime.strptime(contracts[0]["expiry_date"], "%Y%m%d").date()
    maturity = (expiry - day).days / 365
    if maturity <= 0:
        return None
    key = day.isoformat()
    pairs: dict[float, dict[str, float]] = {}
    for item in contracts:
        position = bisect.bisect_right(item["price_dates"], key) - 1
        if position < 0:
            continue
        price = item["prices"][item["price_dates"][position]]
        pairs.setdefault(float(item["strike"]), {})["C" if item["option_type"] == "认购" else "P"] = price
    parity = [(abs(pair["C"] - pair["P"]), strike, pair["C"], pair["P"])
              for strike, pair in pairs.items() if pair.get("C", 0) > 0 and pair.get("P", 0) > 0]
    if len(parity) < 3:
        return None
    _, reference, call, put = min(parity)
    forward = reference + math.exp(RATE * maturity) * (call - put)
    eligible = [strike for strike in pairs if strike <= forward]
    if not eligible:
        return None
    k0 = max(eligible)
    quotes: list[tuple[float, float]] = []
    for side, iterator in (("P", sorted((k for k in pairs if k < k0), reverse=True)), ("C", sorted(k for k in pairs if k > k0))):
        zeros = 0
        for strike in iterator:
            price = pairs[strike].get(side, 0)
            if price <= 0:
                zeros += 1
                if zeros >= 2:
                    break
                continue
            zeros = 0
            quotes.append((strike, price))
    at_k0 = pairs.get(k0, {})
    if at_k0.get("C", 0) > 0 and at_k0.get("P", 0) > 0:
        quotes.append((k0, (at_k0["C"] + at_k0["P"]) / 2))
    elif max(at_k0.get("C", 0), at_k0.get("P", 0)) > 0:
        quotes.append((k0, max(at_k0.get("C", 0), at_k0.get("P", 0))))
    quotes.sort()
    if len(quotes) < 3:
        return None
    total = 0.0
    for index, (strike, price) in enumerate(quotes):
        delta_k = (quotes[1][0] - strike if index == 0 else strike - quotes[index - 1][0] if index == len(quotes) - 1 else (quotes[index + 1][0] - quotes[index - 1][0]) / 2)
        total += delta_k * price / (strike * strike)
    variance = 2 * math.exp(RATE * maturity) * total / maturity - (forward / k0 - 1) ** 2 / maturity
    return None if variance <= 0 else {"expiry": expiry.isoformat(), "days": (expiry - day).days, "maturity": maturity, "variance": variance}


def vix_for_day(contracts: list[dict], day: dt.date) -> tuple[float | None, dict]:
    compact = day.strftime("%Y%m%d")
    groups: dict[str, list[dict]] = {}
    for contract in contracts:
        if contract["listed_date"] <= compact < contract["expiry_date"]:
            groups.setdefault(contract["expiry_date"], []).append(contract)
    terms = [term_variance(items, day) for _, items in sorted(groups.items())]
    terms = [term for term in terms if term]
    target = TARGET_DAYS / 365
    exact = next((item for item in terms if abs(item["maturity"] - target) < 1e-12), None)
    if exact:
        return 100 * math.sqrt(exact["variance"]), {"near_expiry": exact["expiry"], "next_expiry": exact["expiry"], "near_days": exact["days"], "next_days": exact["days"], "near_variance": exact["variance"], "next_variance": exact["variance"], "term_selection": "exact"}
    lower, upper = [item for item in terms if item["maturity"] < target], [item for item in terms if item["maturity"] > target]
    if lower and upper:
        near, next_, mode = lower[-1], upper[0], "interpolated"
    elif len(terms) >= 2:
        near, next_, mode = terms[0], terms[1], "short_extrapolation"
    else:
        return None, {}
    variance = (near["maturity"] * near["variance"] * (next_["maturity"] - target) + next_["maturity"] * next_["variance"] * (target - near["maturity"])) / ((next_["maturity"] - near["maturity"]) * target)
    return 100 * math.sqrt(max(0, variance)), {"near_expiry": near["expiry"], "next_expiry": next_["expiry"], "near_days": near["days"], "next_days": next_["days"], "near_variance": near["variance"], "next_variance": next_["variance"], "term_selection": mode}


def normal_cdf(value: float) -> float:
    return 0.5 * (1 + math.erf(value / math.sqrt(2)))


def black_scholes_price(spot: float, strike: float, maturity: float, volatility: float, option_type: str) -> float:
    if spot <= 0 or strike <= 0 or maturity <= 0 or volatility <= 0:
        return 0.0
    root_t = math.sqrt(maturity)
    d1 = (math.log(spot / strike) + (RATE + 0.5 * volatility * volatility) * maturity) / (volatility * root_t)
    d2 = d1 - volatility * root_t
    discounted_strike = strike * math.exp(-RATE * maturity)
    if option_type == "认购":
        return spot * normal_cdf(d1) - discounted_strike * normal_cdf(d2)
    return discounted_strike * normal_cdf(-d2) - spot * normal_cdf(-d1)


def implied_volatility(price: float, spot: float, strike: float, maturity: float, option_type: str) -> float | None:
    """A dependency-free Black-Scholes inversion; result is decimal volatility."""
    if price <= 0 or spot <= 0 or strike <= 0 or maturity <= 0:
        return None
    discounted_strike = strike * math.exp(-RATE * maturity)
    lower = max(0.0, spot - discounted_strike) if option_type == "认购" else max(0.0, discounted_strike - spot)
    upper = spot if option_type == "认购" else discounted_strike
    if price < lower - 1e-8 or price > upper + 1e-8:
        return None
    low, high = 1e-5, 5.0
    for _ in range(100):
        mid = (low + high) / 2
        if black_scholes_price(spot, strike, maturity, mid, option_type) < price:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def iv_maturity(day: dt.date, expiry: dt.date) -> float:
    """Black-Scholes year fraction for displayed ETF-option IV.

    Display IV adopts the inclusive-calendar-day convention used by Chinese
    brokerage terminals.  Standard VIX deliberately keeps its own exchange
    style term calculation in ``term_variance`` and does not use this helper.
    """
    return ((expiry - day).days + 1) / 365


def pcivd_for_day(contracts: list[dict], day: dt.date, spot: float) -> tuple[float | None, dict]:
    """Put IV minus call IV for the near-month ATM pair; roll at <= 8 days."""
    compact, key = day.strftime("%Y%m%d"), day.isoformat()
    groups: dict[str, list[dict]] = {}
    for contract in contracts:
        if contract["listed_date"] <= compact < contract["expiry_date"]:
            groups.setdefault(contract["expiry_date"], []).append(contract)
    expiries = sorted(groups)
    if not expiries:
        return None, {}
    first_expiry = dt.datetime.strptime(expiries[0], "%Y%m%d").date()
    expiry_index = 1 if (first_expiry - day).days <= PCIVD_ROLL_DAYS else 0
    if len(expiries) <= expiry_index:
        return None, {}
    expiry_text = expiries[expiry_index]
    expiry = dt.datetime.strptime(expiry_text, "%Y%m%d").date()
    days_to_expiry = (expiry - day).days
    maturity = iv_maturity(day, expiry)
    if maturity <= 0:
        return None, {}
    pairs: dict[float, dict[str, tuple[dict, float]]] = {}
    for contract in groups[expiry_text]:
        position = bisect.bisect_right(contract["price_dates"], key) - 1
        if position < 0:
            continue
        price = contract["prices"][contract["price_dates"][position]]
        if price > 0:
            pairs.setdefault(float(contract["strike"]), {})[contract["option_type"]] = (contract, price)
    for strike in sorted(pairs, key=lambda value: abs(value - spot)):
        pair = pairs[strike]
        if "认购" not in pair or "认沽" not in pair:
            continue
        call_contract, call_price = pair["认购"]
        put_contract, put_price = pair["认沽"]
        call_iv = implied_volatility(call_price, spot, strike, maturity, "认购")
        put_iv = implied_volatility(put_price, spot, strike, maturity, "认沽")
        if call_iv is None or put_iv is None:
            continue
        return 100 * (put_iv - call_iv), {
            "put_iv_pct": 100 * put_iv, "call_iv_pct": 100 * call_iv,
            "expiry_date": expiry.isoformat(), "days_to_expiry": days_to_expiry,
            "strike": strike, "put_option_id": put_contract["option_id"],
            "call_option_id": call_contract["option_id"],
        }
    return None, {}


def calculate_and_store(symbol: str, dates: list[str]) -> int:
    contracts = load_contracts_and_prices(symbol)
    if not contracts:
        return 0
    with connect() as conn:
        closes = {row["trade_date"]: float(row["close"]) for row in conn.execute("SELECT trade_date,close FROM vix_underlying_history WHERE symbol=?", (symbol,))}
        count = 0
        for text_date in dates:
            if text_date not in closes:
                continue
            value, detail = vix_for_day(contracts, dt.date.fromisoformat(text_date))
            conn.execute(
                """INSERT INTO etf_vix_daily
                (symbol,trade_date,underlying_close,standard_vix_pct,near_expiry,next_expiry,near_days,next_days,near_variance,next_variance,term_selection,calculated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(symbol,trade_date) DO UPDATE SET
                underlying_close=excluded.underlying_close,standard_vix_pct=excluded.standard_vix_pct,
                near_expiry=excluded.near_expiry,next_expiry=excluded.next_expiry,near_days=excluded.near_days,next_days=excluded.next_days,
                near_variance=excluded.near_variance,next_variance=excluded.next_variance,term_selection=excluded.term_selection,calculated_at=excluded.calculated_at""",
                (symbol, text_date, closes[text_date], value, detail.get("near_expiry"), detail.get("next_expiry"), detail.get("near_days"), detail.get("next_days"), detail.get("near_variance"), detail.get("next_variance"), detail.get("term_selection"), stamp()),
            )
            count += 1
    return count


def calculate_pcivd_and_store(symbol: str, dates: list[str]) -> int:
    contracts = load_contracts_and_prices(symbol)
    if not contracts:
        return 0
    with connect() as conn:
        closes = {row["trade_date"]: float(row["close"]) for row in conn.execute(
            "SELECT trade_date,close FROM vix_underlying_history WHERE symbol=?", (symbol,)
        )}
        count = 0
        for text_date in dates:
            spot = closes.get(text_date)
            if spot is None:
                continue
            value, detail = pcivd_for_day(contracts, dt.date.fromisoformat(text_date), spot)
            conn.execute(
                """INSERT INTO etf_pcivd_daily
                (symbol,trade_date,underlying_close,pcivd_pct,put_iv_pct,call_iv_pct,expiry_date,days_to_expiry,strike,put_option_id,call_option_id,calculated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(symbol,trade_date) DO UPDATE SET
                underlying_close=excluded.underlying_close,pcivd_pct=excluded.pcivd_pct,
                put_iv_pct=excluded.put_iv_pct,call_iv_pct=excluded.call_iv_pct,expiry_date=excluded.expiry_date,
                days_to_expiry=excluded.days_to_expiry,strike=excluded.strike,put_option_id=excluded.put_option_id,
                call_option_id=excluded.call_option_id,calculated_at=excluded.calculated_at""",
                (symbol, text_date, spot, value, detail.get("put_iv_pct"), detail.get("call_iv_pct"),
                 detail.get("expiry_date"), detail.get("days_to_expiry"), detail.get("strike"),
                 detail.get("put_option_id"), detail.get("call_option_id"), stamp()),
            )
            count += 1
    return count


def calculate_iv_hv_and_store(symbol: str, dates: list[str]) -> int:
    """Store standard VIX less 30-trading-day annualised historical volatility.

    HV uses the sample standard deviation of the latest 30 daily log returns
    and a sqrt(252) annualisation.  Each observation is keyed to the same
    trade date as its standard-VIX input, so the two 30-day measures align.
    """
    if not dates:
        return 0
    with connect() as conn:
        closes = [(row["trade_date"], float(row["close"])) for row in conn.execute(
            "SELECT trade_date,close FROM vix_underlying_history WHERE symbol=? ORDER BY trade_date", (symbol,)
        )]
        vix_by_day = {row["trade_date"]: (float(row["underlying_close"]), float(row["standard_vix_pct"]))
                      for row in conn.execute(
            "SELECT trade_date,underlying_close,standard_vix_pct FROM etf_vix_daily "
            "WHERE symbol=? AND standard_vix_pct IS NOT NULL", (symbol,)
        )}
        returns: dict[str, float] = {}
        for index in range(1, len(closes)):
            previous, current = closes[index - 1][1], closes[index][1]
            if previous > 0 and current > 0:
                returns[closes[index][0]] = math.log(current / previous)
        ordered_return_dates = [day for day, _ in closes[1:] if day in returns]
        return_index = {day: index for index, day in enumerate(ordered_return_dates)}
        values = [returns[day] for day in ordered_return_dates]
        count = 0
        for text_date in dates:
            position = return_index.get(text_date)
            vix_input = vix_by_day.get(text_date)
            if position is None or vix_input is None or position + 1 < HV_WINDOW_DAYS:
                continue
            sample = values[position + 1 - HV_WINDOW_DAYS:position + 1]
            mean = sum(sample) / HV_WINDOW_DAYS
            variance = sum((value - mean) ** 2 for value in sample) / (HV_WINDOW_DAYS - 1)
            hv_pct = 100 * math.sqrt(variance) * math.sqrt(252)
            spot, vix_pct = vix_input
            conn.execute(
                """INSERT INTO etf_iv_hv_daily
                (symbol,trade_date,underlying_close,standard_vix_pct,historical_vol_pct,iv_hv_pct,calculated_at)
                VALUES (?,?,?,?,?,?,?) ON CONFLICT(symbol,trade_date) DO UPDATE SET
                underlying_close=excluded.underlying_close,standard_vix_pct=excluded.standard_vix_pct,
                historical_vol_pct=excluded.historical_vol_pct,iv_hv_pct=excluded.iv_hv_pct,
                calculated_at=excluded.calculated_at""",
                (symbol, text_date, spot, vix_pct, hv_pct, vix_pct - hv_pct, stamp()),
            )
            count += 1
    return count


def calculate_iv_surface_and_store(symbol: str, dates: list[str]) -> int:
    """Persist every valid option IV needed by the recent Skew/Cone charts.

    The price selection mirrors VIX/PCIVD: for a given trading date, use that
    contract's latest available closing quote at or before the date.  IV is
    then obtained by numerically inverting the same Black-Scholes model used
    for PCIVD, with the dashboard's 1.5% risk-free-rate assumption.
    """
    contracts = load_contracts_and_prices(symbol)
    if not contracts:
        return 0
    with connect() as conn:
        closes = {row["trade_date"]: float(row["close"]) for row in conn.execute(
            "SELECT trade_date,close FROM vix_underlying_history WHERE symbol=?", (symbol,)
        )}
        stored = 0
        for text_date in dates:
            spot = closes.get(text_date)
            if spot is None:
                continue
            day = dt.date.fromisoformat(text_date)
            compact = day.strftime("%Y%m%d")
            conn.execute("DELETE FROM etf_option_iv_surface WHERE symbol=? AND trade_date=?", (symbol, text_date))
            payload: list[tuple] = []
            for contract in contracts:
                if not (contract["listed_date"] <= compact < contract["expiry_date"]):
                    continue
                expiry = dt.datetime.strptime(contract["expiry_date"], "%Y%m%d").date()
                days_to_expiry = (expiry - day).days
                if days_to_expiry <= 0:
                    continue
                position = bisect.bisect_right(contract["price_dates"], text_date) - 1
                if position < 0:
                    continue
                close = float(contract["prices"][contract["price_dates"][position]])
                iv = implied_volatility(close, spot, float(contract["strike"]), iv_maturity(day, expiry), contract["option_type"])
                if iv is None or not math.isfinite(iv):
                    continue
                payload.append((
                    symbol, text_date, spot, expiry.isoformat(), days_to_expiry,
                    float(contract["strike"]), contract["option_type"], contract["option_id"],
                    close, 100 * iv, stamp(),
                ))
            if payload:
                conn.executemany(
                    """INSERT INTO etf_option_iv_surface
                    (symbol,trade_date,underlying_close,expiry_date,days_to_expiry,strike,option_type,option_id,close,iv_pct,calculated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(symbol,trade_date,option_id) DO UPDATE SET
                    underlying_close=excluded.underlying_close,expiry_date=excluded.expiry_date,
                    days_to_expiry=excluded.days_to_expiry,strike=excluded.strike,option_type=excluded.option_type,
                    close=excluded.close,iv_pct=excluded.iv_pct,calculated_at=excluded.calculated_at""",
                    payload,
                )
                stored += len(payload)
    return stored


def backfill_pcivd_history(history_start: dt.date) -> None:
    """PCIVD uses the already persisted Tushare option closes; no extra API pull."""
    with connect() as conn:
        state = conn.execute("SELECT value FROM vix_sync_state WHERE key='pcivd_history_source'").fetchone()
    if state and state["value"] == PCIVD_HISTORY_VERSION:
        return
    with connect() as conn:
        conn.execute("DELETE FROM etf_pcivd_daily")
    for symbol in ETFS:
        with connect() as conn:
            dates = [row["trade_date"] for row in conn.execute(
                "SELECT trade_date FROM vix_underlying_history WHERE symbol=? AND trade_date>=? ORDER BY trade_date",
                (symbol, history_start.isoformat()),
            )]
        computed = calculate_pcivd_and_store(symbol, dates)
        print(f"[PCIVD] {symbol} 2019 至今历史完成：计算 {computed} 日。", flush=True)
    with connect() as conn:
        conn.execute(
            """INSERT INTO vix_sync_state (key,value,updated_at) VALUES ('pcivd_history_source',?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
            (PCIVD_HISTORY_VERSION, stamp()),
        )


def backfill_iv_hv_history(history_start: dt.date) -> None:
    """Build the three-year IV-HV spread from persisted VIX and ETF closes."""
    with connect() as conn:
        state = conn.execute("SELECT value FROM vix_sync_state WHERE key='iv_hv_history_source'").fetchone()
    if state and state["value"] == IV_HV_HISTORY_VERSION:
        return
    with connect() as conn:
        conn.execute("DELETE FROM etf_iv_hv_daily")
    for symbol in ETFS:
        with connect() as conn:
            dates = [row["trade_date"] for row in conn.execute(
                "SELECT trade_date FROM etf_vix_daily WHERE symbol=? AND trade_date>=? AND standard_vix_pct IS NOT NULL ORDER BY trade_date",
                (symbol, history_start.isoformat()),
            )]
        computed = calculate_iv_hv_and_store(symbol, dates)
        print(f"[IV-HV] {symbol} 2019 至今历史完成：计算 {computed} 日。", flush=True)
    with connect() as conn:
        conn.execute(
            """INSERT INTO vix_sync_state (key,value,updated_at) VALUES ('iv_hv_history_source',?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
            (IV_HV_HISTORY_VERSION, stamp()),
        )


def backfill_iv_surface_history(today: dt.date) -> None:
    """Build the recent month once from persisted closes; no new source pull."""
    with connect() as conn:
        state = conn.execute("SELECT value FROM vix_sync_state WHERE key='iv_surface_history_source'").fetchone()
    if state and state["value"] == IV_SURFACE_HISTORY_VERSION:
        return
    cutoff = (today - dt.timedelta(days=IV_SURFACE_DAYS)).isoformat()
    with connect() as conn:
        conn.execute("DELETE FROM etf_option_iv_surface WHERE trade_date>=?", (cutoff,))
    for symbol in ETFS:
        with connect() as conn:
            dates = [row["trade_date"] for row in conn.execute(
                "SELECT trade_date FROM vix_underlying_history WHERE symbol=? AND trade_date>=? ORDER BY trade_date",
                (symbol, cutoff),
            )]
        stored = calculate_iv_surface_and_store(symbol, dates)
        print(f"[IV Surface] {symbol} 最近一个月完成：写入 {stored} 条。", flush=True)
    with connect() as conn:
        conn.execute(
            """INSERT INTO vix_sync_state (key,value,updated_at) VALUES ('iv_surface_history_source',?,?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
            (IV_SURFACE_HISTORY_VERSION, stamp()),
        )


def discover_and_store(symbol: str, first: dt.date, last: dt.date) -> list[dict]:
    raw = discover_sse_contracts(symbol, first, last) if ETFS[symbol]["exchange"] == "SSE" else discover_szse_contracts(symbol, first, last)
    start, end = (HISTORY_START_DATE - dt.timedelta(days=CONTRACT_LOOKBACK_DAYS)), dt.date.today() + dt.timedelta(days=FUTURE_CONTRACT_DAYS)
    valid = [row for row in raw if start <= dt.datetime.strptime(row["EXPIRE_DATE"], "%Y%m%d").date() <= end]
    store_contracts(symbol, valid)
    return valid


def sync_history() -> None:
    """One-time 2019-to-present rebuild using Tushare history for expired options."""
    if not LOCK.acquire(blocking=False):
        return
    try:
        init_db(); today = dt.date.today(); history_start = HISTORY_START_DATE
        with connect() as conn:
            source_version = conn.execute("SELECT value FROM vix_sync_state WHERE key='vix_history_source'").fetchone()
        if source_version and source_version["value"] == TUSHARE_HISTORY_VERSION:
            backfill_pcivd_history(history_start)
            backfill_iv_hv_history(history_start)
            backfill_iv_surface_history(today)
            return
        reset_vix_backfill()
        for symbol in ETFS:
            contracts = discover_tushare_contracts(
                symbol, history_start - dt.timedelta(days=CONTRACT_LOOKBACK_DAYS),
                today + dt.timedelta(days=FUTURE_CONTRACT_DAYS),
            )
            store_contracts(symbol, contracts)
            print(f"[VIX] {symbol} 恢复合约 {len(contracts)} 张。", flush=True)
            sync_underlying(symbol)
            store_tushare_underlying_history(symbol, history_start, today)
        rows_written = store_tushare_option_daily(history_start, today)
        for symbol in ETFS:
            with connect() as conn:
                dates = [row["trade_date"] for row in conn.execute(
                    "SELECT trade_date FROM vix_underlying_history WHERE symbol=? AND trade_date>=? ORDER BY trade_date",
                    (symbol, history_start.isoformat()),
                )]
            computed = calculate_and_store(symbol, dates)
            print(f"[VIX] {symbol} 2019 至今历史完成：计算 {computed} 日。", flush=True)
        with connect() as conn:
            conn.execute("INSERT INTO vix_sync_state (key,value,updated_at) VALUES ('history_status',?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at", ("completed", stamp()))
            conn.execute("INSERT INTO vix_sync_state (key,value,updated_at) VALUES ('vix_history_source',?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at", (TUSHARE_HISTORY_VERSION, stamp()))
        print(f"[VIX] Tushare 2019 至今历史回补完成：共写入 {rows_written} 条期权日线。", flush=True)
        backfill_pcivd_history(history_start)
        backfill_iv_hv_history(history_start)
        backfill_iv_surface_history(today)
    except Exception as exc:
        print(f"[VIX] 历史回补失败：{exc}", flush=True)
    finally:
        LOCK.release()


def sync_latest(symbols: tuple[str, ...] | None = None) -> bool:
    """Refresh the latest day after close; retry every 30 minutes until complete."""
    if not LOCK.acquire(blocking=False):
        # A historical backfill is currently writing.  Keep the request pending
        # so the next half-hour/manual cycle will perform an actual refresh.
        return False
    try:
        init_db(); today = dt.date.today()
        for symbol in (symbols or tuple(ETFS)):
            # Current listings can change through volatility-driven new strikes.
            discover_and_store(symbol, today - dt.timedelta(days=CONTRACT_LOOKBACK_DAYS), today)
            dates = sync_underlying(symbol, refresh=True)
            latest_dates = [dt.date.fromisoformat(value) for value in dates[-8:]]
            if latest_dates:
                store_tushare_option_daily(min(latest_dates), max(latest_dates), (symbol,))
            calculate_and_store(symbol, dates[-8:])
            calculate_pcivd_and_store(symbol, dates[-8:])
            calculate_iv_hv_and_store(symbol, dates[-8:])
            recent_cutoff = (today - dt.timedelta(days=IV_SURFACE_DAYS)).isoformat()
            recent_dates = [value for value in dates if value >= recent_cutoff]
            calculate_iv_surface_and_store(symbol, recent_dates)
        print("[VIX] 最新交易日已刷新。", flush=True)
        return True
    except Exception as exc:
        print(f"[VIX] 最新数据刷新失败，将在下一轮重试：{exc}", flush=True)
        return False
    finally:
        LOCK.release()


def sync_manual_requests() -> None:
    with connect() as conn:
        pending = conn.execute("SELECT id FROM vix_refresh_requests WHERE status='pending' ORDER BY id LIMIT 1").fetchone()
    if not pending:
        return
    if sync_latest():
        with connect() as conn:
            conn.execute("UPDATE vix_refresh_requests SET status='completed',completed_at=? WHERE id=?", (stamp(), pending["id"]))


def start_scheduler() -> None:
    init_db()
    scheduler = BlockingScheduler(timezone="Asia/Shanghai", job_defaults={"coalesce": True, "misfire_grace_time": 86400})
    options = {"coalesce": True, "misfire_grace_time": 86400, "max_instances": 1}
    # 历史回补为独立工作；不阻塞现有行情、涨跌停采集器。
    scheduler.add_job(sync_history, "date", run_date=dt.datetime.now(), id="vix_history", **options)
    scheduler.add_job(sync_latest, "cron", day_of_week="mon-fri", hour="15", minute="30", id="vix_post_close_first", **options)
    scheduler.add_job(sync_latest, "cron", day_of_week="mon-fri", hour="16-23", minute="0,30", id="vix_post_close_retry", **options)
    scheduler.add_job(sync_manual_requests, "interval", minutes=1, id="vix_manual_refresh", **options)
    scheduler.start()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--history", action="store_true")
    parser.add_argument("--latest", action="store_true")
    parser.add_argument("--symbol", choices=tuple(ETFS))
    args = parser.parse_args()
    sync_history() if args.history else sync_latest((args.symbol,) if args.symbol else None) if args.latest else start_scheduler()
