from __future__ import annotations

import json
import re
from pathlib import Path
from statistics import median
from bs4 import BeautifulSoup


def parse_sep_page(html: str) -> dict:
    text = " ".join(BeautifulSoup(html or "", "html.parser").stripped_strings)
    # 공식 페이지에서 명시적으로 읽을 수 있는 중앙값 문구 탐색.
    candidates = []
    for match in re.finditer(r"(?:federal funds rate|policy rate).{0,180}?(\d+\.\d+)", text, re.I):
        try:
            value = float(match.group(1))
            if 0 <= value <= 15:
                candidates.append(value)
        except ValueError:
            pass
    return {
        "auto_candidates": candidates[:20],
        "auto_median": median(candidates) if candidates else None,
    }


def load_manual_dotplot(path: str = "data/manual/dotplot.json") -> dict:
    p = Path(path)
    if not p.exists():
        return {"available": False, "reason": "manual dotplot file missing"}
    payload = json.loads(p.read_text(encoding="utf-8"))
    dots = payload.get("dots", {})
    medians = {}
    for year, values in dots.items():
        clean = [float(x) for x in values]
        medians[year] = median(clean) if clean else None
    return {
        "available": True,
        "meeting_date": payload.get("meeting_date"),
        "medians": medians,
        "source": payload.get("source"),
    }
