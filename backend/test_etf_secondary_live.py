import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database
import etf_secondary_live as live


class LiveSecondaryTests(unittest.TestCase):
    def test_parses_same_day_shanghai_and_shenzhen_in_yi(self):
        for code, market in (("510300.SH", 1), ("159919.SZ", 0)):
            payload = {"data": {"code": code[:6], "market": market,
                                "klines": ["2026-11-19,124356640,-29939919,-94416600,-81052384,205409024"]}}
            row = live.parse_live_payload(payload, code, dt.date(2026, 11, 19))
            self.assertEqual(row["trade_date"], "2026-11-19")
            self.assertAlmostEqual(row["main"], 1.2435664)
            self.assertAlmostEqual(row["large"] + row["super_large"], row["main"])

    def test_rejects_stale_wrong_code_and_invalid_numbers(self):
        base = {"data": {"code": "159919", "market": 0,
                         "klines": ["2026-11-18,0,0,0,0,0"]}}
        with self.assertRaises(ValueError):
            live.parse_live_payload(base, "159919.SZ", dt.date(2026, 11, 19))
        base["data"]["klines"] = ["2026-11-19,0,0,0,0,0"]
        with self.assertRaises(ValueError):
            live.parse_live_payload(base, "510300.SH", dt.date(2026, 11, 19))
        base["data"]["klines"] = ["2026-11-19,abc,0,0,0,0"]
        with self.assertRaises(ValueError):
            live.parse_live_payload(base, "159919.SZ", dt.date(2026, 11, 19))

    def test_partial_coverage_never_becomes_complete_total(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(database, "DB_PATH", Path(directory) / "test.db"), \
             patch.object(live, "FUNDS", (("A", "甲", "沪深300"), ("B", "乙", "沪深300"))), \
             patch.object(live.dt, "datetime") as mock_datetime:
            mock_datetime.now.return_value.date.return_value.isoformat.return_value = "2026-11-19"
            live.init_live_db()
            with database.connect() as conn:
                conn.execute("INSERT INTO broad_etf_secondary_live VALUES (?,?,?,?,?,?,?,?)",
                             ("A", "2026-11-19", 1.2, -0.2, -1.0, 0.5, 0.7, "2026-11-19T15:01:00+08:00"))
            result = live.live_snapshot()
            self.assertEqual(result["summary"]["covered"], 1)
            self.assertIsNone(result["summary"]["main"])
            self.assertEqual(result["summary"]["available_main"], 1.2)
            self.assertIsNone(result["groups"][0]["main"])
            self.assertIsNone(result["funds"][1]["main"])


if __name__ == "__main__":
    unittest.main()
