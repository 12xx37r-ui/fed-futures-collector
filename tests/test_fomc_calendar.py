import unittest
from datetime import date

from engine.fomc_calendar import next_meeting, parse_fomc_dates


class FomcCalendarTests(unittest.TestCase):
    def test_official_page_pattern(self):
        html = """
        <div class="fomc-meeting">
          <div class="fomc-meeting__month">July</div>
          <div class="fomc-meeting__date">28-29</div>
          <div class="fomc-meeting__year">2026</div>
        </div>
        """
        dates = parse_fomc_dates(html)
        self.assertIn("2026-07-29", dates)
        self.assertEqual(next_meeting(dates, date(2026, 7, 24)), "2026-07-29")


if __name__ == "__main__":
    unittest.main()
