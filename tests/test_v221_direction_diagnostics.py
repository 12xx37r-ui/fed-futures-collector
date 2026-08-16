from backtest.evaluate import _score_rows


def test_direction_diagnostics_expose_majority_baseline_problem():
    rows=[]
    for actual in ["hold"]*7 + ["cut"]*2 + ["hike"]:
        rows.append({"actual_direction": actual, "probabilities": {"cut":10,"hold":80,"hike":10}})
    scored=_score_rows(rows)
    assert scored["majority_class_accuracy"] == 0.7
    assert scored["direction_accuracy"] == 0.7
    assert abs(scored["direction_skill_vs_majority"]) < 1e-12
    assert scored["balanced_accuracy"] < scored["direction_accuracy"]
    assert scored["non_hold_recall"] == 0.0
    assert scored["confusion_matrix"]["cut"]["hold"] == 2
