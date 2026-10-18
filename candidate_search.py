"""Candidate search flows for MySQL gradient and coordinate-grid tests."""

from __future__ import annotations

import itertools
import math
from typing import Any, Callable, Dict, Iterable, List, Mapping, Sequence, Tuple

from gradient_descent_optimizer import ParameterRange


ResourceEstimator = Callable[[Any, Dict[str, float]], Dict[str, Any]]
ScoreFunction = Callable[[Mapping[str, float]], float]


def signature(params: Mapping[str, float]) -> Tuple[Tuple[str, float], ...]:
    return tuple(sorted((str(name), round(float(value), 12)) for name, value in params.items()))


def log_grid(param_range: ParameterRange, points: int) -> List[float]:
    minimum = float(param_range.min_value)
    maximum = float(param_range.max_value)
    if minimum <= 0 or maximum <= minimum:
        raise ValueError("MySQL cost ranges must be positive and increasing")
    if points <= 1:
        return [math.sqrt(minimum * maximum)]
    ratio = maximum / minimum
    return [minimum * ratio ** (index / (points - 1)) for index in range(points)]


def evaluate_candidates(
    candidates: Iterable[Mapping[str, float]],
    estimator: ResourceEstimator,
    score_func: ScoreFunction,
    *,
    sql_set: Any = None,
    source: str,
    max_candidates: int = 0,
) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    seen = set()
    for candidate in candidates:
        params = {str(name): float(value) for name, value in candidate.items()}
        key = signature(params)
        if key in seen:
            continue
        if max_candidates > 0 and len(records) >= max_candidates:
            break
        seen.add(key)
        resource = estimator(sql_set, params)
        records.append(
            {
                "candidate_id": f"{source}_{len(records):05d}",
                "source": source,
                "params": params,
                "predicted_resource": {
                    name: float(resource[name]) for name in ("cpu", "memory", "io")
                },
                "predicted_score": float(score_func(resource)),
            }
        )
    records.sort(key=lambda row: (-float(row["predicted_score"]), signature(row["params"])))
    return records


def coordinate_grid_candidates(
    baseline: Mapping[str, float],
    ranges: Mapping[str, ParameterRange],
    *,
    grid_points: int = 15,
    combination_max_size: int = 4,
    combination_values_per_param: int = 3,
) -> Iterable[Dict[str, float]]:
    yield dict(baseline)
    grids = {name: log_grid(param_range, grid_points) for name, param_range in ranges.items()}
    for name, values in grids.items():
        for value in values:
            candidate = dict(baseline)
            candidate[name] = value
            yield candidate
    names = list(ranges)
    per_param_values: Dict[str, List[float]] = {}
    for name in names:
        values = grids[name]
        if combination_values_per_param == 2:
            indices = [0, len(values) - 1]
        else:
            indices = [
                round(index * (len(values) - 1) / (combination_values_per_param - 1))
                for index in range(combination_values_per_param)
            ]
        per_param_values[name] = [values[index] for index in sorted(set(indices))]
    for size in range(2, min(combination_max_size, len(names)) + 1):
        for subset in itertools.combinations(names, size):
            for values in itertools.product(*(per_param_values[name] for name in subset)):
                candidate = dict(baseline)
                candidate.update(dict(zip(subset, values)))
                yield candidate


def anchored_log_grid(
    param_range: ParameterRange,
    baseline_value: float,
    points: int,
) -> List[float]:
    """Build a positive logarithmic grid that always contains the live baseline."""
    minimum = float(param_range.min_value)
    maximum = float(param_range.max_value)
    baseline = min(max(float(baseline_value), minimum), maximum)
    if points < 2:
        raise ValueError("full-factorial grid_points must be at least 2")
    if points == 2:
        return [minimum, maximum]
    lower_count = points // 2 + 1
    upper_count = points - lower_count + 1

    def segment(left: float, right: float, count: int) -> List[float]:
        if count <= 1 or math.isclose(left, right, rel_tol=1e-15, abs_tol=1e-15):
            return [left]
        ratio = right / left
        return [left * ratio ** (index / (count - 1)) for index in range(count)]

    values = segment(minimum, baseline, lower_count)[:-1] + segment(
        baseline, maximum, upper_count
    )
    deduplicated: List[float] = []
    for value in values:
        if not deduplicated or not math.isclose(value, deduplicated[-1], rel_tol=1e-12, abs_tol=1e-15):
            deduplicated.append(value)
    return deduplicated


def full_factorial_candidates(
    baseline: Mapping[str, float],
    parameter_values: Mapping[str, Sequence[float]],
) -> Iterable[Tuple[int, Dict[str, float]]]:
    """Enumerate every point in the configured Cartesian product."""
    names = list(parameter_values)
    if not names:
        raise ValueError("full-factorial search requires at least one active parameter")
    value_lists = [list(parameter_values[name]) for name in names]
    if any(not values for values in value_lists):
        raise ValueError("full-factorial parameter value lists must not be empty")
    for candidate_index, values in enumerate(itertools.product(*value_lists)):
        candidate = {str(name): float(value) for name, value in baseline.items()}
        candidate.update({name: float(value) for name, value in zip(names, values)})
        yield candidate_index, candidate


def gradient_trace_candidates(
    result: Any,
    baseline: Mapping[str, float],
    ranges: Mapping[str, ParameterRange],
    *,
    minimum_candidates: int = 15,
) -> List[Dict[str, float]]:
    candidates: List[Dict[str, float]] = [dict(baseline), dict(result.optimized_params)]
    for row in result.history:
        for key in ("candidate_params", "params"):
            value = row.get(key)
            if isinstance(value, Mapping):
                candidates.append({name: float(number) for name, number in value.items()})
    if len({signature(item) for item in candidates}) < minimum_candidates:
        for candidate in coordinate_grid_candidates(
            baseline,
            ranges,
            grid_points=max(3, minimum_candidates),
            combination_max_size=2,
            combination_values_per_param=2,
        ):
            candidates.append(candidate)
            if len({signature(item) for item in candidates}) >= minimum_candidates:
                break
    deduplicated: List[Dict[str, float]] = []
    seen = set()
    for candidate in candidates:
        key = signature(candidate)
        if key not in seen:
            seen.add(key)
            deduplicated.append(candidate)
    return deduplicated
