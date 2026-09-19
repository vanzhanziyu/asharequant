"""SQLite schema and connection helpers shared by collectors and API."""
from __future__ import annotations

import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "market_data.db"


def connect() -> sqlite3.Connection:
    # Collectors run independently.  WAL and a sensible wait window avoid a
    # short source refresh colliding with an API read or another collector.
    connection = sqlite3.connect(DB_PATH, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=30000")
    connection.execute("PRAGMA journal_mode=WAL")
    return connection


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS market_turnover_history (
                trade_date TEXT PRIMARY KEY,
                total_amount REAL, sh_amount REAL, sz_amount REAL,
                cyb_amount REAL, kc50_amount REAL, hl_amount REAL,
                wind_micro_amount REAL, updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS index_kline_history (
                index_code TEXT NOT NULL, trade_date TEXT NOT NULL,
                open REAL, high REAL, low REAL, close REAL, volume REAL, amount REAL,
                PRIMARY KEY (index_code, trade_date)
            );
            CREATE TABLE IF NOT EXISTS wind_kline_history (
                trade_date TEXT PRIMARY KEY,
                open REAL, high REAL, low REAL, close REAL, volume REAL, amount_yi REAL
            );
            CREATE TABLE IF NOT EXISTS limit_stocks (
                trade_date TEXT NOT NULL, stock_code TEXT NOT NULL, stock_name TEXT,
                last_price REAL, change_pct REAL, limit_type TEXT NOT NULL, status INTEGER,
                industry TEXT, first_limit_time TEXT, limit_num INTEGER, updated_at TEXT,
                PRIMARY KEY (trade_date, stock_code, limit_type)
            );
            CREATE TABLE IF NOT EXISTS margin_history (
                trade_date TEXT PRIMARY KEY, rzrqye REAL, updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS us_treasury_history (
                trade_date TEXT PRIMARY KEY,
                m3 REAL, y2 REAL, y5 REAL, y10 REAL, y30 REAL, updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS macro_price_history (
                symbol TEXT NOT NULL, trade_date TEXT NOT NULL,
                open REAL, high REAL, low REAL, close REAL, source TEXT, updated_at TEXT,
                PRIMARY KEY (symbol, trade_date)
            );
            CREATE TABLE IF NOT EXISTS vix_underlying_history (
                symbol TEXT NOT NULL, trade_date TEXT NOT NULL, close REAL NOT NULL,
                updated_at TEXT NOT NULL, PRIMARY KEY (symbol, trade_date)
            );
            CREATE TABLE IF NOT EXISTS vix_option_contracts (
                symbol TEXT NOT NULL, option_id TEXT NOT NULL, exchange TEXT NOT NULL,
                trade_code TEXT, option_type TEXT NOT NULL, strike REAL NOT NULL,
                listed_date TEXT NOT NULL, expiry_date TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY (symbol, option_id)
            );
            CREATE TABLE IF NOT EXISTS vix_option_prices (
                option_id TEXT NOT NULL, trade_date TEXT NOT NULL, close REAL NOT NULL,
                volume REAL, source TEXT NOT NULL DEFAULT 'sina', updated_at TEXT NOT NULL,
                PRIMARY KEY (option_id, trade_date)
            );
            CREATE TABLE IF NOT EXISTS etf_vix_daily (
                symbol TEXT NOT NULL, trade_date TEXT NOT NULL, underlying_close REAL,
                standard_vix_pct REAL, near_expiry TEXT, next_expiry TEXT,
                near_days INTEGER, next_days INTEGER, near_variance REAL, next_variance REAL,
                term_selection TEXT, calculated_at TEXT NOT NULL,
                PRIMARY KEY (symbol, trade_date)
            );
            CREATE TABLE IF NOT EXISTS etf_pcivd_daily (
                symbol TEXT NOT NULL, trade_date TEXT NOT NULL, underlying_close REAL,
                pcivd_pct REAL, put_iv_pct REAL, call_iv_pct REAL,
                expiry_date TEXT, days_to_expiry INTEGER, strike REAL,
                put_option_id TEXT, call_option_id TEXT, calculated_at TEXT NOT NULL,
                PRIMARY KEY (symbol, trade_date)
            );
            CREATE TABLE IF NOT EXISTS etf_iv_hv_daily (
                symbol TEXT NOT NULL, trade_date TEXT NOT NULL,
                underlying_close REAL NOT NULL, standard_vix_pct REAL NOT NULL,
                historical_vol_pct REAL NOT NULL, iv_hv_pct REAL NOT NULL,
                calculated_at TEXT NOT NULL,
                PRIMARY KEY (symbol, trade_date)
            );
            -- Recent option IV observations used by the Skew / Cone panels.
            -- This is deliberately an audit table rather than a browser-time
            -- calculation: every chart point can be traced back to one close.
            CREATE TABLE IF NOT EXISTS etf_option_iv_surface (
                symbol TEXT NOT NULL, trade_date TEXT NOT NULL,
                underlying_close REAL NOT NULL, expiry_date TEXT NOT NULL,
                days_to_expiry INTEGER NOT NULL, strike REAL NOT NULL,
                option_type TEXT NOT NULL, option_id TEXT NOT NULL,
                close REAL NOT NULL, iv_pct REAL NOT NULL,
                calculated_at TEXT NOT NULL,
                PRIMARY KEY (symbol, trade_date, option_id)
            );
            CREATE TABLE IF NOT EXISTS vix_sync_state (
                key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS vix_refresh_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT, requested_at TEXT NOT NULL,
                completed_at TEXT, status TEXT NOT NULL DEFAULT 'pending'
            );
            -- Reusable A-share inputs for factor research.  Raw observations
            -- live separately from factor outputs so new factors can reuse the
            -- same stock universe, prices, corporate actions and accounts.
            CREATE TABLE IF NOT EXISTS factor_stock_basic (
                ts_code TEXT PRIMARY KEY, stock_code TEXT NOT NULL, stock_name TEXT NOT NULL,
                industry TEXT, list_date TEXT, is_st INTEGER NOT NULL DEFAULT 0,
                dividend_synced_at TEXT, financial_synced_at TEXT, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS factor_trade_calendar (
                trade_date TEXT PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS factor_stock_daily (
                ts_code TEXT NOT NULL, trade_date TEXT NOT NULL,
                close REAL NOT NULL, pct_chg REAL, total_mv REAL, adj_factor REAL,
                source TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY (ts_code, trade_date)
            );
            CREATE TABLE IF NOT EXISTS factor_dividend (
                ts_code TEXT NOT NULL, ann_date TEXT, record_date TEXT NOT NULL,
                ex_date TEXT, cash_div_tax REAL NOT NULL,
                source TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY (ts_code, record_date, ann_date)
            );
            CREATE TABLE IF NOT EXISTS factor_financial (
                ts_code TEXT NOT NULL, ann_date TEXT NOT NULL, end_date TEXT NOT NULL,
                ebitda REAL NOT NULL, source TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY (ts_code, ann_date, end_date)
            );
            -- Latest full-universe rank for each factor and each rebalance date.
            CREATE TABLE IF NOT EXISTS factor_universe_snapshot (
                factor_name TEXT NOT NULL, signal_date TEXT NOT NULL, ts_code TEXT NOT NULL,
                factor_value REAL NOT NULL, factor_rank INTEGER NOT NULL, universe_count INTEGER NOT NULL,
                close REAL NOT NULL, pct_chg REAL, total_mv REAL, industry TEXT,
                calculated_at TEXT NOT NULL,
                PRIMARY KEY (factor_name, signal_date, ts_code)
            );
            -- Historical top-100 factor membership used to audit the return
            -- series; only holdings are persisted, not every daily universe.
            CREATE TABLE IF NOT EXISTS factor_portfolio_members (
                factor_name TEXT NOT NULL, signal_date TEXT NOT NULL, ts_code TEXT NOT NULL,
                factor_value REAL NOT NULL, factor_rank INTEGER NOT NULL,
                PRIMARY KEY (factor_name, signal_date, ts_code)
            );
            CREATE TABLE IF NOT EXISTS factor_portfolio_daily (
                factor_name TEXT NOT NULL, trade_date TEXT NOT NULL, signal_date TEXT NOT NULL,
                holding_count INTEGER NOT NULL, daily_return_pct REAL NOT NULL, nav REAL NOT NULL,
                calculated_at TEXT NOT NULL,
                PRIMARY KEY (factor_name, trade_date)
            );
            CREATE TABLE IF NOT EXISTS factor_sync_state (
                key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_etf_vix_symbol_date
                ON etf_vix_daily (symbol, trade_date DESC);
            CREATE INDEX IF NOT EXISTS idx_etf_pcivd_symbol_date
                ON etf_pcivd_daily (symbol, trade_date DESC);
            CREATE INDEX IF NOT EXISTS idx_etf_iv_hv_symbol_date
                ON etf_iv_hv_daily (symbol, trade_date DESC);
            CREATE INDEX IF NOT EXISTS idx_etf_option_iv_surface_symbol_date
                ON etf_option_iv_surface (symbol, trade_date DESC);
            CREATE INDEX IF NOT EXISTS idx_vix_option_contracts_symbol_dates
                ON vix_option_contracts (symbol, listed_date, expiry_date);
            CREATE INDEX IF NOT EXISTS idx_vix_option_prices_option_date
                ON vix_option_prices (option_id, trade_date DESC);
            CREATE INDEX IF NOT EXISTS idx_factor_stock_daily_date
                ON factor_stock_daily (trade_date DESC, ts_code);
            CREATE INDEX IF NOT EXISTS idx_factor_dividend_code_date
                ON factor_dividend (ts_code, record_date DESC);
            CREATE INDEX IF NOT EXISTS idx_factor_financial_code_date
                ON factor_financial (ts_code, ann_date DESC);
            CREATE INDEX IF NOT EXISTS idx_factor_snapshot_lookup
                ON factor_universe_snapshot (factor_name, signal_date DESC, factor_rank);
            CREATE INDEX IF NOT EXISTS idx_factor_portfolio_history
                ON factor_portfolio_daily (factor_name, trade_date DESC);
            CREATE INDEX IF NOT EXISTS idx_limit_stocks_type_date
                ON limit_stocks (limit_type, trade_date DESC);
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(macro_price_history)")}
        if "source" not in columns:
            conn.execute("ALTER TABLE macro_price_history ADD COLUMN source TEXT")
        option_price_columns = {row[1] for row in conn.execute("PRAGMA table_info(vix_option_prices)")}
        if "source" not in option_price_columns:
            conn.execute("ALTER TABLE vix_option_prices ADD COLUMN source TEXT NOT NULL DEFAULT 'sina'")
        conn.execute("PRAGMA optimize")
