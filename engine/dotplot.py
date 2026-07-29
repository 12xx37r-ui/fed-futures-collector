from __future__ import annotations

import json
import re
from pathlib import Path
from statistics import median
from bs4 import BeautifulSoup


def parse_sep_page(html: str) -> dict:
    """Conservative SEP parser.

    Automatic values are exposed only when a coherent table row labelled
    Federal funds rate/policy rate is found. Ambiguous prose numbers are kept as
    rejected diagnostics and never count as a valid dot plot.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    candidates: list[float] = []
    for row in soup.find_all("tr"):
        text = " ".join(row.stripped_strings)
        if not re.search(r"federal funds rate|policy rate", text, re.I):
            continue
        values = []
        for token in re.findall(r"(?<!\d)(\d{1,2}(?:\.\d+)?)(?!\d)", text):
            value = float(token)
            if 0.0 <= value <= 10.0:
                values.append(value)
        if len(values) >= 3:
            candidates.extend(values)

    coherent = len(candidates) >= 3 and (max(candidates) - min(candidates) <= 3.0)
    return {
        "validated": coherent,
        "auto_candidates": candidates[:20] if coherent else [],
        "auto_median": median(candidates) if coherent else None,
        "rejected_candidate_count": 0 if coherent else len(candidates),
        "method": "validated_sep_table_row_v2",
    }


def load_manual_dotplot(path: str = "data/manual/dotplot.json") -> dict:
    p = Path(path)
    if not p.exists():
        return {"available": False, "reason": "manual dotplot file missing"}
    payload = json.loads(p.read_text(encoding="utf-8"))
    dots = payload.get("dots", {})
    medians = {}
    for year, values in dots.items():
        clean = [float(x) for x in values if 0.0 <= float(x) <= 10.0]
        medians[year] = median(clean) if clean else None
    source = str(payload.get("source") or "").strip()
    available = bool(source and any(v is not None for v in medians.values()))
    return {
        "available": available,
        "meeting_date": payload.get("meeting_date"),
        "medians": medians,
        "source": source or None,
        "reason": None if available else "official source URL and dot values are required",
    }
