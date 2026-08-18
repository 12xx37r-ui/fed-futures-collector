from __future__ import annotations

import json
from pathlib import Path
import numpy as np
from sklearn.linear_model import LogisticRegression
from config import BASE_WEIGHTS, MIN_SNAPSHOTS_FOR_OPTIMIZATION

FEATURES = ["policy_inertia", "market", "inflation", "employment", "growth", "financial", "fed_text"]


def _fixed_weight_action(features: dict) -> str:
    w = BASE_WEIGHTS["next_meeting"]
    score = sum(float(features.get(k, 0.0) or 0.0) * float(w.get(k, 0.0) or 0.0) for k in FEATURES)
    # Conservative neutral band: the fixed model should not manufacture
    # direction when the weighted evidence is weak.
    if score > 0.10:
        return "hike"
    if score < -0.10:
        return "cut"
    return "hold"


def optimized_weights(history_path: str = "public/data/history.json") -> dict:
    """Train optional weights, but activate them only after temporal OOS proof.

    Existing fixed weights remain the hard safety benchmark.  Once enough
    labeled snapshots exist, a multinomial logistic candidate is evaluated on
    the most recent 25% of observations (never shuffled).  It is promoted only
    when it beats the fixed-weight action baseline by at least two percentage
    points and has at least ten OOS cases.
    """
    p = Path(history_path)
    base = BASE_WEIGHTS["next_meeting"]
    if not p.exists():
        return {"active": False, "reason": "no history", "weights": base, "promotion_gate": {"passed": False}}
    history = json.loads(p.read_text(encoding="utf-8"))
    rows = [x for x in history if x.get("actual_direction") in ("cut", "hold", "hike") and isinstance(x.get("features"), dict)]
    if len(rows) < MIN_SNAPSHOTS_FOR_OPTIMIZATION:
        return {
            "active": False,
            "reason": f"need {MIN_SNAPSHOTS_FOR_OPTIMIZATION} labeled snapshots; have {len(rows)}",
            "weights": base,
            "promotion_gate": {"passed": False, "oos_samples": 0, "candidate_accuracy": None, "fixed_weight_accuracy": None},
        }

    X = np.array([[float(r["features"].get(k, 0) or 0.0) for k in FEATURES] for r in rows], dtype=float)
    y_map = {"cut": 0, "hold": 1, "hike": 2}
    y = np.array([y_map[r["actual_direction"]] for r in rows])
    split = max(30, int(len(rows) * 0.75))
    if len(rows) - split < 10 or len(set(y[:split])) < 2:
        return {"active": False, "reason": "insufficient temporal OOS/class diversity", "weights": base, "promotion_gate": {"passed": False, "oos_samples": max(0, len(rows)-split)}}

    candidate = LogisticRegression(max_iter=400).fit(X[:split], y[:split])
    pred = candidate.predict(X[split:])
    cand_acc = float(np.mean(pred == y[split:]))
    fixed_pred = np.array([y_map[_fixed_weight_action(r["features"])] for r in rows[split:]])
    fixed_acc = float(np.mean(fixed_pred == y[split:]))
    improvement = cand_acc - fixed_acc
    gate = bool((len(rows)-split) >= 10 and improvement >= 0.02)

    # Fit final candidate on all labeled history only after the OOS gate passes.
    if not gate:
        return {
            "active": False,
            "reason": "optimized weights did not beat fixed weights on temporal OOS",
            "weights": base,
            "candidate_weights": None,
            "promotion_gate": {"passed": False, "oos_samples": len(rows)-split, "candidate_accuracy": cand_acc, "fixed_weight_accuracy": fixed_acc, "accuracy_improvement": improvement, "required_improvement": 0.02},
        }

    model = LogisticRegression(max_iter=400).fit(X, y)
    importance = np.mean(np.abs(model.coef_), axis=0)
    if importance.sum() == 0:
        return {"active": False, "reason": "zero coefficients", "weights": base, "promotion_gate": {"passed": False}}
    weights = {k: float(v / importance.sum()) for k, v in zip(FEATURES, importance)}
    return {
        "active": True,
        "reason": "trained on labeled history and beat fixed weights on temporal OOS",
        "weights": weights,
        "promotion_gate": {"passed": True, "oos_samples": len(rows)-split, "candidate_accuracy": cand_acc, "fixed_weight_accuracy": fixed_acc, "accuracy_improvement": improvement, "required_improvement": 0.02},
    }
