from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(slots=True)
class EwmaTracker:
    alpha: float = 0.2
    mean: float = 0.0
    variance: float = 0.0
    initialized: bool = False

    def update(self, value: float) -> float:
        if not self.initialized:
            self.mean = value
            self.variance = 1e-6
            self.initialized = True
            return 0.0

        delta = value - self.mean
        self.mean = self.alpha * value + (1.0 - self.alpha) * self.mean
        self.variance = self.alpha * (delta**2) + (1.0 - self.alpha) * self.variance
        return self.zscore(value)

    def zscore(self, value: float) -> float:
        std = math.sqrt(max(self.variance, 1e-6))
        return (value - self.mean) / std


@dataclass(slots=True)
class ToolDistributionTracker:
    alpha: float = 0.2
    baseline: dict[str, float] = field(default_factory=dict)

    def update(self, observed: dict[str, float]) -> float:
        keys = set(self.baseline) | set(observed)
        for key in keys:
            current = observed.get(key, 0.0)
            previous = self.baseline.get(key, current)
            self.baseline[key] = self.alpha * current + (1.0 - self.alpha) * previous

        # L1 distance normalized to [0,1]
        distance = 0.0
        for key in keys:
            distance += abs(observed.get(key, 0.0) - self.baseline.get(key, 0.0))
        return min(1.0, distance / 2.0)
