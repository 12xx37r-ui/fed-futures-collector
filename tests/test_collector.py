import unittest
from collector import parse_fred_bulk_csv


class FredBulkTests(unittest.TestCase):
    def test_parses_multiple_series(self):
        text = "DATE,DFF,UNRATE\n2026-01-01,3.50,.\n2026-02-01,3.60,4.1\n"
        result = parse_fred_bulk_csv(text, ["DFF", "UNRATE"], "test")
        self.assertEqual(result["DFF"]["latest"]["value"], 3.6)
        self.assertEqual(result["UNRATE"]["latest"]["value"], 4.1)


if __name__ == "__main__":
    unittest.main()
