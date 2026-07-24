from __future__ import annotations

CRITICAL = ("yahoo:ZQ=F", "fred:DFF", "fred:DGS2", "nyfed:effr", "nyfed:sofr")


def calculate(status: dict, features: dict) -> dict:
    sources = status.get("sources", [])

    # Candidate scans intentionally probe invalid ticker variants.  Counting every
    # 404 as a failed source makes confidence meaningless, so score logical groups.
    groups = {
        "zq_continuous": any(x.get("name") == "yahoo:ZQ=F" and x.get("ok") for x in sources),
        "zq_curve": bool(features.get("zq_curve")),
        "sofr_curve": bool(features.get("sofr_curve")),
        "fred": any(str(x.get("name", "")).startswith("fred:") and x.get("ok") for x in sources),
        "nyfed_effr": any(x.get("name") == "nyfed:effr" and x.get("ok") for x in sources),
        "nyfed_sofr": any(x.get("name") == "nyfed:sofr" and x.get("ok") for x in sources),
        "fomc_calendar": bool(features.get("fomc_dates")),
        "fed_text": features.get("fed_text_score") is not None,
        "dotplot": bool(features.get("dotplot_available")),
    }
    group_ok = sum(groups.values())
    source_score = 60 * group_ok / len(groups)

    critical_ok = sum(any(x.get("name") == name and x.get("ok") for x in sources) for name in CRITICAL)
    critical_score = 25 * critical_ok / len(CRITICAL)

    stale_fred = sum(bool(x.get("stale")) for x in sources if str(x.get("name", "")).startswith("fred:"))
    stale_penalty = min(15, stale_fred * 1.5)

    score = round(max(0, source_score + critical_score + 15 - stale_penalty))
    grade = "HIGH" if score >= 85 else "MEDIUM" if score >= 70 else "LOW" if score >= 50 else "FAIL"
    return {
        "score": score,
        "grade": grade,
        "logical_groups_ok": group_ok,
        "logical_groups_total": len(groups),
        "critical_ok": critical_ok,
        "critical_total": len(CRITICAL),
        "stale_fred_series": stale_fred,
        "groups": groups,
    }
