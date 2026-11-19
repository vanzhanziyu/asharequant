"""Checks for unit conversion, coverage, and no gap-bridging in ETF estimates."""
import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database
import etf_flow


class FlowTests(unittest.TestCase):
    def test_units_and_sign(self):
        self.assertEqual(etf_flow.estimate_flow({"shares": 10000}, {"shares": 15000, "nav": 4})[0], 2)
        self.assertEqual(etf_flow.estimate_flow({"shares": 15000}, {"shares": 10000, "nav": 4})[0], -2)

    def test_missing_and_split_are_not_flows(self):
        self.assertIsNone(etf_flow.estimate_flow({"shares": 10}, {"shares": 20})[0])
        self.assertIsNone(etf_flow.estimate_flow({"shares": 10, "nav": 4}, {"shares": 20, "nav": 2})[0])
        self.assertEqual(etf_flow.estimate_flow({"shares": 10}, {"shares": 10, "nav": 2})[0], 0)

    def test_coverage_calendar_gap_and_rolling_window(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(etf_flow, "FUNDS", (("A", "A", "测试"), ("B", "B", "测试"))), patch.object(database, "DB_PATH", Path(directory) / "test.db"):
            etf_flow.init_flow_db()
            dates = [(dt.date(2026, 10, 1) + dt.timedelta(days=i)).isoformat() for i in range(23)]
            with database.connect() as conn:
                conn.executemany("INSERT INTO broad_etf_calendar VALUES (?)", [(d,) for d in dates])
                for code in ("A", "B"):
                    conn.executemany("INSERT INTO broad_etf_daily VALUES (?,?,?,?,?,?,?)", [(code, d, 10000 + i * 10000, 1, 1, 1, d) for i, d in enumerate(dates)])
            result = etf_flow.snapshot()
            self.assertEqual(result["summary"]["flow_20"], 40)
            self.assertEqual(result["summary"]["net_flow"], 2)
            with database.connect() as conn:
                conn.execute("DELETE FROM broad_etf_daily WHERE ts_code='A' AND trade_date=?", (dates[-2],))
            result = etf_flow.snapshot()
            self.assertIsNone(result["summary"]["net_flow"])
            self.assertIsNone(result["summary"]["flow_5"])
            self.assertEqual(result["summary"]["covered"], 1)
            self.assertEqual(result["daily"][-1]["available_flow"], 1)
            self.assertIsNone(result["daily"][-1]["cumulative"])


if __name__ == "__main__":
    unittest.main()
