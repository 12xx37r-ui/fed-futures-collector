from __future__ import annotations

from typing import Any


def calculate_confidence(status_payload: dict[str, Any]) -> dict[str, Any]:
    sources = status_payload.get("sources", [])
    if not sources:
        return {"score": 0, "grade": "FAIL", "usable_sources": 0, "total_sources": 0}

    total = len(sources)
    usable = sum(1 for item in sources if item.get("ok"))
    ratio = usable / total

    score = round(ratio * 70)

    critical_prefixes = [
        "yahoo:ZQ=F",
        "fred:DFF",
        "fred:DGS2",
        "nyfed:effr",
        "nyfed:sofr",
    ]
    critical_ok = 0
    for prefix in critical_prefixes:
        if any(
            item.get("name") == prefix and item.get("ok")
            for item in sources
        ):
            critical_ok += 1
    score += round((critical_ok / len(critical_prefixes)) * 30)
    score = min(score, 100)

    if score >= 85:
        grade = "HIGH"
    elif score >= 70:
        grade = "MEDIUM"
    elif score >= 50:
        grade = "LOW"
    else:
        grade = "FAIL"

    return {
        "score": score,
        "grade": grade,
        "usable_sources": usable,
        "total_sources": total,
        "critical_sources_ok": critical_ok,
        "critical_sources_total": len(critical_prefixes),
    }
