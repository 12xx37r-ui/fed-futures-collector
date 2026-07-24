from __future__ import annotations

import json
import math
from pathlib import Path

LABELS = ("cut", "hold", "hike")


def brier(prob: dict, actual: str) -> float:
    return sum(((prob.get(k, 0) / 100) - (1 if k == actual else 0)) ** 2 for k in LABELS)


def log_loss(prob: dict, actual: str) -> float:
    p = max(1e-9, prob.get(actual, 0) / 100)
    return -math.log(p)


def main() -> None:
    p = Path("public/data/history.json")
    history = json.loads(p.read_text(encoding="utf-8")) if p.exists() else []
    rows = [x for x in history if x.get("actual_direction") in LABELS]
    result = {
        "labeled_rows": len(rows),
        "direction_accuracy": None,
        "mean_brier": None,
        "mean_log_loss": None,
    }
    if rows:
        correct = 0
        briers, losses = [], []
        for row in rows:
            prob = row["probabilities"]
            prediction = max(prob, key=prob.get)
            actual = row["actual_direction"]
            correct += prediction == actual
            briers.append(brier(prob, actual))
            losses.append(log_loss(prob, actual))
        result.update({
            "direction_accuracy": correct / len(rows),
            "mean_brier": sum(briers) / len(briers),
            "mean_log_loss": sum(losses) / len(losses),
        })
    Path("public/data/backtest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
