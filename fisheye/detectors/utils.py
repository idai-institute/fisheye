from __future__ import annotations

import math
from typing import Any

from fisheye.preprocessors.utils import walk_strings


def extract_text(payload: dict[str, Any]) -> str:
    return "\n".join(walk_strings(payload))


def shannon_entropy(text: str) -> float:
    if not text:
        return 0.0
    counts: dict[str, int] = {}
    for ch in text:
        counts[ch] = counts.get(ch, 0) + 1

    total = len(text)
    entropy = 0.0
    for count in counts.values():
        prob = count / total
        entropy -= prob * math.log2(prob)
    return entropy


def clamp(value: float, minimum: float = 0.0, maximum: float = 1.0) -> float:
    return max(minimum, min(maximum, value))
