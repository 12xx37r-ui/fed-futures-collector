import json
import unittest
from datetime import date
from pathlib import Path

from engine.fomc_calendar import next_meeting, parse_fomc_dates


class FomcCalendarTests(unittest.TestCase):
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
