import pytest

from fisheye_instant.models import Rule, anomaly_score


def test_unified_score_preserves_strong_signals_and_deduplicates_channels():
    rows = [{"channel": "injection", "score": 0.6}, {"channel": "behavior", "score": 0.5}]
    assert anomaly_score(rows)[0] == 80
    assert anomaly_score(rows + rows)[0] == 80
    assert anomaly_score([]) == (0, [])
    assert anomaly_score(rows + [{"channel": "graph", "score": 1}])[0] == 100


def test_rules_and_scores_reject_unsafe_or_ambiguous_inputs():
    for score in (float("nan"), float("inf"), -1, 2):
        with pytest.raises(ValueError):
            anomaly_score([{"channel": "bad", "score": score}])
    with pytest.raises(ValueError):
        Rule(name="Mail", action="email")
    with pytest.raises(ValueError):
        Rule(name="Mail", action="email", recipients=["a@example.org\nBcc:other@example.org"])
    with pytest.raises(ValueError):
        Rule(name="Too low", threshold=5, hysteresis=10)
