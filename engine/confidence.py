from __future__ import annotations

CRITICAL = (
    "yahoo:ZQ=F", "fred:DFF", "fred:DGS2", "nyfed:effr", "nyfed:sofr"
)


def calculate(status: dict, features: dict) -> dict:
    sources = status.get("sources", [])
    total = len(sources)
    ok = sum(bool(x.get("ok")) for x in sources)
    source_score = 50 * ok / total if total else 0

    critical_ok = sum(
        any(x.get("name") == name and x.get("ok") for x in sources)
        for name in CRITICAL
    )
    critical_score = 25 * critical_ok / len(CRITICAL)

    feature_checks = [
        bool(features.get("zq_curve")),
        bool(features.get("sofr_curve")),
        bool(features.get("fomc_dates")),
        features.get("fed_text_score") is not None,
        bool(features.get("dotplot_available")),
    ]
    feature_score = 25 * sum(feature_checks) / len(feature_checks)

    score = round(source_score + critical_score + feature_score)
    grade = "HIGH" if score >= 85 else "MEDIUM" if score >= 70 else "LOW" if score >= 50 else "FAIL"
    return {
        "score": score,
        "grade": grade,
        "source_ok": ok,
        "source_total": total,
        "critical_ok": critical_ok,
        "feature_checks_passed": sum(feature_checks),
        "feature_checks_total": len(feature_checks),
    }
