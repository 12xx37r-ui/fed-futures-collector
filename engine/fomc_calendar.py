from __future__ import annotations

import re
from datetime import date

from bs4 import BeautifulSoup

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}
MONTH_ALIASES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
MONTH_PATTERN = "|".join(MONTHS)

# Standard same-month ranges, e.g. July 28-29.
MEETING_RANGE_RE = re.compile(
    rf"\b({MONTH_PATTERN})\s+(\d{{1,2}})\s*[-–—]\s*(\d{{1,2}})\*?",
    re.IGNORECASE,
)
# Cross-month ranges shown by the Fed as Jan/Feb 31-1, Apr/May 30-1, etc.
CROSS_MONTH_RE = re.compile(
    r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\s*/\s*"
    r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\s+"
    r"(\d{1,2})\s*[-–—]\s*(\d{1,2})\*?",
    re.IGNORECASE,
)
YEAR_SECTION_RE = re.compile(
    r"\b(20\d{2})\s+FOMC\s+Meetings\b(.*?)(?=\b20\d{2}\s+FOMC\s+Meetings\b|$)",
    re.IGNORECASE | re.DOTALL,
)

# Decision dates from the Federal Reserve's historical FOMC pages.  Only
# regularly scheduled meetings are included.  The extraordinary March 2020
# actions are deliberately excluded because they were not known one week in
# advance and therefore are not fair "scheduled next-meeting" backtest cases.
HISTORICAL_REGULAR_FOMC_DATES = (
    # 2015
    "2015-01-28", "2015-03-18", "2015-04-29", "2015-06-17",
    "2015-07-29", "2015-09-17", "2015-10-28", "2015-12-16",
    # 2016
    "2016-01-27", "2016-03-16", "2016-04-27", "2016-06-15",
    "2016-07-27", "2016-09-21", "2016-11-02", "2016-12-14",
    # 2017
    "2017-02-01", "2017-03-15", "2017-05-03", "2017-06-14",
    "2017-07-26", "2017-09-20", "2017-11-01", "2017-12-13",
    # 2018
    "2018-01-31", "2018-03-21", "2018-05-02", "2018-06-13",
    "2018-08-01", "2018-09-26", "2018-11-08", "2018-12-19",
    # 2019
    "2019-01-30", "2019-03-20", "2019-05-01", "2019-06-19",
    "2019-07-31", "2019-09-18", "2019-10-30", "2019-12-11",
    # 2020 regular scheduled meetings only
    "2020-01-29", "2020-04-29", "2020-06-10", "2020-07-29",
    "2020-09-16", "2020-11-05", "2020-12-16",
)


def parse_fomc_dates(html: str) -> list[str]:
    """Extract scheduled FOMC decision dates from the official calendar page.

    Both normal same-month ranges and the Fed's cross-month notation are
    supported.  The final day of the range is the policy-decision date.
    """
    if not html:
        return []

    text = " ".join(BeautifulSoup(html, "html.parser").stripped_strings)
    text = re.sub(r"\s+", " ", text)
    by_month: dict[tuple[int, int], str] = {}

    for section in YEAR_SECTION_RE.finditer(text):
        year = int(section.group(1))
        body = section.group(2)

        for match in MEETING_RANGE_RE.finditer(body):
            month_name, _first_day, final_day = match.groups()
            month = MONTHS[month_name.lower()]
            try:
                decision = date(year, month, int(final_day))
            except ValueError:
                continue
            by_month.setdefault((decision.year, decision.month), decision.isoformat())

        for match in CROSS_MONTH_RE.finditer(body):
            _first_month, second_month, _first_day, final_day = match.groups()
            month = MONTH_ALIASES[second_month.lower()]
            try:
                decision = date(year, month, int(final_day))
            except ValueError:
                continue
            by_month.setdefault((decision.year, decision.month), decision.isoformat())

    return sorted(by_month.values())


def all_fomc_dates(html: str) -> list[str]:
    """Merge the live Fed calendar with audited regular historical meetings."""
    return sorted(set(HISTORICAL_REGULAR_FOMC_DATES).union(parse_fomc_dates(html)))


def next_meeting(dates: list[str], today: date | None = None) -> str | None:
    today = today or date.today()
    for value in sorted(set(dates)):
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            continue
        if parsed >= today:
            return value
    return None
