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
    briers, losses, benchmark_briers, benchmark_losses = [], [], [], []
    prior_counts = {"cut": 1, "hold": 3, "hike": 1}
    confusion = {actual: {pred: 0 for pred in LABELS} for actual in LABELS}
    predicted_counts = {k: 0 for k in LABELS}
    for row in rows:
        prob = row["probabilities"]
        actual = row["actual_direction"]
        predicted = max(prob, key=prob.get)
        correct += predicted == actual
        confusion[actual][predicted] += 1
        predicted_counts[predicted] += 1
        briers.append(brier(prob, actual))
        losses.append(log_loss(prob, actual))
        total = sum(prior_counts.values())
        benchmark_prob = {k: prior_counts[k] / total * 100 for k in LABELS}
        benchmark_briers.append(brier(benchmark_prob, actual))
        benchmark_losses.append(log_loss(benchmark_prob, actual))
        prior_counts[actual] += 1
    mean_brier = sum(briers) / len(briers)
    benchmark = sum(benchmark_briers) / len(benchmark_briers)
    benchmark_log_loss = sum(benchmark_losses) / len(benchmark_losses)
    skill = 1 - mean_brier / benchmark if benchmark > 0 else None
    log_loss_skill = 1 - (sum(losses) / len(losses)) / benchmark_log_loss if benchmark_log_loss > 0 else None
    n = len(rows)
    accuracy = correct / n
    z = 1.959963984540054
    denom = 1 + z*z/n
    accuracy_lb = (accuracy + z*z/(2*n) - z*math.sqrt((accuracy*(1-accuracy)+z*z/(4*n))/n)) / denom
    class_frequency = {k: sum(r["actual_direction"] == k for r in rows) / n for k in LABELS}
    predicted_frequency = {k: predicted_counts[k] / n for k in LABELS}
    majority_accuracy = max(class_frequency.values())
    recalls = {}
    precisions = {}
    for k in LABELS:
        actual_total = sum(confusion[k].values())
        pred_total = sum(confusion[a][k] for a in LABELS)
        recalls[k] = confusion[k][k] / actual_total if actual_total else None
        precisions[k] = confusion[k][k] / pred_total if pred_total else None
    valid_recalls = [v for v in recalls.values() if v is not None]
    balanced_accuracy = sum(valid_recalls) / len(valid_recalls) if valid_recalls else None
    non_hold_actual = sum(sum(confusion[k].values()) for k in ("cut", "hike"))
    non_hold_correct = confusion["cut"]["cut"] + confusion["hike"]["hike"]
    non_hold_recall = non_hold_correct / non_hold_actual if non_hold_actual else None
    return {
        "direction_accuracy": accuracy,
        "direction_accuracy_wilson_lower_95": max(0.0, accuracy_lb),
        "majority_class_accuracy": majority_accuracy,
        "direction_skill_vs_majority": accuracy-majority_accuracy,
        "mean_brier": mean_brier,
        "mean_log_loss": sum(losses) / len(losses),
        "benchmark_log_loss": benchmark_log_loss,
        "log_loss_skill_score": log_loss_skill,
        "benchmark_brier": benchmark,
        "brier_skill_score": skill,
        "class_frequency": class_frequency,
        "predicted_class_frequency": predicted_frequency,
        "confusion_matrix": confusion,
        "recall_by_class": recalls,
        "precision_by_class": precisions,
        "balanced_accuracy": balanced_accuracy,
        "non_hold_recall": non_hold_recall,
    }


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _validation_score(samples: int, scored: dict) -> int:
    """0-100 probability-forecast validation quality; not hit-rate probability."""
    wilson = scored.get("direction_accuracy_wilson_lower_95")
    brier_skill = scored.get("brier_skill_score")
    log_skill = scored.get("log_loss_skill_score")
    direction_skill = scored.get("direction_skill_vs_majority")
    sample_part = 20 * _clamp01(samples / 60.0)
    # Proper scoring rules receive the largest weight because the auxiliary
    # model's job is to produce cut/hold/hike probabilities, not merely an argmax class.
    brier_part = 30 * _clamp01((brier_skill or 0.0) / 0.20)
    log_part = 25 * _clamp01((log_skill or 0.0) / 0.20)
    wilson_part = 15 * _clamp01(((wilson or 0.0) - 0.45) / 0.15)
    direction_part = 10 * _clamp01((direction_skill or 0.0) / 0.10)
    return round(sample_part + brier_part + log_part + wilson_part + direction_part)


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
    log_skill = scored.get("log_loss_skill_score")

    # Two separate questions are audited: probability quality and hard action
    # classification.  A calibrated probability model can add information even
    # when its argmax class does not beat an imbalanced hold-majority rule.
    probability_passed = (
        samples >= 60
        and skill is not None and skill >= 0.10
        and log_skill is not None and log_skill >= 0.05
    )
    action_classification_passed = (
        samples >= 60 and accuracy is not None and accuracy >= 0.60
        and accuracy_lb is not None and accuracy_lb > 0.50
        and direction_skill is not None and direction_skill > 0.0
    )

    # This gate validates the release-lag reconstruction.  It is deliberately
    # separate from the stronger live-vintage gate below; no field claims that
    # revised macro history is a true ALFRED vintage.
    reconstructed_passed = probability_passed and action_classification_passed
    # Candidate means proper-score probability OOS passed while hard direction
    # classification has not established an edge over the hold-majority rule.
    candidate = probability_passed and not action_classification_passed

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
        "validation_method": "release_lagged_policy_regime_fomc_base_rate_calibrated",
        "release_lag_backtest": True,
        "probability_calibration": "past_only_fomc_action_base_rate",
        "real_time_vintage": live_vintage_passed,
        "model_validation_score": validation_score,
        "direction_accuracy": scored.get("direction_accuracy"),
        "direction_accuracy_wilson_lower_95": accuracy_lb,
        "majority_class_accuracy": scored.get("majority_class_accuracy"),
        "direction_skill_vs_majority": direction_skill,
        "mean_brier": scored.get("mean_brier"),
        "mean_log_loss": scored.get("mean_log_loss"),
        "benchmark_log_loss": scored.get("benchmark_log_loss"),
        "log_loss_skill_score": scored.get("log_loss_skill_score"),
        "benchmark_brier": scored.get("benchmark_brier"),
        "brier_skill_score": scored.get("brier_skill_score"),
        "class_frequency": scored.get("class_frequency"),
        "direction_diagnostics": {
            "reason_code": (
                "ARGMAX_EQUALS_MAJORITY_BASELINE"
                if direction_skill is not None and abs(direction_skill) < 1e-12
                else ("ARGMAX_BELOW_MAJORITY_BASELINE" if direction_skill is not None and direction_skill < 0 else "ARGMAX_ABOVE_MAJORITY_BASELINE")
            ),
            "explanation": (
                "모델의 argmax 방향 정확도가 최빈 클래스(대부분 동결) 기준모형과 동일해 방향분류의 추가 우위가 아직 확인되지 않았습니다."
                if direction_skill is not None and abs(direction_skill) < 1e-12
                else "방향분류 우위는 전체 정확도뿐 아니라 클래스 불균형을 고려한 기준모형 대비 개선으로 판정합니다."
            ),
            "confusion_matrix": scored.get("confusion_matrix"),
            "predicted_class_frequency": scored.get("predicted_class_frequency"),
            "actual_class_frequency": scored.get("class_frequency"),
            "recall_by_class": scored.get("recall_by_class"),
            "precision_by_class": scored.get("precision_by_class"),
            "balanced_accuracy": scored.get("balanced_accuracy"),
            "non_hold_recall": scored.get("non_hold_recall"),
            "objective_next_targets": {
                "direction_skill_vs_majority_min_exclusive": 0.0,
                "balanced_accuracy_min": 0.55,
                "non_hold_recall_min": 0.45,
                "direction_accuracy_wilson_lower_95_min_exclusive": 0.50,
            },
            "safe_improvement_paths": [
                "확률 calibration은 proper scoring rule 기준으로 유지",
                "동결 편향을 줄이는 후보 threshold/decision rule은 과거시점 walk-forward에서만 선택",
                "cut/hike 희소 클래스 성능은 balanced accuracy와 non-hold recall로 별도 검증",
                "시장 내재확률은 대표값으로 유지하고 자체모델은 검증된 보조 신호로만 사용",
            ],
            "score_inflation_forbidden": True,
        },
        "probability_quality_gate": {
            "passed": probability_passed,
            "level": "확률예측 OOS 통과" if probability_passed else "확률예측 OOS 미통과",
            "requirements": {
                "historical_reconstructed_rows_min": 60,
                "brier_skill_score_min": 0.10,
                "log_loss_skill_score_min": 0.05,
            },
            "observed": {
                "historical_reconstructed_rows": samples,
                "brier_skill_score": skill,
                "log_loss_skill_score": log_skill,
                "mean_brier": scored.get("mean_brier"),
                "benchmark_brier": scored.get("benchmark_brier"),
                "mean_log_loss": scored.get("mean_log_loss"),
                "benchmark_log_loss": scored.get("benchmark_log_loss"),
            },
        },
        "action_classification_gate": {
            "passed": action_classification_passed,
            "level": "방향분류 우위 확인" if action_classification_passed else "방향분류 기준모형 우위 미확인",
            "requirements": {
                "direction_accuracy_min": 0.60,
                "direction_accuracy_wilson_lower_95_min_exclusive": 0.50,
                "direction_skill_vs_majority_min_exclusive": 0.0,
            },
            "observed": {
                "direction_accuracy": accuracy,
                "direction_accuracy_wilson_lower_95": accuracy_lb,
                "majority_class_accuracy": scored.get("majority_class_accuracy"),
                "direction_skill_vs_majority": direction_skill,
            },
        },
        "quality_gate": {
            "passed": reconstructed_passed,
            "candidate": candidate,
            "level": "확률+방향 OOS 통과" if reconstructed_passed else ("확률 OOS 통과·방향분류 우위 미확인" if candidate else "검증 미통과"),
            "requirements": {
                "historical_reconstructed_rows_min": 60,
                "direction_accuracy_min": 0.60,
                "direction_accuracy_wilson_lower_95_min_exclusive": 0.50,
                "direction_skill_vs_majority_min_exclusive": 0.0,
                "brier_skill_score_min": 0.10,
                "log_loss_skill_score_min": 0.05,
                "release_lag_backtest_required": True,
            },
            "observed": {
                "historical_reconstructed_rows": samples,
                "direction_accuracy": accuracy,
                "direction_accuracy_wilson_lower_95": accuracy_lb,
                "majority_class_accuracy": scored.get("majority_class_accuracy"),
                "direction_skill_vs_majority": direction_skill,
                "brier_skill_score": skill,
                "log_loss_skill_score": log_skill,
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
