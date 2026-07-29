from __future__ import annotations

CRITICAL = ("yahoo:ZQ=F", "fred:DFF", "fred:DGS2", "nyfed:effr", "nyfed:sofr")


def _validation_gate(backtest: dict) -> dict:
    rows = int(backtest.get("labeled_rows") or 0)
    accuracy = backtest.get("direction_accuracy")
    skill = backtest.get("brier_skill_score")
    passed = (
        rows >= 40
        and accuracy is not None and float(accuracy) >= 0.50
        and skill is not None and float(skill) > 0.0
    )
    candidate = rows >= 20 and skill is not None and float(skill) > 0.0
    return {
        "passed": passed,
        "candidate": candidate,
        "level": "준기관급" if passed else ("준기관급 후보" if candidate else "검증 축적 중"),
        "requirements": {
            "labeled_rows_min": 40,
            "direction_accuracy_min": 0.50,
            "brier_skill_score_min": 0.0,
        },
        "observed": {
            "labeled_rows": rows,
            "direction_accuracy": accuracy,
            "brier_skill_score": skill,
            "mean_brier": backtest.get("mean_brier"),
            "mean_log_loss": backtest.get("mean_log_loss"),
        },
    }


def calculate(status: dict, features: dict, backtest: dict | None = None) -> dict:
    sources = status.get("sources", [])
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
    data_score = round(max(0, source_score + critical_score + 15 - stale_penalty))
    data_grade = "HIGH" if data_score >= 85 else "MEDIUM" if data_score >= 70 else "LOW" if data_score >= 50 else "FAIL"

    validation = _validation_gate(backtest or {})
    # Preserve score compatibility, but never equate source completeness with forecast validation.
    if validation["passed"]:
        score, grade = data_score, data_grade
    elif validation["candidate"]:
        score, grade = min(data_score, 69), "CANDIDATE"
    else:
        score, grade = min(data_score, 49), "UNVERIFIED"

    return {
        "score": score,
        "grade": grade,
        "data_quality_score": data_score,
        "data_quality_grade": data_grade,
        "forecast_validation": validation,
        "logical_groups_ok": group_ok,
        "logical_groups_total": len(groups),
        "critical_ok": critical_ok,
        "critical_total": len(CRITICAL),
        "stale_fred_series": stale_fred,
        "groups": groups,
    }
