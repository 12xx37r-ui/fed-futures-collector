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
MEETING_RE = re.compile(
    rf"\b({MONTH_PATTERN})\s+(\d{{1,2}})(?:\s*[-–—]\s*(\d{{1,2}}))?\*?",
    re.IGNORECASE,
)
YEAR_SECTION_RE = re.compile(
    r"\b(20\d{2})\s+FOMC\s+Meetings\b(.*?)(?=\b20\d{2}\s+FOMC\s+Meetings\b|$)",
    re.IGNORECASE | re.DOTALL,
)


def parse_fomc_dates(html: str) -> list[str]:
    """Extract FOMC decision dates from the official calendar page.

    The Fed page exposes headings such as ``2026 FOMC Meetings`` followed by
    entries such as ``July 28-29``.  The policy decision date is the final day.
    """
    if not html:
        return []

    text = " ".join(BeautifulSoup(html, "html.parser").stripped_strings)
    text = re.sub(r"\s+", " ", text)
    dates: set[str] = set()

    for section in YEAR_SECTION_RE.finditer(text):
        year = int(section.group(1))
        body = section.group(2)
        for match in MEETING_RE.finditer(body):
            month_name, first_day, second_day = match.groups()
            day = int(second_day or first_day)
            try:
                dates.add(date(year, MONTHS[month_name.lower()], day).isoformat())
            except ValueError:
                continue

    return sorted(dates)


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
