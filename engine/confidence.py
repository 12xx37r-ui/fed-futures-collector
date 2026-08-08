from __future__ import annotations

CRITICAL = ("yahoo:ZQ=F", "fred:DFF", "fred:DGS2", "nyfed:effr", "nyfed:sofr")


def _validation_gate(backtest: dict) -> dict:
    gate = dict(backtest.get("quality_gate") or {})
    if gate:
        return gate
    return {
        "passed": False,
        "candidate": False,
        "level": "검증 미통과",
        "requirements": {
            "historical_reconstructed_rows_min": 60,
            "direction_accuracy_min": 0.60,
            "direction_accuracy_wilson_lower_95_min_exclusive": 0.50,
            "direction_skill_vs_majority_min_exclusive": 0.0,
            "brier_skill_score_min": 0.10,
        },
        "observed": {
            "historical_reconstructed_rows": int(backtest.get("historical_reconstructed_rows") or 0),
            "direction_accuracy": backtest.get("direction_accuracy"),
            "direction_accuracy_wilson_lower_95": backtest.get("direction_accuracy_wilson_lower_95"),
            "direction_skill_vs_majority": backtest.get("direction_skill_vs_majority"),
            "brier_skill_score": backtest.get("brier_skill_score"),
        },
    }


def _model_validation_score(backtest: dict, validation: dict) -> int:
    value = backtest.get("model_validation_score")
    try:
        return max(0, min(100, round(float(value))))
    except (TypeError, ValueError):
        pass

    # Backward-compatible diagnostic when an older backtest.json is present.
    observed = validation.get("observed") or {}
    samples = int(observed.get("historical_reconstructed_rows") or 0)
    accuracy = observed.get("direction_accuracy")
    wilson = observed.get("direction_accuracy_wilson_lower_95")
    brier_skill = observed.get("brier_skill_score")
    direction_skill = observed.get("direction_skill_vs_majority")

    def clamp01(x: float) -> float:
        return max(0.0, min(1.0, x))

    sample_part = 20 * clamp01(samples / 60.0)
    accuracy_part = 25 * clamp01(((float(accuracy) if accuracy is not None else 0.0) - 0.50) / 0.20)
    wilson_part = 20 * clamp01(((float(wilson) if wilson is not None else 0.0) - 0.45) / 0.15)
    brier_part = 20 * clamp01((float(brier_skill) if brier_skill is not None else 0.0) / 0.20)
    direction_part = 15 * clamp01((float(direction_skill) if direction_skill is not None else 0.0) / 0.15)
    return round(sample_part + accuracy_part + wilson_part + brier_part + direction_part)


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

    backtest = backtest or {}
    validation = _validation_gate(backtest)
    model_score = _model_validation_score(backtest, validation)
    if validation.get("passed"):
        model_grade = "VALIDATED"
    elif validation.get("candidate"):
        model_grade = "CANDIDATE"
    else:
        model_grade = "UNVERIFIED"

    # `score`/`grade` are retained only as a conservative compatibility field
    # for older consumers.  New consumers must use the explicit data-quality
    # and model-validation fields below.
    if validation.get("passed"):
        legacy_score, legacy_grade = data_score, data_grade
    elif validation.get("candidate"):
        legacy_score, legacy_grade = min(data_score, 69), "CANDIDATE"
    else:
        legacy_score, legacy_grade = min(data_score, 49), "UNVERIFIED"

    return {
        "score": legacy_score,
        "grade": legacy_grade,
        "model_validation_score": model_score,
        "model_validation_grade": model_grade,
        "model_validation_level": validation.get("level") or "검증 미통과",
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
