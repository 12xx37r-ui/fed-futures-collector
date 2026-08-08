from __future__ import annotations

import json
import math
from datetime import date, timedelta
from pathlib import Path

from engine.fomc_calendar import all_fomc_dates
from engine.policy_regime import actual_direction
from .reconstruct import reconstruct

LABELS = ("cut", "hold", "hike")


def brier(prob: dict, actual: str) -> float:
    return sum(((prob.get(k, 0) / 100) - (1 if k == actual else 0)) ** 2 for k in LABELS)


def log_loss(prob: dict, actual: str) -> float:
    p = max(1e-9, prob.get(actual, 0) / 100)
    return -math.log(p)


def _auto_label(history: list[dict], raw: dict) -> int:
    """Label matured live snapshots from the actual policy decision.

    The FOMC target-range midpoint is preferred when collected; DFF is the
    backward-compatible fallback inside ``actual_direction``.
    """
    today = date.today()
    labeled = 0
    for row in history:
        if row.get("actual_direction") in LABELS or not row.get("meeting"):
            continue
        try:
            meeting = date.fromisoformat(row["meeting"])
        except ValueError:
            continue
        if today < meeting + timedelta(days=5):
            continue
        actual = actual_direction(raw, meeting)
        if not actual:
            continue
        row["actual_direction"] = actual[0]
        row["actual_change_bps"] = actual[1]
        row["label_source"] = "FOMC_target_range_midpoint_or_DFF_fallback"
        labeled += 1
    return labeled


def _score_rows(rows: list[dict]) -> dict:
    if not rows:
        return {}
    correct = 0
    briers, losses, benchmark_briers = [], [], []
    prior_counts = {"cut": 1, "hold": 3, "hike": 1}
    for row in rows:
        prob = row["probabilities"]
        actual = row["actual_direction"]
        correct += max(prob, key=prob.get) == actual
        briers.append(brier(prob, actual))
        losses.append(log_loss(prob, actual))
        total = sum(prior_counts.values())
        benchmark_prob = {k: prior_counts[k] / total * 100 for k in LABELS}
        benchmark_briers.append(brier(benchmark_prob, actual))
        prior_counts[actual] += 1
    mean_brier = sum(briers) / len(briers)
    benchmark = sum(benchmark_briers) / len(benchmark_briers)
    skill = 1 - mean_brier / benchmark if benchmark > 0 else None
    n = len(rows)
    accuracy = correct / n
    z = 1.959963984540054
    denom = 1 + z*z/n
    accuracy_lb = (accuracy + z*z/(2*n) - z*math.sqrt((accuracy*(1-accuracy)+z*z/(4*n))/n)) / denom
    class_frequency = {k: sum(r["actual_direction"] == k for r in rows) / n for k in LABELS}
    majority_accuracy = max(class_frequency.values())
    return {
        "direction_accuracy": accuracy,
        "direction_accuracy_wilson_lower_95": max(0.0, accuracy_lb),
        "majority_class_accuracy": majority_accuracy,
        "direction_skill_vs_majority": accuracy-majority_accuracy,
        "mean_brier": mean_brier,
        "mean_log_loss": sum(losses) / len(losses),
        "benchmark_brier": benchmark,
        "brier_skill_score": skill,
        "class_frequency": class_frequency,
    }


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _validation_score(samples: int, scored: dict) -> int:
    """Transparent 0-100 diagnostic score; not a probability of being correct."""
    accuracy = scored.get("direction_accuracy")
    wilson = scored.get("direction_accuracy_wilson_lower_95")
    brier_skill = scored.get("brier_skill_score")
    direction_skill = scored.get("direction_skill_vs_majority")
    sample_part = 20 * _clamp01(samples / 60.0)
    accuracy_part = 25 * _clamp01(((accuracy or 0.0) - 0.50) / 0.20)
    wilson_part = 20 * _clamp01(((wilson or 0.0) - 0.45) / 0.15)
    brier_part = 20 * _clamp01((brier_skill or 0.0) / 0.20)
    direction_part = 15 * _clamp01((direction_skill or 0.0) / 0.15)
    return round(sample_part + accuracy_part + wilson_part + brier_part + direction_part)


