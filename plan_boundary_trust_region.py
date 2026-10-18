"""Plan-boundary-aware trust-region search for PostgreSQL cost parameters.

The experience table is a weak global directional prior.  Local magnitude is
learned only from observations on opposite sides of an exact-plan boundary;
same-plan points are a plateau, not samples of a smooth gradient.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import copy
import math
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


@dataclass(frozen=True)
class ParameterRange:
    min_value: float
    max_value: float
    scale: str = "log"

    def __post_init__(self) -> None:
        if not math.isfinite(self.min_value) or not math.isfinite(self.max_value):
            raise ValueError("parameter bounds must be finite")
        if self.max_value <= self.min_value:
            raise ValueError("parameter max_value must exceed min_value")
        if self.scale not in {"log", "linear"}:
            raise ValueError("parameter scale must be 'log' or 'linear'")
        if self.scale == "log" and self.min_value <= 0.0:
            raise ValueError("log-scaled parameter bounds must be positive")


@dataclass(frozen=True)
class DirectionalPrior:
    """Weak global sign/confidence for increasing one relative parameter unit."""

    resource_signs: Mapping[str, float]
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.confidence) or not 0.0 <= self.confidence <= 1.0:
            raise ValueError("prior confidence must be in [0, 1]")
        for value in self.resource_signs.values():
            if not math.isfinite(float(value)) or float(value) not in {-1.0, 0.0, 1.0}:
                raise ValueError("resource prior values must be -1, 0, or 1")


@dataclass(frozen=True)
class PlanObservation:
    distance: float
    params: Mapping[str, float]
    fingerprint: str
    resources: Mapping[str, float] = field(default_factory=dict)
    error_bounds: Mapping[str, float] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not math.isfinite(self.distance) or self.distance < 0.0:
            raise ValueError("observation distance must be finite and non-negative")
        if not self.fingerprint:
            raise ValueError("an exact plan fingerprint is required")


@dataclass(frozen=True)
class BoundaryBracket:
    plateau: PlanObservation
    crossed: PlanObservation

    def __post_init__(self) -> None:
        if self.plateau.fingerprint == self.crossed.fingerprint:
            raise ValueError("boundary bracket endpoints must have different plans")
        if self.plateau.distance >= self.crossed.distance:
            raise ValueError("boundary bracket must be ordered by distance")

    @property
    def width(self) -> float:
        return self.crossed.distance - self.plateau.distance


@dataclass(frozen=True)
class RealCandidateDecision:
    accepted: bool
    reason: str
    hard_gate_passed: bool
    qps_improved: bool
    target_improved: bool
    non_target_below_limit: bool


@dataclass(frozen=True)
class RoundState:
    incumbent: Mapping[str, Any]
    ranges: Mapping[str, ParameterRange]
    accepted_shrink_count: int = 0


@dataclass(frozen=True)
class RoundOutcome:
    status: str
    state: RoundState
    selected_candidate: Optional[Mapping[str, Any]]


def normalize_value(value: float, bounds: ParameterRange) -> float:
    value = float(value)
    if not math.isfinite(value) or not bounds.min_value <= value <= bounds.max_value:
        raise ValueError("value is outside its parameter range")
    if bounds.scale == "log":
        lo, hi, current = map(math.log, (bounds.min_value, bounds.max_value, value))
    else:
        lo, hi, current = bounds.min_value, bounds.max_value, value
    return (current - lo) / (hi - lo)


def denormalize_value(coordinate: float, bounds: ParameterRange) -> float:
    coordinate = float(coordinate)
    if not math.isfinite(coordinate) or not 0.0 <= coordinate <= 1.0:
        raise ValueError("normalized coordinate must be in [0, 1]")
    if bounds.scale == "log":
        return math.exp(math.log(bounds.min_value) + coordinate * math.log(
            bounds.max_value / bounds.min_value
        ))
    return bounds.min_value + coordinate * (bounds.max_value - bounds.min_value)


def normalized_direction(
    priors: Mapping[str, DirectionalPrior],
    *,
    target_resource: str,
    active_params: Optional[Iterable[str]] = None,
) -> Dict[str, float]:
    """Build a unit ray that follows priors expected to reduce the target."""

    active = set(active_params) if active_params is not None else set(priors)
    raw: Dict[str, float] = {}
    for name in sorted(active):
        prior = priors.get(name)
        if prior is None:
            continue
        resource_sign = float(prior.resource_signs.get(target_resource, 0.0))
        # If increasing p raises the target resource, search downward, and vice versa.
        raw[name] = -resource_sign * prior.confidence
    norm = math.sqrt(sum(value * value for value in raw.values()))
    if norm <= 0.0:
        raise ValueError(f"no directional prior for target resource {target_resource!r}")
    return {name: value / norm for name, value in raw.items() if value != 0.0}


def maximum_ray_distance(
    incumbent: Mapping[str, float],
    ranges: Mapping[str, ParameterRange],
    direction: Mapping[str, float],
) -> float:
    limits: List[float] = []
    for name, component in direction.items():
        if component == 0.0:
            continue
        coordinate = normalize_value(float(incumbent[name]), ranges[name])
        room = (1.0 - coordinate) if component > 0.0 else coordinate
        limits.append(room / abs(component))
    if not limits:
        raise ValueError("direction has no non-zero components")
    return max(0.0, min(limits))


def point_on_ray(
    incumbent: Mapping[str, float],
    ranges: Mapping[str, ParameterRange],
    direction: Mapping[str, float],
    distance: float,
) -> Dict[str, float]:
    if distance < 0.0 or not math.isfinite(distance):
        raise ValueError("distance must be finite and non-negative")
    maximum = maximum_ray_distance(incumbent, ranges, direction)
    if distance > maximum + 1e-12:
        raise ValueError("distance exceeds the legal ray")
    result = {name: float(value) for name, value in incumbent.items()}
    for name, component in direction.items():
        coordinate = normalize_value(float(incumbent[name]), ranges[name])
        next_coordinate = min(1.0, max(0.0, coordinate + distance * component))
        result[name] = denormalize_value(next_coordinate, ranges[name])
    return result


def expanding_radii(
    max_distance: float,
    *,
    initial_radius: float = 1.0 / 64.0,
    multiplier: float = 2.0,
) -> List[float]:
    if max_distance <= 0.0:
        return []
    if initial_radius <= 0.0 or multiplier <= 1.0:
        raise ValueError("initial_radius must be positive and multiplier must exceed one")
    result: List[float] = []
    radius = min(initial_radius, max_distance)
    while radius < max_distance - 1e-12:
        result.append(radius)
        radius *= multiplier
    result.append(max_distance)
    return result


def nearest_plan_boundary(observations: Sequence[PlanObservation]) -> Optional[BoundaryBracket]:
    if not observations:
        return None
    ordered = sorted(observations, key=lambda item: item.distance)
    incumbent_fingerprint = ordered[0].fingerprint
    plateau = ordered[0]
    for observation in ordered[1:]:
        if observation.fingerprint != incumbent_fingerprint:
            return BoundaryBracket(plateau=plateau, crossed=observation)
        plateau = observation
    return None


def refine_boundary(
    bracket: BoundaryBracket,
    observe: Callable[[float], PlanObservation],
    *,
    iterations: int = 6,
) -> Tuple[BoundaryBracket, List[PlanObservation]]:
    if iterations < 0:
        raise ValueError("iterations must be non-negative")
    low, high = bracket.plateau, bracket.crossed
    added: List[PlanObservation] = []
    for _ in range(iterations):
        midpoint = (low.distance + high.distance) / 2.0
        observation = observe(midpoint)
        if abs(observation.distance - midpoint) > 1e-12:
            raise ValueError("observer returned a different distance")
        added.append(observation)
        if observation.fingerprint == low.fingerprint:
            low = observation
        else:
            high = observation
    return BoundaryBracket(plateau=low, crossed=high), added


def classify_transition(
    before: PlanObservation,
    after: PlanObservation,
    *,
    resources: Iterable[str] = ("cpu", "memory", "io"),
) -> str:
    credible_change = False
    for resource in resources:
        if resource not in before.resources or resource not in after.resources:
            continue
        delta = abs(float(after.resources[resource]) - float(before.resources[resource]))
        error = abs(float(before.error_bounds.get(resource, 0.0))) + abs(
            float(after.error_bounds.get(resource, 0.0))
        )
        credible_change = credible_change or delta > error
    if before.fingerprint == after.fingerprint:
        return "same_plan_drift_remeasure" if credible_change else "plateau_expand_radius"
    return "credible_plan_boundary" if credible_change else "plan_boundary_needs_calibration"


def local_secant(
    before: PlanObservation,
    after: PlanObservation,
    resource: str,
) -> Optional[float]:
    if before.fingerprint == after.fingerprint or after.distance == before.distance:
        return None
    if resource not in before.resources or resource not in after.resources:
        return None
    delta = float(after.resources[resource]) - float(before.resources[resource])
    error = abs(float(before.error_bounds.get(resource, 0.0))) + abs(
        float(after.error_bounds.get(resource, 0.0))
    )
    if abs(delta) <= error:
        return None
    return delta / (after.distance - before.distance)


def select_cross_cluster_candidates(
    candidates: Sequence[Mapping[str, Any]],
    *,
    incumbent_fingerprint: str,
    limit: int = 5,
) -> List[Mapping[str, Any]]:
    if limit <= 0:
        return []
    selected: List[Mapping[str, Any]] = []
    seen = {incumbent_fingerprint}
    for candidate in candidates:
        fingerprint = str(candidate.get("workload_plan_fingerprint") or "")
        if not fingerprint or fingerprint in seen:
            continue
        seen.add(fingerprint)
        selected.append(candidate)
        if len(selected) == limit:
            break
    return selected


def decide_real_candidate(
    *,
    incumbent_qps: float,
    incumbent_utilization: Mapping[str, float],
    candidate_qps: float,
    candidate_utilization: Mapping[str, float],
    target_resource: str,
    hard_gate_passed: bool,
    non_target_limit: float = 80.0,
) -> RealCandidateDecision:
    qps_improved = candidate_qps > incumbent_qps
    target_improved = (
        target_resource in incumbent_utilization
        and target_resource in candidate_utilization
        and float(candidate_utilization[target_resource])
        < float(incumbent_utilization[target_resource])
    )
    non_target_below_limit = all(
        float(value) < non_target_limit
        for name, value in candidate_utilization.items()
        if name != target_resource
    )
    accepted = bool(
        hard_gate_passed and qps_improved and target_improved and non_target_below_limit
    )
    if not hard_gate_passed:
        reason = "hard_gate_failed"
    elif not non_target_below_limit:
        reason = "non_target_resource_at_or_above_limit"
    elif not qps_improved:
        reason = "db_qps_not_strictly_higher"
    elif not target_improved:
        reason = "target_resource_not_strictly_lower"
    else:
        reason = "accepted"
    return RealCandidateDecision(
        accepted=accepted,
        reason=reason,
        hard_gate_passed=hard_gate_passed,
        qps_improved=qps_improved,
        target_improved=target_improved,
        non_target_below_limit=non_target_below_limit,
    )


def finish_round(
    state: RoundState,
    evaluated_candidates: Sequence[Mapping[str, Any]],
    decisions: Sequence[RealCandidateDecision],
    *,
    shrink: Optional[
        Callable[[Mapping[str, ParameterRange], Mapping[str, Any]], Mapping[str, ParameterRange]]
    ] = None,
) -> RoundOutcome:
    if len(evaluated_candidates) != len(decisions):
        raise ValueError("candidates and decisions must have equal length")
    for candidate, decision in zip(evaluated_candidates, decisions):
        if not decision.accepted:
            continue
        if shrink is None:
            raise ValueError("accepted round requires an explicit shrink function")
        next_ranges = shrink(state.ranges, candidate)
        return RoundOutcome(
            status="accepted_and_shrunk",
            state=RoundState(
                incumbent=copy.deepcopy(candidate),
                ranges=copy.deepcopy(next_ranges),
                accepted_shrink_count=state.accepted_shrink_count + 1,
            ),
            selected_candidate=copy.deepcopy(candidate),
        )
    # Rejection is deliberately an identity transition for incumbent and ranges.
    return RoundOutcome(
        status="round_rejected/no_feasible_candidate",
        state=RoundState(
            incumbent=copy.deepcopy(state.incumbent),
            ranges=copy.deepcopy(state.ranges),
            accepted_shrink_count=state.accepted_shrink_count,
        ),
        selected_candidate=None,
    )
