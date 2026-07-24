from __future__ import annotations

import re
from bs4 import BeautifulSoup

HAWKISH = {
    "inflation remains elevated": 3,
    "upside risks to inflation": 3,
    "restrictive": 1,
    "not appropriate to reduce": 3,
    "higher for longer": 3,
    "price stability": 1,
    "persistent inflation": 2,
}
DOVISH = {
    "downside risks to employment": 3,
    "labor market has cooled": 2,
    "appropriate to reduce": 3,
    "policy easing": 2,
    "disinflation": 1,
    "economic activity has slowed": 2,
    "risks are balanced": 1,
}


def text_score(*documents: str) -> dict:
    text = " ".join(BeautifulSoup(d or "", "html.parser").get_text(" ") for d in documents).lower()
    text = re.sub(r"\s+", " ", text)
    hawkish_hits = {k: text.count(k) for k in HAWKISH if k in text}
    dovish_hits = {k: text.count(k) for k in DOVISH if k in text}
    hawkish = sum(HAWKISH[k] * n for k, n in hawkish_hits.items())
    dovish = sum(DOVISH[k] * n for k, n in dovish_hits.items())
    total = hawkish + dovish
    score = 0.0 if total == 0 else (dovish - hawkish) / total
    return {
        "score": round(max(-1.0, min(1.0, score)), 5),
        "hawkish_hits": hawkish_hits,
        "dovish_hits": dovish_hits,
        "method": "auditable_lexicon_v1",
    }
