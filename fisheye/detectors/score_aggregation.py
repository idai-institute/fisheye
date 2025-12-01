from __future__ import annotations

from collections.abc import Iterable

from fisheye.detectors.base import DetectorSignal
from fisheye.detectors.utils import clamp


def combine_signals(
    signals: Iterable[DetectorSignal],
    weights: dict[str, float] | None = None,
) -> float:
    weights = weights or {}
    residual = 1.0

    for signal in signals:
        weight = weights.get(signal.detector_id, 1.0)
        weighted = signal.score * weight
        residual *= 1.0 - weighted

    return clamp(1.0 - residual)
