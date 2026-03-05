from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass(slots=True)
class EwmaTracker:
    alpha: float = 0.2
    mean: float = 0.0
    variance: float = 0.0
    initialized: bool = False
    count: int = 0
    frozen: bool = False
    min_samples: int = 1
    quarantine_z: float = 6.0

    def update(self, value: float) -> float:
        if not self.initialized:
            self.mean = value
            self.variance = 1e-6
            self.initialized = True
            self.count = 1
            return 0.0

        score = self.zscore(value) if self.count >= self.min_samples else 0.0
        if self.frozen or (self.count >= max(self.min_samples, 5) and abs(score) >= self.quarantine_z):
            return score
        delta = value - self.mean
        self.mean = self.alpha * value + (1.0 - self.alpha) * self.mean
        self.variance = self.alpha * (delta**2) + (1.0 - self.alpha) * self.variance
        self.count += 1
        return score

    def zscore(self, value: float) -> float:
        std = math.sqrt(max(self.variance, 1e-6))
        return (value - self.mean) / std


@dataclass(slots=True)
class ToolDistributionTracker:
    alpha: float = 0.2
    baseline: dict[str, float] = field(default_factory=dict)

    def update(self, observed: dict[str, float]) -> float:
        total = sum(observed.values())
        observed = {k: v / total for k, v in observed.items()} if total else {}
        if not self.baseline:
            self.baseline = dict(observed)
            return 0.0
        keys = set(self.baseline) | set(observed)
        distance = sum(abs(observed.get(k, 0.0) - self.baseline.get(k, 0.0)) for k in keys)
        for key in keys:
            current = observed.get(key, 0.0)
            previous = self.baseline.get(key, 0.0)
            self.baseline[key] = self.alpha * current + (1.0 - self.alpha) * previous

        # L1 distance normalized to [0,1]
        return min(1.0, distance / 2.0)
