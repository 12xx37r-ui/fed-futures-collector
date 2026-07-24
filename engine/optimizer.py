from __future__ import annotations

import json
from pathlib import Path
import numpy as np
from sklearn.linear_model import LogisticRegression
from config import BASE_WEIGHTS, MIN_SNAPSHOTS_FOR_OPTIMIZATION

FEATURES = ["market", "inflation", "employment", "growth", "financial", "fed_text"]


def optimized_weights(history_path: str = "public/data/history.json") -> dict:
    p = Path(history_path)
    if not p.exists():
        return {"active": False, "reason": "no history", "weights": BASE_WEIGHTS["next_meeting"]}
    history = json.loads(p.read_text(encoding="utf-8"))
    rows = [x for x in history if x.get("actual_direction") in ("cut", "hold", "hike")]
    if len(rows) < MIN_SNAPSHOTS_FOR_OPTIMIZATION:
        return {
            "active": False,
            "reason": f"need {MIN_SNAPSHOTS_FOR_OPTIMIZATION} labeled snapshots; have {len(rows)}",
            "weights": BASE_WEIGHTS["next_meeting"],
        }

    X = np.array([[float(r["features"].get(k, 0)) for k in FEATURES] for r in rows])
    y_map = {"cut": 0, "hold": 1, "hike": 2}
    y = np.array([y_map[r["actual_direction"]] for r in rows])
    model = LogisticRegression(max_iter=300, multi_class="multinomial").fit(X, y)
    importance = np.mean(np.abs(model.coef_), axis=0)
    if importance.sum() == 0:
        return {"active": False, "reason": "zero coefficients", "weights": BASE_WEIGHTS["next_meeting"]}
    weights = {k: float(v / importance.sum()) for k, v in zip(FEATURES, importance)}
    return {"active": True, "reason": "trained on labeled history", "weights": weights}
