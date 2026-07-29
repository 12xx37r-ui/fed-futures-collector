from __future__ import annotations

import json
import math
from datetime import date, timedelta
from pathlib import Path

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
        # Wait for a full post-meeting observation window.
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


def main() -> None:
    history_path = Path("public/data/history.json")
    raw_path = Path("public/data/raw.json")
    history = json.loads(history_path.read_text(encoding="utf-8")) if history_path.exists() else []
    raw = json.loads(raw_path.read_text(encoding="utf-8")) if raw_path.exists() else {}
    newly_labeled = _auto_label(history, raw)
    history_path.write_text(json.dumps(history[-5000:], ensure_ascii=False, indent=2), encoding="utf-8")

    rows = [x for x in history if x.get("actual_direction") in LABELS and isinstance(x.get("probabilities"), dict)]
    result = {
        "labeled_rows": len(rows),
        "newly_labeled_rows": newly_labeled,
        "direction_accuracy": None,
        "mean_brier": None,
        "mean_log_loss": None,
        "benchmark_brier": None,
        "brier_skill_score": None,
        "quality_gate": {"passed": False, "level": "검증 축적 중"},
    }
    if rows:
        correct = 0
        briers, losses = [], []
        counts = {k: 0 for k in LABELS}
        for row in rows:
            prob = row["probabilities"]
            prediction = max(prob, key=prob.get)
            actual = row["actual_direction"]
            counts[actual] += 1
            correct += prediction == actual
            briers.append(brier(prob, actual))
            losses.append(log_loss(prob, actual))
        frequencies = {k: counts[k] / len(rows) for k in LABELS}
        benchmark = sum((frequencies[k] - (1 if k == actual else 0)) ** 2 for actual in [r["actual_direction"] for r in rows] for k in LABELS) / len(rows)
        mean_brier = sum(briers) / len(briers)
        skill = 1.0 - mean_brier / benchmark if benchmark > 0 else None
        accuracy = correct / len(rows)
        passed = len(rows) >= 40 and accuracy >= 0.50 and skill is not None and skill > 0
        candidate = len(rows) >= 20 and skill is not None and skill > 0
        result.update({
            "direction_accuracy": accuracy,
            "mean_brier": mean_brier,
            "mean_log_loss": sum(losses) / len(losses),
            "benchmark_brier": benchmark,
            "brier_skill_score": skill,
            "class_frequency": frequencies,
            "quality_gate": {
                "passed": passed,
                "candidate": candidate,
                "level": "준기관급" if passed else ("준기관급 후보" if candidate else "검증 축적 중"),
                "requirements": {"labeled_rows_min": 40, "direction_accuracy_min": 0.50, "brier_skill_score_min": 0.0},
            },
        })
    Path("public/data/backtest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
