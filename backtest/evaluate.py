from __future__ import annotations

import json
import math
from datetime import date, timedelta
from pathlib import Path

from engine.fomc_calendar import parse_fomc_dates
from .reconstruct import reconstruct

LABELS = ("cut", "hold", "hike")


def brier(prob: dict, actual: str) -> float:
    return sum(((prob.get(k, 0) / 100) - (1 if k == actual else 0)) ** 2 for k in LABELS)


def log_loss(prob: dict, actual: str) -> float:
    p = max(1e-9, prob.get(actual, 0) / 100)
    return -math.log(p)


def _auto_label(history: list[dict], raw: dict) -> int:
    series = ((raw.get("fred") or {}).get("effr_fred") or {}).get("observations") or []
    points = []
    for row in series:
        try:
            points.append((date.fromisoformat(row["date"]), float(row["value"])))
        except (KeyError, TypeError, ValueError):
            continue
    points.sort()
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
        pre = [v for d, v in points if meeting - timedelta(days=7) <= d < meeting]
        post = [v for d, v in points if meeting < d <= meeting + timedelta(days=7)]
        if not pre or not post:
            continue
        change = post[-1] - pre[-1]
        row["actual_direction"] = "hike" if change > 0.125 else "cut" if change < -0.125 else "hold"
        row["actual_change_bps"] = round(change * 100, 2)
        row["label_source"] = "FRED_DFF_pre_post_window"
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


def main() -> None:
    history_path = Path("public/data/history.json")
    raw_path = Path("public/data/raw.json")
    history = json.loads(history_path.read_text(encoding="utf-8")) if history_path.exists() else []
    raw = json.loads(raw_path.read_text(encoding="utf-8")) if raw_path.exists() else {}
    newly_labeled = _auto_label(history, raw)
    history_path.write_text(json.dumps(history[-5000:], ensure_ascii=False, indent=2), encoding="utf-8")

    live_rows = [x for x in history if x.get("actual_direction") in LABELS and isinstance(x.get("probabilities"), dict)]
    calendar_html = ((raw.get("fed") or {}).get("fomc_calendar") or {}).get("text", "")
    historical_rows = reconstruct(raw, parse_fomc_dates(calendar_html))
    scored = _score_rows(historical_rows)

    samples = len(historical_rows)
    accuracy = scored.get("direction_accuracy")
    skill = scored.get("brier_skill_score")
    accuracy_lb = scored.get("direction_accuracy_wilson_lower_95")
    direction_skill = scored.get("direction_skill_vs_majority")
    real_time_vintage = False
    passed = (
        samples >= 60 and accuracy is not None and accuracy >= 0.55
        and accuracy_lb is not None and accuracy_lb > 0.50
        and direction_skill is not None and direction_skill > 0
        and skill is not None and skill >= 0.10 and real_time_vintage
    )
    candidate = (
        not passed and samples >= 30 and skill is not None and skill >= 0.05
        and direction_skill is not None and direction_skill > 0
    )

    result = {
        "labeled_rows": len(live_rows),
        "newly_labeled_rows": newly_labeled,
        "historical_reconstructed_rows": samples,
        "validation_method": "release_lagged_reconstructed_fomc_walk_forward",
        "real_time_vintage": real_time_vintage,
        "release_lag_backtest": True,
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
            "passed": passed,
            "candidate": candidate,
            "level": "실시간 독립검증 통과" if passed else ("재구성 OOS 후보" if candidate else "검증 미통과"),
            "requirements": {
                "historical_reconstructed_rows_min": 60,
                "direction_accuracy_min": 0.55,
                "direction_accuracy_wilson_lower_95_min_exclusive": 0.50,
                "direction_skill_vs_majority_min_exclusive": 0.0,
                "brier_skill_score_min": 0.10,
                "release_lag_backtest_required": True,
                "real_time_vintage_required": True,
            },
            "observed": {
                "historical_reconstructed_rows": samples,
                "direction_accuracy": accuracy,
                "direction_accuracy_wilson_lower_95": accuracy_lb,
                "majority_class_accuracy": scored.get("majority_class_accuracy"),
                "direction_skill_vs_majority": direction_skill,
                "brier_skill_score": skill,
                "release_lag_backtest": True,
                "real_time_vintage": real_time_vintage,
            },
        },
        "limitations": [
            "과거 검증은 당시 원본 빈티지가 아니라 발표시차를 적용한 최신 이력 재구성입니다.",
            "실시간 스냅숏 라벨은 별도로 계속 누적됩니다.",
        ],
    }
    Path("public/data/historical_backtest_rows.json").write_text(json.dumps(historical_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    Path("public/data/backtest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
