from __future__ import annotations

import re
from datetime import date
from bs4 import BeautifulSoup

MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}

MEETING_RE = re.compile(
    r"\b("
    + "|".join(MONTHS)
    + r")\s+(\d{1,2})(?:\s*[-–—]\s*(\d{1,2}))?",
    re.IGNORECASE,
)
YEAR_RE = re.compile(r"\b(20\d{2})\b")


def _extract_year_sections(soup: BeautifulSoup) -> list[tuple[int, str]]:
    """
    연도 제목부터 다음 연도 제목 전까지를 한 구간으로 묶는다.
    Fed 페이지의 div/h2/h3 구조가 바뀌어도 텍스트 순서를 이용한다.
    """
    sections: list[tuple[int, list[str]]] = []
    current_year: int | None = None
    current_parts: list[str] = []

    for element in soup.find_all(["h1", "h2", "h3", "h4", "div", "p", "td", "th"]):
        text = " ".join(element.stripped_strings)
        if not text:
            continue

        year_match = YEAR_RE.fullmatch(text.strip())
        if year_match:
            if current_year is not None and current_parts:
                sections.append((current_year, current_parts))
            current_year = int(year_match.group(1))
            current_parts = []
            continue

        if current_year is not None:
            current_parts.append(text)

    if current_year is not None and current_parts:
        sections.append((current_year, current_parts))

    return [(year, " ".join(parts)) for year, parts in sections]


def parse_fomc_dates(html: str) -> list[str]:
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    dates: set[str] = set()

    sections = _extract_year_sections(soup)

    # 연도별 구간 파싱
    for year, section_text in sections:
        for match in MEETING_RE.finditer(section_text):
            month_name, first_day, second_day = match.groups()
            # 정책결정 발표일은 이틀 회의의 마지막 날
            day = int(second_day or first_day)
            month = MONTHS[month_name.lower()]
            try:
                dates.add(date(year, month, day).isoformat())
            except ValueError:
                continue

    # 구간 파싱 실패 시 전체 텍스트 + 가까운 연도 기반 보조 파싱
    if not dates:
        text = " ".join(soup.stripped_strings)
        year_positions = [
            (m.start(), int(m.group(1)))
            for m in YEAR_RE.finditer(text)
        ]

        for match in MEETING_RE.finditer(text):
            previous_years = [item for item in year_positions if item[0] <= match.start()]
            year = previous_years[-1][1] if previous_years else date.today().year
            month_name, first_day, second_day = match.groups()
            day = int(second_day or first_day)
            month = MONTHS[month_name.lower()]
            try:
                dates.add(date(year, month, day).isoformat())
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
