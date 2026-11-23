import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import database
import etf_secondary_live as live


class LiveSecondaryTests(unittest.TestCase):
    @staticmethod
    def batch_payload(codes, day, when=None):
        stamp = int((when or dt.datetime.combine(day, dt.time(10, 30), live.SHANGHAI)).timestamp())
        return {"data": {"diff": [
            {"f12": code[:6], "f13": 1 if code.endswith(".SH") else 0,
             "f62": 300000000, "f66": 100000000, "f72": 200000000,
             "f78": -100000000, "f84": -200000000, "f124": stamp}
            for code in codes]}}

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

    def test_batch_parses_same_day_and_rejects_bad_quotes(self):
        today = dt.date(2026, 11, 23)
        payload = self.batch_payload(("510300.SH", "159919.SZ"), today)
        for code in ("510300.SH", "159919.SZ"):
            row = live.parse_batch_payload(payload, code, today)
            self.assertEqual(row["trade_date"], today.isoformat())
            self.assertEqual(row["main"], 3.0)
            self.assertEqual(row["large"] + row["super_large"], row["main"])
        with self.assertRaises(ValueError):
            live.parse_batch_payload(payload, "510300.SH", today + dt.timedelta(days=1))
        with self.assertRaises(ValueError):
            live.parse_batch_payload(payload, "510300.SH", today,
                                     dt.datetime.combine(today, dt.time(11), live.SHANGHAI))
        payload["data"]["diff"][0]["f72"] = 1
        with self.assertRaises(ValueError):
            live.parse_batch_payload(payload, "510300.SH", today)
        payload["data"]["diff"][0]["f72"] = 200000000
        payload["data"]["diff"][0]["f13"] = 0
        with self.assertRaises(ValueError):
            live.parse_batch_payload(payload, "510300.SH", today)

    def test_batch_sync_uses_one_request_for_all_funds(self):
        today = dt.datetime.now(live.SHANGHAI).date()
        codes = ("510300.SH", "159919.SZ")
        response = MagicMock()
        response.json.return_value = self.batch_payload(codes, today, dt.datetime.now(live.SHANGHAI))
        session = MagicMock()
        session.__enter__.return_value = session
        session.get.return_value = response
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(database, "DB_PATH", Path(directory) / "test.db"), \
             patch.object(live, "FUNDS", tuple((code, code, "沪深300") for code in codes)), \
             patch.dict(sys.modules, {"requests": SimpleNamespace(Session=lambda: session)}):
            live.sync_live()
            self.assertEqual(session.get.call_count, 1)
            result = live.live_snapshot()
            self.assertEqual(result["summary"]["covered"], 2)
            self.assertEqual(result["summary"]["main"], 6.0)
            self.assertIsNone(result["funds"][0]["change_5m"])
            self.assertEqual(len(live.live_history(codes[0])["points"]), 1)
            self.assertEqual(result["errors"], [])

    def test_successive_collections_keep_history_and_show_change(self):
        code = "510300.SH"
        today = dt.date(2026, 11, 23)
        first = dt.datetime.combine(today, dt.time(10, 30), live.SHANGHAI)

        class FrozenDateTime(dt.datetime):
            current = first

            @classmethod
            def now(cls, tz=None):
                return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)

        first_response = MagicMock()
        first_response.json.return_value = self.batch_payload((code,), today, first)
        second_response = MagicMock()
        second_response.json.return_value = self.batch_payload((code,), today, first + dt.timedelta(minutes=5))
        second_response.json.return_value["data"]["diff"][0].update(
            {"f62": 500000000, "f72": 400000000})
        session = MagicMock()
        session.__enter__.return_value = session
        session.get.side_effect = [first_response, second_response]
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(database, "DB_PATH", Path(directory) / "test.db"), \
             patch.object(live, "FUNDS", ((code, "沪深300ETF", "沪深300"),)), \
             patch.object(live.dt, "datetime", FrozenDateTime), \
             patch.dict(sys.modules, {"requests": SimpleNamespace(Session=lambda: session)}):
            live.sync_live()
            self.assertIsNone(live.live_snapshot()["funds"][0]["change_5m"])
            FrozenDateTime.current = first + dt.timedelta(minutes=5)
            live.sync_live()
            result = live.live_snapshot()["funds"][0]
            self.assertEqual(result["main"], 5.0)
            self.assertEqual(result["change_5m"], 2.0)
            self.assertEqual(result["previous_checked_at"], first.isoformat(timespec="seconds"))
            history = live.live_history(code)
            self.assertEqual([point["main"] for point in history["points"]], [3.0, 5.0])
            self.assertEqual([point["checked_at"] for point in history["points"]],
                             [first.isoformat(timespec="seconds"), FrozenDateTime.current.isoformat(timespec="seconds")])

    def test_upgrade_backfills_latest_value_without_losing_it(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(database, "DB_PATH", Path(directory) / "test.db"):
            with database.connect() as conn:
                conn.execute("""CREATE TABLE broad_etf_secondary_live (
                    ts_code TEXT NOT NULL, trade_date TEXT NOT NULL,
                    main REAL, small REAL, medium REAL, large REAL, super_large REAL,
                    checked_at TEXT NOT NULL, PRIMARY KEY(ts_code,trade_date))""")
                conn.execute("INSERT INTO broad_etf_secondary_live VALUES (?,?,?,?,?,?,?,?)",
                             ("510300.SH", "2026-11-23", 1.2, 0, 0, 0.5, 0.7,
                              "2026-11-23T10:30:00+08:00"))
            live.init_live_db()
            live.init_live_db()
            with database.connect() as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM broad_etf_secondary_live_history").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT main FROM broad_etf_secondary_live").fetchone()[0], 1.2)

    def test_history_rejects_unknown_etf(self):
        with self.assertRaises(ValueError):
            live.live_history("UNKNOWN")

    def test_reader_fallback_uses_fresh_validated_snapshot(self):
        today = dt.datetime.now(live.SHANGHAI).date()
        codes = ("510300.SH", "159919.SZ")
        response = MagicMock()
        response.text = "Title:\n\nMarkdown Content:\n" + json.dumps(
            self.batch_payload(codes, today, dt.datetime.now(live.SHANGHAI)))
        session = MagicMock()
        session.__enter__.return_value = session
        session.get.side_effect = [RuntimeError("502"), RuntimeError("502"), response]
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(database, "DB_PATH", Path(directory) / "test.db"), \
             patch.object(live, "FUNDS", tuple((code, code, "沪深300") for code in codes)), \
             patch.dict(sys.modules, {"requests": SimpleNamespace(Session=lambda: session)}):
            live.sync_live()
            self.assertEqual(session.get.call_count, len(live.BATCH_URLS) + 1)
            self.assertEqual(live.live_snapshot()["summary"]["covered"], 2)

    def test_batch_failure_does_not_flood_legacy_endpoint(self):
        session = MagicMock()
        session.__enter__.return_value = session
        session.get.side_effect = RuntimeError("502")
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(database, "DB_PATH", Path(directory) / "test.db"), \
             patch.object(live, "FUNDS", (("510300.SH", "甲", "沪深300"),)), \
             patch.dict(sys.modules, {"requests": SimpleNamespace(Session=lambda: session)}):
            with self.assertRaises(RuntimeError):
                live.sync_live()
            self.assertEqual(session.get.call_count, len(live.BATCH_URLS) + 1)
            self.assertEqual(len(live.live_snapshot()["errors"]), 1)

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
