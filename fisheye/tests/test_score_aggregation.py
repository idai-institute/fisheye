from __future__ import annotations

from fisheye.detectors.base import DetectorSignal
from fisheye.detectors.score_aggregation import combine_signals


def test_weighted_noisy_or_combines_scores() -> None:
    signals = [
        DetectorSignal(detector_id="a", category="dos", score=0.5),
        DetectorSignal(detector_id="b", category="dos", score=0.4),
    ]

    score = combine_signals(signals)
    assert 0.0 <= score <= 1.0
    assert round(score, 2) == 0.7


def test_weights_affect_combined_score() -> None:
    signals = [DetectorSignal(detector_id="a", category="dos", score=0.9)]
    score = combine_signals(signals, weights={"a": 0.5})
    assert round(score, 2) == 0.45
