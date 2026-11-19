import datetime as dt
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import database
import etf_secondary as secondary


class SecondaryTests(unittest.TestCase):
    def test_real_source_field_order_and_units(self):
        payload = [{"opendate": "2026-11-18", "trade": "4.0580", "changeratio": "0.00395844", "netamount": "-399682947.3450", "r0_net": "-483523057.9240", "r0_ratio": "-0.06329504"}]
        row = secondary.parse_payload(payload, "510300.SH", dt.date(2026, 11, 19))["2026-11-18"]
        self.assertAlmostEqual(row[0], -4.83523057924)
        self.assertAlmostEqual(row[1], -6.329504)
        self.assertAlmostEqual(row[2], 4.058)
        self.assertAlmostEqual(row[3], 0.395844)
        self.assertAlmostEqual(row[4], -3.99682947345)

    def test_invalid_data_not_zero(self):
        for payload in ({"__ERROR": 1}, [], [{"opendate": "2026-11-19", "r0_net": "-", "netamount": "0"}], [{"opendate": "2026-11-20", "r0_net": "0", "netamount": "0"}]):
            with self.assertRaises(ValueError):
                secondary.parse_payload(payload, "510300.SH", dt.date(2026, 11, 19))
        payload = [{"opendate": "2026-11-19", "r0_net": "0", "netamount": "0"}]
        self.assertEqual(secondary.parse_payload(payload, "510300.SH", dt.date(2026, 11, 19))["2026-11-19"][0], 0)

    def test_missing_day_coverage_windows_and_filter(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(database, "DB_PATH", Path(directory) / "test.db"), patch.object(secondary, "FUNDS", (("A", "A", "甲"), ("B", "B", "乙"))):
            secondary.init_secondary_db()
            self.assertIsNone(secondary.snapshot()["date"])
            dates = [(dt.date(2026, 1, 1) + dt.timedelta(days=i)).isoformat() for i in range(21)]
            with database.connect() as conn:
                conn.executemany("INSERT INTO broad_etf_calendar VALUES (?)", [(d,) for d in dates])
                for code in ["A", "B"]:
                    conn.executemany("INSERT INTO broad_etf_secondary_daily VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", [(code, d, 3, None, None, None, None, 10, 4, 1, d, 5) for d in dates])
            result = secondary.snapshot()
            self.assertEqual(result["summary"]["flow_20"], 120)
            self.assertEqual(result["daily"][-1]["net_amount"], 10)
            self.assertEqual(secondary.snapshot("甲")["summary"]["flow_5"], 15)
            with database.connect() as conn:
                conn.execute("DELETE FROM broad_etf_secondary_daily WHERE trade_date=? AND ts_code='A'", (dates[-1],))
                conn.execute("DELETE FROM broad_etf_secondary_daily WHERE trade_date=?", (dates[-3],))
            result = secondary.snapshot()
            self.assertIsNone(result["summary"]["main"])
            self.assertIsNone(result["summary"]["flow_5"])
            self.assertEqual(result["summary"]["covered"], 1)
            self.assertEqual(result["daily"][-1]["available_main"], 3)
            self.assertEqual(result["daily"][-3]["covered"], 0)
            self.assertEqual(result["funds"][0]["data_date"], dates[-2])
            self.assertEqual(result["complete_date"], dates[-2])
            self.assertTrue(secondary.snapshot("甲")["stale"])
            with database.connect() as conn:
                conn.execute("DELETE FROM broad_etf_secondary_daily WHERE trade_date=?", (dates[-1],))
            self.assertTrue(secondary.snapshot()["stale"])


if __name__ == "__main__":
    unittest.main()