def main() -> None:
    history_path = Path("public/data/history.json")
    raw_path = Path("public/data/raw.json")
    history = json.loads(history_path.read_text(encoding="utf-8")) if history_path.exists() else []
    raw = json.loads(raw_path.read_text(encoding="utf-8")) if raw_path.exists() else {}
    newly_labeled = _auto_label(history, raw)
    history_path.write_text(json.dumps(history[-5000:], ensure_ascii=False, indent=2), encoding="utf-8")

    # Live-vintage validation must score the auxiliary model itself.  Older
    # snapshots only stored the representative (usually market-implied)
    # probabilities, so they are deliberately not mislabelled as model OOS.
    live_rows = []
    for row in history:
        model_prob = row.get("model_probabilities")
        if row.get("actual_direction") not in LABELS or not isinstance(model_prob, dict):
            continue
        scored_row = dict(row)
        scored_row["probabilities"] = model_prob
        live_rows.append(scored_row)
    calendar_html = ((raw.get("fed") or {}).get("fomc_calendar") or {}).get("text", "")
    historical_rows = reconstruct(raw, all_fomc_dates(calendar_html))
    scored = _score_rows(historical_rows)

    samples = len(historical_rows)
    accuracy = scored.get("direction_accuracy")
    skill = scored.get("brier_skill_score")
    accuracy_lb = scored.get("direction_accuracy_wilson_lower_95")
    direction_skill = scored.get("direction_skill_vs_majority")

    # This gate validates the release-lag reconstruction.  It is deliberately
    # separate from the stronger live-vintage gate below; no field claims that
    # revised macro history is a true ALFRED vintage.
    reconstructed_passed = (
        samples >= 60 and accuracy is not None and accuracy >= 0.60
        and accuracy_lb is not None and accuracy_lb > 0.50
        and direction_skill is not None and direction_skill > 0
        and skill is not None and skill >= 0.10
    )
    candidate = (
        not reconstructed_passed and samples >= 40 and accuracy is not None and accuracy >= 0.60
        and skill is not None and skill >= 0.10
        and direction_skill is not None and direction_skill > 0
    )

    live_scored = _score_rows(live_rows)
    live_samples = len(live_rows)
    live_vintage_passed = (
        live_samples >= 40
        and (live_scored.get("direction_accuracy") or 0) >= 0.55
        and (live_scored.get("direction_accuracy_wilson_lower_95") or 0) > 0.50
        and (live_scored.get("brier_skill_score") or 0) >= 0.05
    )
    validation_score = _validation_score(samples, scored)

    result = {
        "labeled_rows": live_samples,
        "newly_labeled_rows": newly_labeled,
        "historical_reconstructed_rows": samples,
        "validation_method": "release_lagged_policy_regime_fomc",
        "release_lag_backtest": True,
        "real_time_vintage": live_vintage_passed,
        "model_validation_score": validation_score,
        "direction_accuracy": scored.get("direction_accuracy"),
        "direction_accuracy_wilson_lower_95": accuracy_lb,
        "majority_class_accuracy": scored.get("majority_class_accuracy"),
        "direction_skill_vs_majority": direction_skill,
        "mean_brier": scored.get("mean_brier"),
        "mean_log_loss": scored.get("mean_log_loss"),
        "benchmark_brier": scored.get("benchmark_brier"),
        "brier_skill_score": scored.get("brier_skill_score"),
        "class_frequency": scored.get("class_frequency"),
        "quality_gate": {
            "passed": reconstructed_passed,
            "candidate": candidate,
            "level": "발표시차 OOS 통과" if reconstructed_passed else ("검증 후보·표본확대 중" if candidate else "검증 미통과"),
            "requirements": {
                "historical_reconstructed_rows_min": 60,
                "direction_accuracy_min": 0.60,
                "direction_accuracy_wilson_lower_95_min_exclusive": 0.50,
                "direction_skill_vs_majority_min_exclusive": 0.0,
                "brier_skill_score_min": 0.10,
                "release_lag_backtest_required": True,
            },
            "observed": {
                "historical_reconstructed_rows": samples,
                "direction_accuracy": accuracy,
                "direction_accuracy_wilson_lower_95": accuracy_lb,
                "majority_class_accuracy": scored.get("majority_class_accuracy"),
                "direction_skill_vs_majority": direction_skill,
                "brier_skill_score": skill,
                "release_lag_backtest": True,
            },
        },
        "live_vintage_gate": {
            "passed": live_vintage_passed,
            "level": "실시간 스냅숏 검증 통과" if live_vintage_passed else "실시간 스냅숏 누적 중",
            "samples": live_samples,
            "direction_accuracy": live_scored.get("direction_accuracy"),
            "direction_accuracy_wilson_lower_95": live_scored.get("direction_accuracy_wilson_lower_95"),
            "brier_skill_score": live_scored.get("brier_skill_score"),
        },
        "limitations": [
            "발표시차 OOS는 각 지표의 공개 지연을 적용하지만 수정 전 원본 빈티지를 완전히 재현한 것은 아닙니다.",
            "실시간 스냅숏 검증은 별도로 누적하며 충분한 표본이 쌓이기 전에는 시장 내재확률을 대표값으로 유지합니다.",
        ],
    }
    Path("public/data/historical_backtest_rows.json").write_text(json.dumps(historical_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    Path("public/data/backtest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
