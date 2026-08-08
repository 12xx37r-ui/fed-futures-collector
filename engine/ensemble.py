from __future__ import annotations
import math


def softmax_three(score: float) -> dict[str, float]:
    """Legacy fixed-prior mapping retained for compatibility/tests."""
    logits = {
        "cut": 1.6 * score,
        "hold": 0.7 - 0.5 * abs(score),
        "hike": -1.6 * score,
    }
    exps = {k: math.exp(v) for k, v in logits.items()}
    total = sum(exps.values())
    return {k: round(v / total * 100, 2) for k, v in exps.items()}


def policy_probabilities(score: float, prior: dict[str, float] | None = None) -> dict[str, float]:
    """Base-rate calibrated policy-action probabilities.

    The previous auxiliary model assumed roughly 50% probability of a hold
    whenever the score was neutral.  Historically, however, most scheduled
    FOMC meetings leave the target range unchanged.  That misspecified prior
    created too many false hike/cut calls immediately after a policy move and
    hurt both directional skill and Brier calibration.

    This mapper keeps the same signed macro/policy score but anchors the three
    logits to *past-only* action frequencies.  In backtests the prior is updated
    sequentially after each realized meeting, so no future class frequency is
    used.  For the live forecast the prior comes from already completed
    historical meetings.  Strong macro/market scores can still overcome the
    hold prior during genuine hiking/cutting cycles.
    """
    default = {"cut": 0.15, "hold": 0.70, "hike": 0.15}
    src = prior or default
    clean: dict[str, float] = {}
    for key in ("cut", "hold", "hike"):
        try:
            value = float(src.get(key, default[key]))
        except (TypeError, ValueError, AttributeError):
            value = default[key]
        clean[key] = max(1e-6, value)
    total_prior = sum(clean.values()) or 1.0
    clean = {k: v / total_prior for k, v in clean.items()}

    # A stronger signed tilt than the legacy mapper lets decisive market/macro
    # regimes beat the high hold base-rate, while the abs(score) term gradually
    # lowers hold odds as evidence becomes stronger.
    action_tilt = 2.0
    hold_penalty = 0.50
    logits = {
        "cut": math.log(clean["cut"]) + action_tilt * score,
        "hold": math.log(clean["hold"]) - hold_penalty * abs(score),
        "hike": math.log(clean["hike"]) - action_tilt * score,
    }
    peak = max(logits.values())
    exps = {k: math.exp(v - peak) for k, v in logits.items()}
    denom = sum(exps.values()) or 1.0
    return {k: round(v / denom * 100, 2) for k, v in exps.items()}


def combine(features: dict[str, float], weights: dict[str, float]) -> tuple[float, dict[str, float]]:
    used = {k: weights.get(k, 0.0) for k in features}
    total = sum(used.values()) or 1.0
    normalized = {k: v / total for k, v in used.items()}
    score = sum(features[k] * normalized[k] for k in features)
    return score, normalized
