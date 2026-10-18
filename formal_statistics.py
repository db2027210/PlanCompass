"""Deterministic replay summaries required by the frozen measurement protocol."""

from __future__ import annotations

from dataclasses import dataclass
import math
import random
import statistics
from typing import Any, Sequence


def _values(values: Sequence[float]) -> tuple[float, ...]:
    parsed = tuple(float(value) for value in values)
    if not parsed or any(not math.isfinite(value) for value in parsed):
        raise ValueError("replay values must be a non-empty finite sequence")
    return parsed


def _percentile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


@dataclass(frozen=True)
class ReplaySummary:
    values: tuple[float, ...]
    mean: float
    sample_std: float
    bootstrap_95_ci_lower: float
    bootstrap_95_ci_upper: float
    bootstrap_seed: int
    bootstrap_resamples: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "values": list(self.values),
            "mean": self.mean,
            "sample_std": self.sample_std,
            "bootstrap_95_ci": [self.bootstrap_95_ci_lower, self.bootstrap_95_ci_upper],
            "bootstrap_seed": self.bootstrap_seed,
            "bootstrap_resamples": self.bootstrap_resamples,
        }


def summarize_replays(
    values: Sequence[float],
    *,
    seed: int,
    resamples: int = 10_000,
) -> ReplaySummary:
    parsed = _values(values)
    if len(parsed) != 3:
        raise ValueError("the frozen protocol requires exactly three independent replays")
    if resamples <= 0:
        raise ValueError("bootstrap resamples must be positive")
    rng = random.Random(int(seed))
    means = [
        statistics.fmean(rng.choice(parsed) for _ in range(len(parsed)))
        for _ in range(int(resamples))
    ]
    return ReplaySummary(
        values=parsed,
        mean=statistics.fmean(parsed),
        sample_std=statistics.stdev(parsed),
        bootstrap_95_ci_lower=_percentile(means, 0.025),
        bootstrap_95_ci_upper=_percentile(means, 0.975),
        bootstrap_seed=int(seed),
        bootstrap_resamples=int(resamples),
    )
