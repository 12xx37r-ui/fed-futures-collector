from __future__ import annotations

import re
from datetime import date

from bs4 import BeautifulSoup

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}
MONTH_PATTERN = "|".join(MONTHS)

# Scheduled FOMC meetings are normally displayed as date ranges, e.g. July 28-29.
# Requiring a range prevents "Minutes released January 5" and similar dates
# from being mistaken for policy-decision dates.
MEETING_RANGE_RE = re.compile(
    rf"\b({MONTH_PATTERN})\s+(\d{{1,2}})\s*[-–—]\s*(\d{{1,2}})\*?",
    re.IGNORECASE,
)
YEAR_SECTION_RE = re.compile(
    r"\b(20\d{2})\s+FOMC\s+Meetings\b(.*?)(?=\b20\d{2}\s+FOMC\s+Meetings\b|$)",
    re.IGNORECASE | re.DOTALL,
)


def parse_fomc_dates(html: str) -> list[str]:
    """Extract scheduled FOMC decision dates from the official calendar page.

    Only scheduled date ranges are accepted. The final day of each range is
    treated as the decision date. At most one scheduled meeting per month is
    retained, preventing minutes-release dates and duplicated links from
    entering the market-path calculation.
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

            key = (decision.year, decision.month)
            # The official schedule should contain one scheduled meeting per
            # month. Keeping the first range blocks duplicate page elements.
            by_month.setdefault(key, decision.isoformat())

    return sorted(by_month.values())


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
