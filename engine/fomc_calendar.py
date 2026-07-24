from __future__ import annotations

import re
from datetime import date, datetime
from bs4 import BeautifulSoup
from dateutil import parser


def parse_fomc_dates(html: str) -> list[str]:
    soup = BeautifulSoup(html or "", "html.parser")
    text = " ".join(soup.stripped_strings)
    years = re.findall(r"\b20\d{2}\b", text)
    current_year = int(years[0]) if years else date.today().year

    months = {
        "January": 1, "February": 2, "March": 3, "April": 4,
        "May": 5, "June": 6, "July": 7, "August": 8,
        "September": 9, "October": 10, "November": 11, "December": 12,
    }
    found = []
    pattern = re.compile(
        r"(January|February|March|April|May|June|July|August|September|October|November|December)"
        r"\s+(\d{1,2})(?:-(\d{1,2}))?"
    )
    for match in pattern.finditer(text):
        month_name, day1, day2 = match.groups()
        day = int(day2 or day1)
        try:
            found.append(date(current_year, months[month_name], day).isoformat())
        except ValueError:
            pass
    return sorted(set(found))


def next_meeting(dates: list[str], today: date | None = None) -> str | None:
    today = today or date.today()
    for value in sorted(dates):
        if date.fromisoformat(value) >= today:
            return value
    return None
