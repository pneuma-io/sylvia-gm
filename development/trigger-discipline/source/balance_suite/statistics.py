from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable


@dataclass(frozen=True, slots=True)
class ConfidenceInterval:
    estimate: float
    low: float
    high: float
    confidence: float = 0.95

    def to_dict(self) -> dict[str, float]:
        return {"estimate": self.estimate, "low": self.low, "high": self.high, "confidence": self.confidence}


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> ConfidenceInterval:
    if total <= 0:
        return ConfidenceInterval(0.0, 0.0, 1.0)
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    half = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total) / denominator
    return ConfidenceInterval(p, max(0.0, center - half), min(1.0, center + half))


def mean_interval(values: Iterable[float], z: float = 1.959963984540054) -> ConfidenceInterval:
    data = list(values)
    if not data:
        return ConfidenceInterval(0.0, 0.0, 0.0)
    mean = sum(data) / len(data)
    if len(data) == 1:
        return ConfidenceInterval(mean, mean, mean)
    variance = sum((value - mean) ** 2 for value in data) / (len(data) - 1)
    half = z * math.sqrt(variance / len(data))
    return ConfidenceInterval(mean, mean - half, mean + half)


def relative_delta(candidate: float, control: float) -> float | None:
    if control == 0:
        return None
    return (candidate - control) / abs(control)
