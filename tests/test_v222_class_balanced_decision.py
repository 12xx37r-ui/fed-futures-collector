from backtest.evaluate import _chronological_decision_rule_validation


def test_chronological_decision_rule_does_not_change_probabilities():
    rows = []
    # deterministic synthetic chronology with enough non-hold examples
    for i in range(90):
        if i % 10 < 2:
            actual = 'cut'
            probs = {'cut': 34.0, 'hold': 50.0, 'hike': 16.0}
        elif i % 10 < 4:
            actual = 'hike'
            probs = {'cut': 16.0, 'hold': 50.0, 'hike': 34.0}
        else:
            actual = 'hold'
            probs = {'cut': 15.0, 'hold': 70.0, 'hike': 15.0}
        rows.append({'actual_direction': actual, 'probabilities': probs})
    out = _chronological_decision_rule_validation(rows)
    assert out['probabilities_changed'] is False
    assert out['selected_hold_ratio_threshold'] >= 0.30
    assert out['validation']['samples'] >= 24
