from __future__ import annotations
import math


def softmax_three(score: float) -> dict[str, float]:
    logits = {
        "cut": 1.6 * score,
        "hold": 0.7 - 0.5 * abs(score),
        "hike": -1.6 * score,
    }
    exps = {k: math.exp(v) for k, v in logits.items()}
    total = sum(exps.values())
    return {k: round(v / total * 100, 2) for k, v in exps.items()}


def combine(features: dict[str, float], weights: dict[str, float]) -> tuple[float, dict[str, float]]:
    used = {k: weights.get(k, 0.0) for k in features}
    total = sum(used.values()) or 1.0
    normalized = {k: v / total for k, v in used.items()}
    score = sum(features[k] * normalized[k] for k in features)
    return score, normalized
