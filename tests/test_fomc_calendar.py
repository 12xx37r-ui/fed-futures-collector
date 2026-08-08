import json
import unittest
from datetime import date
from pathlib import Path

from engine.fomc_calendar import all_fomc_dates, next_meeting, parse_fomc_dates


class FomcCalendarTests(unittest.TestCase):

    def test_cross_month_meeting_is_parsed(self):
        html = """<div>2024 FOMC Meetings</div><div>Jan/Feb 31-1</div><div>Apr/May 30-1</div>"""
        dates = parse_fomc_dates(html)
        self.assertIn("2024-02-01", dates)
        self.assertIn("2024-05-01", dates)

    def test_historical_regular_dates_are_merged(self):
        self.assertIn("2019-07-31", all_fomc_dates(""))

    def test_official_saved_page(self):
        raw_path = Path("public/data/raw.json")
        if not raw_path.exists():
            self.skipTest("raw.json unavailable")
        raw = json.loads(raw_path.read_text(encoding="utf-8"))
        html = (raw.get("fed", {}).get("fomc_calendar") or {}).get("text", "")
        dates = parse_fomc_dates(html)
        self.assertIn("2026-07-29", dates)
        self.assertEqual(next_meeting(dates, date(2026, 7, 24)), "2026-07-29")


if __name__ == "__main__":
    unittest.main()
