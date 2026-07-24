import unittest

from collector import parse_fred_series_csv


class CollectorTests(unittest.TestCase):
    def test_fred_observation_date_header(self):
        text = "observation_date,DGS10\n2026-07-01,4.48\n2026-07-02,.\n"
        result = parse_fred_series_csv(text, "DGS10", "test")
        self.assertEqual(result["latest"]["date"], "2026-07-01")
        self.assertEqual(result["latest"]["value"], 4.48)

    def test_fred_legacy_date_header(self):
        text = "DATE,UNRATE\n2026-06-01,4.1\n"
        result = parse_fred_series_csv(text, "UNRATE", "test")
        self.assertEqual(result["latest"]["value"], 4.1)


if __name__ == "__main__":
    unittest.main()
