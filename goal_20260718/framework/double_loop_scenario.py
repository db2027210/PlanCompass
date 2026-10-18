"""Fresh-baseline-only resource objective and online experience model.

This module deliberately has no dependency on the legacy optimizers, historical run
artifacts, teacher coordinates, sensitivity tables, model labels, or acceptance gates.
The only training rows visible to :class:`FreshExperienceModel` are parameter vectors
and measured resource vectors produced by the current process.  Estimated and real
measurements are scored by the exact same resource-only formula.
"""

from __future__ import annotations

import copy
import math
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence, Tuple

import numpy as np


Params = Dict[str, float]
Resource = Dict[str, float]
ScenarioName = Literal["cpu", "memory", "io", "none"]
ScorePolicy = Literal["sigmoid_linear_resource"]


@dataclass(frozen=True)
class ScenarioMeasurement:
    """ScenarioMeasurement."""

    db_qps: float
    cpu: float
    memory: float
    io: float

    def as_resource(self) -> Resource:
        return {
            "db_qps": self.db_qps,
            "cpu": self.cpu,
            "memory": self.memory,
            "io": self.io,
        }


@dataclass(frozen=True)
class ScenarioDecision:
    """ScenarioDecision."""


    scenario: ScenarioName
    score_policy: ScorePolicy
    successful: bool
    qps_higher: bool
    qps_threshold_met: bool
    min_qps_gain_ratio: float
    target_lower: Optional[bool]
    target_drop_threshold_met: Optional[bool]
    min_target_drop_ratio: Optional[float]
    any_resource_higher: Optional[bool]
    qps_gain_ratio: float
    target_drop_ratio: Optional[float]
    resource_gain_ratios: Dict[str, float]
    qps_utility: float
    qps_score_contribution: float
    target_sweet_upper: Optional[float]
    target_sweet_progress: Optional[float]
    resource_bonus_progress: Dict[str, float]
    resource_bonus_contributions: Dict[str, float]
    none_cpu_memory_balance_weight: Optional[float]
    none_cpu_memory_imbalance_ratio: Optional[float]
    none_cpu_memory_imbalance_penalty: Optional[float]
    none_io_usage_weight: Optional[float]
    none_io_usage_adjustment: Optional[float]
    qps_constraint_violation: float
    target_constraint_violation: Optional[float]
    target_overshoot_violation: Optional[float]
    any_resource_constraint_violation: Optional[float]
    constraint_violation_penalty: float
    objective: float


@dataclass(frozen=True)
class OuterAcceptanceDecision:
    """OuterAcceptanceDecision."""


    scenario: ScenarioName
    accepted: bool
    reason: str
    qps_higher: bool
    target_lower: Optional[bool]
    any_resource_higher: Optional[bool]
    any_non_target_resource_higher: Optional[bool]
    qps_gain_ratio: float
    target_drop_ratio: Optional[float]
    resource_gain_ratios: Dict[str, float]


@dataclass(frozen=True)
class FinalValidationDecision:
    """FinalValidationDecision."""

    scenario: ScenarioName
    successful: bool
    reason: str
    qps_gain_ratio: float
    qps_gain_threshold_met: bool
    min_qps_gain_ratio: float
    target_drop_ratio: Optional[float]
    target_drop_in_range: Optional[bool]
    min_target_drop_ratio: Optional[float]
    max_target_drop_ratio: Optional[float]
    target_value: Optional[float]
    target_not_bottleneck: Optional[bool]
    target_in_sweet_spot: Optional[bool]
    target_sweet_lower: Optional[float]
    target_sweet_upper: Optional[float]
    any_resource_higher: Optional[bool]
    any_non_target_resource_higher: Optional[bool]
    resource_gain_ratios: Dict[str, float]


def _finite(value: Any, name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"missing or invalid {name}: {value!r}") from exc
    if not math.isfinite(result):
        raise ValueError(f"non-finite {name}: {result}")
    return result


def measurement_from_payload(resource: Mapping[str, Any], raw: Any = None) -> ScenarioMeasurement:
    """Extract the four permitted objective signals from a validation payload."""
    payload = raw[0] if isinstance(raw, tuple) and raw else raw
    payload = payload if isinstance(payload, Mapping) else {}
    nested: Mapping[str, Any] = resource
    for key in ("resource", "resources", "resource_avg", "resource_normalized"):
        if isinstance(payload.get(key), Mapping):
            nested = payload[key]
            break
    db_qps = payload.get("db_qps", nested.get("db_qps"))
    return ScenarioMeasurement(
        db_qps=_finite(db_qps, "db_qps"),
        cpu=_finite(nested.get("cpu"), "cpu"),
        memory=_finite(nested.get("memory"), "memory"),
        io=_finite(nested.get("io"), "io"),
    )


class FreshBaselineObjective:
    """Scenario objective anchored permanently to one current-run baseline."""

    RESOURCE_NAMES = ("cpu", "memory", "io")
    MIN_REQUIRED_QPS_GAIN_RATIO = 0.15
    MIN_REQUIRED_TARGET_DROP_RATIO = 0.05
    MAX_REQUIRED_TARGET_DROP_RATIO = 0.10
    DEFAULT_TARGET_SWEET_WEIGHT = 1.0
    DEFAULT_RESOURCE_BONUS_SATURATION_RATIO = 0.10
    DEFAULT_NON_TARGET_BONUS_WEIGHT = 0.05
    FAILURE_MARGIN_BALANCE_WEIGHT = 0.05
    DEFAULT_NONE_RESOURCE_BONUS_WEIGHTS = {
        "cpu": 0.05,
        "memory": 0.10,
        "io": 0.02,
    }
    DEFAULT_NONE_CPU_MEMORY_BALANCE_WEIGHT = 1.0
    DEFAULT_NONE_IO_USAGE_WEIGHT = 0.5
    DEFAULT_CONSTRAINT_VIOLATION_WEIGHT = 1.0
    DEFAULT_IO_REWARD_WEIGHT = 0.25
    SIGMOID_K = 5.0
    OVERLOAD_LINEAR = 10.0
    OVERLOAD_QUADRATIC = 30.0

    def __init__(
        self,
        scenario: ScenarioName,
        baseline: ScenarioMeasurement,
        *,
        score_policy: ScorePolicy = "sigmoid_linear_resource",
        resource_limits: Optional[Mapping[str, float]] = None,
        min_qps_gain_ratio: float = 0.15,
        min_target_drop_ratio: float = 0.05,
        max_target_drop_ratio: float = 0.10,
        target_sweet_lower: Optional[float] = None,
        target_sweet_upper: Optional[float] = None,
        target_sweet_weight: float = DEFAULT_TARGET_SWEET_WEIGHT,
        resource_bonus_saturation_ratio: float = DEFAULT_RESOURCE_BONUS_SATURATION_RATIO,
        non_target_bonus_weight: float = DEFAULT_NON_TARGET_BONUS_WEIGHT,
        none_resource_bonus_weights: Optional[Mapping[str, float]] = None,
        none_cpu_memory_balance_weight: float = DEFAULT_NONE_CPU_MEMORY_BALANCE_WEIGHT,
        none_io_usage_weight: float = DEFAULT_NONE_IO_USAGE_WEIGHT,
        constraint_violation_weight: float = DEFAULT_CONSTRAINT_VIOLATION_WEIGHT,
        io_reward_weight: float = DEFAULT_IO_REWARD_WEIGHT,
    ) -> None:
        if scenario not in {"cpu", "memory", "io", "none"}:
            raise ValueError(f"unsupported scenario: {scenario}")
        if score_policy != "sigmoid_linear_resource":
            raise ValueError(f"unsupported score policy: {score_policy}")
        if baseline.db_qps <= 0.0:
            raise ValueError("fresh baseline DB QPS must be positive")
        if float(min_qps_gain_ratio) < self.MIN_REQUIRED_QPS_GAIN_RATIO:
            raise ValueError("minimum DB QPS gain ratio cannot be lower than 0.15")
        if float(min_target_drop_ratio) < self.MIN_REQUIRED_TARGET_DROP_RATIO:
            raise ValueError("minimum target bottleneck drop ratio cannot be lower than 0.05")
        if not math.isfinite(float(max_target_drop_ratio)):
            raise ValueError("maximum target bottleneck drop ratio must be finite")
        if float(max_target_drop_ratio) > self.MAX_REQUIRED_TARGET_DROP_RATIO:
            raise ValueError("maximum target bottleneck drop ratio cannot exceed 0.10")
        if float(max_target_drop_ratio) < float(min_target_drop_ratio):
            raise ValueError("maximum target bottleneck drop ratio cannot be below the minimum")
        if scenario != "none":
            if target_sweet_upper is None or not math.isfinite(float(target_sweet_upper)):
                raise ValueError("CPU/Memory/IO objective requires a finite target sweet upper value")
            if float(target_sweet_upper) >= float(getattr(baseline, scenario)):
                raise ValueError("fresh target bottleneck must be above its physical sweet upper value")
            lowest_allowed_target = float(getattr(baseline, scenario)) * (
                1.0 - float(max_target_drop_ratio)
            )
            if lowest_allowed_target > float(target_sweet_upper) + 1e-12:
                raise ValueError(
                    "fresh target, sweet upper, and maximum target drop have no "
                    "feasible intersection"
                )
            if target_sweet_lower is not None:
                if not math.isfinite(float(target_sweet_lower)):
                    raise ValueError("target sweet lower must be finite")
                if float(target_sweet_lower) > float(target_sweet_upper):
                    raise ValueError("target sweet lower cannot exceed target sweet upper")
        for value, label in (
            (target_sweet_weight, "target sweet weight"),
            (non_target_bonus_weight, "non-target bonus weight"),
            (none_cpu_memory_balance_weight, "None CPU/Memory balance weight"),
            (none_io_usage_weight, "None IO usage weight"),
            (constraint_violation_weight, "constraint violation weight"),
            (io_reward_weight, "IO reward weight"),
        ):
            if not math.isfinite(float(value)) or float(value) < 0.0:
                raise ValueError(f"{label} must be finite and non-negative")
        if (
            not math.isfinite(float(resource_bonus_saturation_ratio))
            or float(resource_bonus_saturation_ratio) <= 0.0
        ):
            raise ValueError("resource bonus saturation ratio must be finite and positive")
        none_weights = dict(
            none_resource_bonus_weights or self.DEFAULT_NONE_RESOURCE_BONUS_WEIGHTS
        )
        if set(none_weights) != set(self.RESOURCE_NAMES):
            raise ValueError("None resource bonus weights must contain exactly cpu,memory,io")
        for name, value in none_weights.items():
            if not math.isfinite(float(value)) or float(value) < 0.0:
                raise ValueError(f"None {name} bonus weight must be finite and non-negative")
        self.scenario = scenario
        self.score_policy = score_policy
        self.baseline = baseline
        raw_limits = dict(resource_limits or {})
        if set(raw_limits) != set(self.RESOURCE_NAMES):
            raise ValueError("resource_limits must contain exactly cpu,memory,io")
        self.resource_limits = {
            name: float(raw_limits[name]) for name in self.RESOURCE_NAMES
        }
        for name, value in self.resource_limits.items():
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError(f"{name} resource limit must be finite and positive")
        self.min_qps_gain_ratio = float(min_qps_gain_ratio)
        self.min_target_drop_ratio = float(min_target_drop_ratio)
        self.max_target_drop_ratio = float(max_target_drop_ratio)
        self.target_sweet_upper = (
            float(target_sweet_upper) if target_sweet_upper is not None else None
        )
        self.target_sweet_lower = (
            float(target_sweet_lower) if target_sweet_lower is not None else None
        )
        self.target_sweet_weight = float(target_sweet_weight)
        self.resource_bonus_saturation_ratio = float(resource_bonus_saturation_ratio)
        self.non_target_bonus_weight = float(non_target_bonus_weight)
        self.none_resource_bonus_weights = {
            name: float(none_weights[name]) for name in self.RESOURCE_NAMES
        }
        self.none_cpu_memory_balance_weight = float(none_cpu_memory_balance_weight)
        self.none_io_usage_weight = float(none_io_usage_weight)
        self.constraint_violation_weight = float(constraint_violation_weight)
        self.io_reward_weight = float(io_reward_weight)

    @staticmethod
    def _ratio_delta(candidate: float, baseline: float) -> float:
        return (candidate - baseline) / max(abs(baseline), 1e-12)

    def _bonus_progress(self, gain_ratio: float) -> float:
        """Map a positive resource gain to a capped optional-bonus utility."""
        return min(
            1.0,
            max(0.0, gain_ratio) / self.resource_bonus_saturation_ratio,
        )

    @classmethod
    def sigmoid_linear_score(cls, utilization: float) -> float:
        """Exact legacy small-loop resource score requested by the user."""
        u = max(0.0, float(utilization))
        if u <= 1.0:
            sigmoid_u = 1.0 / (1.0 + math.exp(-cls.SIGMOID_K * u))
            sigmoid_one = 1.0 / (1.0 + math.exp(-cls.SIGMOID_K))
            return (sigmoid_u - 0.5) / (sigmoid_one - 0.5)
        excess = u - 1.0
        return (
            1.0
            - cls.OVERLOAD_LINEAR * excess
            - cls.OVERLOAD_QUADRATIC * excess * excess
        )

    def resource_score_details(
        self,
        measurement: ScenarioMeasurement,
    ) -> Dict[str, Any]:
        raw_utilizations = {
            name: float(getattr(measurement, name)) / self.resource_limits[name]
            for name in self.RESOURCE_NAMES
        }
        score_coordinates = dict(raw_utilizations)
        target_band_distance_ratio: Optional[float] = None
        if self.scenario != "none":
            target = self.scenario
            target_value = float(getattr(measurement, target))
            sweet_upper = float(self.target_sweet_upper or self.resource_limits[target])
            sweet_lower = float(
                self.target_sweet_lower
                if self.target_sweet_lower is not None
                else sweet_upper
            )
            if target_value < sweet_lower:
                target_band_distance_ratio = (sweet_lower - target_value) / max(
                    sweet_lower,
                    1e-12,
                )
            elif target_value > sweet_upper:
                target_band_distance_ratio = (target_value - sweet_upper) / max(
                    sweet_upper,
                    1e-12,
                )
            else:
                target_band_distance_ratio = 0.0
            # The target receives its maximum score anywhere inside the physical
            # sweet band. Moving either above or below the band uses the same
            # u>1 overload branch, so an unnecessarily large decrease is not a
            # source of reward.
            score_coordinates[target] = 1.0 + target_band_distance_ratio
        contributions = {
            name: self.sigmoid_linear_score(score_coordinates[name])
            for name in self.RESOURCE_NAMES
        }
        reward_weights = {
            name: (
                self.io_reward_weight
                if name == "io" and self.scenario != "io"
                else 1.0
            )
            for name in self.RESOURCE_NAMES
        }
        penalty_weights = {name: 1.0 for name in self.RESOURCE_NAMES}
        weighted_contributions = {
            name: (
                reward_weights[name] * max(0.0, contributions[name])
                + penalty_weights[name] * min(0.0, contributions[name])
            )
            for name in self.RESOURCE_NAMES
        }
        weight_sum = sum(reward_weights.values())
        return {
            "formula": "sigmoid_linear_sweet_band_weighted_mean_v2",
            "resource_limits": dict(self.resource_limits),
            "raw_utilizations": raw_utilizations,
            "score_coordinates": score_coordinates,
            "target_sweet_lower": self.target_sweet_lower,
            "target_sweet_upper": self.target_sweet_upper,
            "target_band_distance_ratio": target_band_distance_ratio,
            "weights": reward_weights,
            "reward_weights": reward_weights,
            "penalty_weights": penalty_weights,
            "contributions": contributions,
            "weighted_contributions": weighted_contributions,
            "weight_sum": weight_sum,
            "total": sum(weighted_contributions.values()) / weight_sum,
        }

    def optimistic_score_in_resource_box(
        self,
        lower_resource: Mapping[str, float],
        upper_resource: Mapping[str, float],
    ) -> Dict[str, Any]:
        """Maximize the separable resource score inside an error-bound box.

        This is used only by current-run candidate/replay pruning.  Non-target
        resources peak at their physical ``u=1`` limit.  The target resource
        peaks anywhere inside its sweet band.  Choosing the closest feasible
        value to those optima gives the exact optimistic score for the box.
        """
        optimistic: Dict[str, float] = {}
        for name in self.RESOURCE_NAMES:
            lower = max(0.0, float(lower_resource[name]))
            upper = max(lower, float(upper_resource[name]))
            if self.scenario != "none" and name == self.scenario:
                sweet_upper = float(
                    self.target_sweet_upper or self.resource_limits[name]
                )
                sweet_lower = float(
                    self.target_sweet_lower
                    if self.target_sweet_lower is not None
                    else sweet_upper
                )
                if upper < sweet_lower:
                    value = upper
                elif lower > sweet_upper:
                    value = lower
                else:
                    value = max(lower, sweet_lower)
            else:
                limit = self.resource_limits[name]
                value = max(lower, min(upper, limit))
            optimistic[name] = value
        measurement = ScenarioMeasurement(
            db_qps=self.baseline.db_qps,
            cpu=optimistic["cpu"],
            memory=optimistic["memory"],
            io=optimistic["io"],
        )
        details = self.resource_score_details(measurement)
        return {
            "score": float(details["total"]),
            "optimistic_resource": optimistic,
            "score_details": details,
        }

    def decide(self, measurement: ScenarioMeasurement) -> ScenarioDecision:
        qps_gain = self._ratio_delta(measurement.db_qps, self.baseline.db_qps)
        qps_margin = qps_gain - self.min_qps_gain_ratio
        shared_resource_score = float(self.resource_score_details(measurement)["total"])
        gains = {
            name: self._ratio_delta(getattr(measurement, name), getattr(self.baseline, name))
            for name in self.RESOURCE_NAMES
        }
        qps_higher = measurement.db_qps > self.baseline.db_qps
        qps_threshold_met = qps_gain >= self.min_qps_gain_ratio
        qps_constraint_violation = max(
            0.0,
            self.min_qps_gain_ratio - qps_gain,
        ) / self.min_qps_gain_ratio
        positive_gains = {name: max(0.0, gain) for name, gain in gains.items()}

        if self.scenario == "none":
            any_resource_higher = any(
                getattr(measurement, name) > getattr(self.baseline, name)
                for name in self.RESOURCE_NAMES
            )
            strongest_name = max(
                self.RESOURCE_NAMES,
                key=lambda name: (positive_gains[name], -self.RESOURCE_NAMES.index(name)),
            )
            strongest = positive_gains[strongest_name]
            bonus_contributions = {
                name: (
                    0.0
                    if name == strongest_name
                    else self.non_target_bonus_weight * positive_gains[name]
                )
                for name in self.RESOURCE_NAMES
            }
            any_resource_constraint_violation = 0.0 if any_resource_higher else 1.0
            successful = qps_threshold_met and any_resource_higher
            margin_balance_penalty = self.FAILURE_MARGIN_BALANCE_WEIGHT * abs(
                qps_margin - strongest
            )
            objective = shared_resource_score
            return ScenarioDecision(
                scenario=self.scenario,
                score_policy=self.score_policy,
                successful=successful,
                qps_higher=qps_higher,
                qps_threshold_met=qps_threshold_met,
                min_qps_gain_ratio=self.min_qps_gain_ratio,
                target_lower=None,
                target_drop_threshold_met=None,
                min_target_drop_ratio=None,
                any_resource_higher=any_resource_higher,
                qps_gain_ratio=qps_gain,
                target_drop_ratio=None,
                resource_gain_ratios=gains,
                qps_utility=qps_gain,
                qps_score_contribution=0.0,
                target_sweet_upper=None,
                target_sweet_progress=None,
                resource_bonus_progress=positive_gains,
                resource_bonus_contributions=bonus_contributions,
                none_cpu_memory_balance_weight=None,
                none_cpu_memory_imbalance_ratio=None,
                none_cpu_memory_imbalance_penalty=None,
                none_io_usage_weight=None,
                none_io_usage_adjustment=None,
                qps_constraint_violation=qps_constraint_violation,
                target_constraint_violation=None,
                target_overshoot_violation=None,
                any_resource_constraint_violation=any_resource_constraint_violation,
                constraint_violation_penalty=0.0,
                objective=objective,
            )

        target = self.scenario
        assert self.target_sweet_upper is not None
        target_drop = -gains[target]
        target_lower = getattr(measurement, target) < getattr(self.baseline, target)
        target_drop_threshold_met = target_drop >= self.min_target_drop_ratio
        target_margin = target_drop - self.min_target_drop_ratio
        target_constraint_violation = max(
            0.0,
            self.min_target_drop_ratio - target_drop,
        ) / self.min_target_drop_ratio
        target_overshoot_violation = max(
            0.0,
            target_drop - self.max_target_drop_ratio,
        ) / self.max_target_drop_ratio
        target_sweet_progress = min(
            1.0,
            max(
                0.0,
                (getattr(self.baseline, target) - getattr(measurement, target))
                / (getattr(self.baseline, target) - self.target_sweet_upper),
            ),
        )
        bonus_contributions = {
            name: (
                0.0
                if name == target
                else self.non_target_bonus_weight * positive_gains[name]
            )
            for name in self.RESOURCE_NAMES
        }
        successful = qps_threshold_met and target_drop_threshold_met
        margin_balance_penalty = self.FAILURE_MARGIN_BALANCE_WEIGHT * abs(
            qps_margin - target_margin
        )
        objective = shared_resource_score
        return ScenarioDecision(
            scenario=self.scenario,
            score_policy=self.score_policy,
            successful=successful,
            qps_higher=qps_higher,
            qps_threshold_met=qps_threshold_met,
            min_qps_gain_ratio=self.min_qps_gain_ratio,
            target_lower=target_lower,
            target_drop_threshold_met=target_drop_threshold_met,
            min_target_drop_ratio=self.min_target_drop_ratio,
            any_resource_higher=None,
            qps_gain_ratio=qps_gain,
            target_drop_ratio=target_drop,
            resource_gain_ratios=gains,
            qps_utility=qps_gain,
            qps_score_contribution=0.0,
            target_sweet_upper=self.target_sweet_upper,
            target_sweet_progress=target_sweet_progress,
            resource_bonus_progress=positive_gains,
            resource_bonus_contributions=bonus_contributions,
            none_cpu_memory_balance_weight=None,
            none_cpu_memory_imbalance_ratio=None,
            none_cpu_memory_imbalance_penalty=None,
            none_io_usage_weight=None,
            none_io_usage_adjustment=None,
            qps_constraint_violation=qps_constraint_violation,
            target_constraint_violation=target_constraint_violation,
            target_overshoot_violation=target_overshoot_violation,
            any_resource_constraint_violation=None,
            constraint_violation_penalty=0.0,
            objective=objective,
        )

    def score(self, resource: Resource, raw: Any) -> float:
        return self.decide(measurement_from_payload(resource, raw)).objective

    def outer_decide(self, measurement: ScenarioMeasurement) -> OuterAcceptanceDecision:
        """outer decide."""
        qps_gain = self._ratio_delta(measurement.db_qps, self.baseline.db_qps)
        gains = {
            name: self._ratio_delta(getattr(measurement, name), getattr(self.baseline, name))
            for name in self.RESOURCE_NAMES
        }
        qps_higher = measurement.db_qps > self.baseline.db_qps
        if self.scenario == "none":
            any_resource_higher = any(gains[name] > 0.0 for name in self.RESOURCE_NAMES)
            accepted = qps_higher and any_resource_higher
            reason = (
                "accepted_qps_up_and_any_resource_up"
                if accepted
                else "db_qps_not_higher"
                if not qps_higher
                else "no_resource_higher"
            )
            return OuterAcceptanceDecision(
                scenario=self.scenario,
                accepted=accepted,
                reason=reason,
                qps_higher=qps_higher,
                target_lower=None,
                any_resource_higher=any_resource_higher,
                any_non_target_resource_higher=None,
                qps_gain_ratio=qps_gain,
                target_drop_ratio=None,
                resource_gain_ratios=gains,
            )

        target = self.scenario
        target_lower = getattr(measurement, target) < getattr(self.baseline, target)
        any_non_target_higher = any(
            gains[name] > 0.0 for name in self.RESOURCE_NAMES if name != target
        )
        accepted = qps_higher and target_lower
        reason = (
            "accepted_db_qps_up_and_target_down"
            if accepted
            else "db_qps_and_target_direction_failed"
            if not qps_higher and not target_lower
            else "db_qps_not_higher"
            if not qps_higher
            else "target_bottleneck_not_lower"
        )
        return OuterAcceptanceDecision(
            scenario=self.scenario,
            accepted=accepted,
            reason=reason,
            qps_higher=qps_higher,
            target_lower=target_lower,
            any_resource_higher=None,
            any_non_target_resource_higher=any_non_target_higher,
            qps_gain_ratio=qps_gain,
            target_drop_ratio=-gains[target],
            resource_gain_ratios=gains,
        )

    def final_decide(self, measurement: ScenarioMeasurement) -> FinalValidationDecision:
        """final decide."""
        qps_gain = self._ratio_delta(measurement.db_qps, self.baseline.db_qps)
        qps_threshold_met = qps_gain >= self.min_qps_gain_ratio
        gains = {
            name: self._ratio_delta(getattr(measurement, name), getattr(self.baseline, name))
            for name in self.RESOURCE_NAMES
        }
        if self.scenario == "none":
            any_resource_higher = any(gains[name] > 0.0 for name in self.RESOURCE_NAMES)
            successful = qps_threshold_met and any_resource_higher
            reason = (
                "final_success"
                if successful
                else "final_qps_gain_below_threshold"
                if not qps_threshold_met
                else "final_no_resource_higher"
            )
            return FinalValidationDecision(
                scenario=self.scenario,
                successful=successful,
                reason=reason,
                qps_gain_ratio=qps_gain,
                qps_gain_threshold_met=qps_threshold_met,
                min_qps_gain_ratio=self.min_qps_gain_ratio,
                target_drop_ratio=None,
                target_drop_in_range=None,
                min_target_drop_ratio=None,
                max_target_drop_ratio=None,
                target_value=None,
                target_not_bottleneck=None,
                target_in_sweet_spot=None,
                target_sweet_lower=None,
                target_sweet_upper=None,
                any_resource_higher=any_resource_higher,
                any_non_target_resource_higher=None,
                resource_gain_ratios=gains,
            )

        target = self.scenario
        assert self.target_sweet_upper is not None
        target_value = float(getattr(measurement, target))
        target_drop_ratio = -gains[target]
        target_drop_in_range = (
            self.min_target_drop_ratio
            <= target_drop_ratio
            <= self.max_target_drop_ratio
        )
        target_not_bottleneck = target_value <= self.target_sweet_upper
        target_in_sweet_spot = (
            target_not_bottleneck
            if self.target_sweet_lower is None
            else self.target_sweet_lower <= target_value <= self.target_sweet_upper
        )
        any_non_target_higher = any(
            gains[name] > 0.0 for name in self.RESOURCE_NAMES if name != target
        )
        # Non-target rises are reported as optional evidence, never a hard gate.
        successful = qps_threshold_met and target_not_bottleneck and target_drop_in_range
        failed_reasons: List[str] = []
        if not qps_threshold_met:
            failed_reasons.append("qps_gain_below_15pct")
        if not target_not_bottleneck:
            failed_reasons.append("target_still_bottleneck")
        if not target_drop_in_range:
            failed_reasons.append("target_drop_outside_5_to_10pct")
        return FinalValidationDecision(
            scenario=self.scenario,
            successful=successful,
            reason="final_success" if successful else "+".join(failed_reasons),
            qps_gain_ratio=qps_gain,
            qps_gain_threshold_met=qps_threshold_met,
            min_qps_gain_ratio=self.min_qps_gain_ratio,
            target_drop_ratio=target_drop_ratio,
            target_drop_in_range=target_drop_in_range,
            min_target_drop_ratio=self.min_target_drop_ratio,
            max_target_drop_ratio=self.max_target_drop_ratio,
            target_value=target_value,
            target_not_bottleneck=target_not_bottleneck,
            target_in_sweet_spot=target_in_sweet_spot,
            target_sweet_lower=self.target_sweet_lower,
            target_sweet_upper=self.target_sweet_upper,
            any_resource_higher=None,
            any_non_target_resource_higher=any_non_target_higher,
            resource_gain_ratios=gains,
        )

    def outer_acceptance(
        self,
        resource: Resource,
        raw: Any,
        _baseline_resource: Resource,
        _baseline_raw: Any,
    ) -> OuterAcceptanceDecision:
        return self.outer_decide(measurement_from_payload(resource, raw))

    def final_acceptance(
        self,
        resource: Resource,
        raw: Any,
        _baseline_resource: Resource,
        _baseline_raw: Any,
    ) -> FinalValidationDecision:
        return self.final_decide(measurement_from_payload(resource, raw))

    def eligible(
        self,
        resource: Resource,
        raw: Any,
        _baseline_resource: Resource,
        _baseline_raw: Any,
    ) -> bool:
        """eligible."""
        return self.outer_decide(measurement_from_payload(resource, raw)).accepted


@dataclass(frozen=True)
class FreshExperience:
    """One current-run training row: parameter vector -> measured resources."""

    params: Params
    measurement: ScenarioMeasurement
    objective: float


@dataclass(frozen=True)
class FreshModelQualityThresholds:
    """Hard pre-optimizer gates for the current-run resource surrogate."""

    min_samples: int = 15
    max_relative_error_p95: float = 1.0
    max_relative_error_max: float = 5.0
    min_r2: float = 0.0
    max_pairwise_inversion_rate: float = 0.10
    score_tie_margin: float = 0.01
    signal_floor: float = 1e-9


class FreshExperienceModel:
    """Current-run resource estimator with one shared scoring formula.

    IDW predicts DB QPS and the three resource measurements for auditability, but only
    the predicted resources enter the inner score.  A real replay is scored by passing
    its measured resources through the same :class:`FreshBaselineObjective` instance.
    Top-K size control belongs to the optimizer and cannot alter candidate scores.
    """

    def __init__(
        self,
        objective: FreshBaselineObjective,
        param_ranges: Mapping[str, Tuple[float, float]],
        *,
        exploration_weight: float = 0.25,
        distance_power: float = 2.0,
        max_neighbors: int = 2,
    ) -> None:
        self.objective = objective
        self.param_ranges = {
            str(name): (float(bounds[0]), float(bounds[1]))
            for name, bounds in param_ranges.items()
        }
        self.exploration_weight = max(0.0, float(exploration_weight))
        self.distance_power = max(1.0, float(distance_power))
        if int(max_neighbors) < 1:
            raise ValueError("max_neighbors must be positive")
        self.max_neighbors = int(max_neighbors)
        self._rows: List[FreshExperience] = []
        self._lock = threading.Lock()
        self._version = 0
        self._mode: Dict[str, Any] = {
            "kind": "idw",
            "max_neighbors": self.max_neighbors,
            "distance_power": self.distance_power,
        }
        self._ridge_cache_version = -1
        self._ridge_cache_mode: Tuple[Tuple[str, Any], ...] = ()
        self._ridge_cache: Dict[str, np.ndarray] = {}
        self._support_radius_cache_version = -1
        self._support_radius_cache = 0.0

    @staticmethod
    def _signature(params: Mapping[str, float]) -> Tuple[Tuple[str, float], ...]:
        return tuple((name, round(float(value), 12)) for name, value in sorted(params.items()))

    def build_experience(
        self,
        params: Mapping[str, float],
        resource: Mapping[str, Any],
        raw: Any = None,
    ) -> FreshExperience:
        """Build a scored row without mutating the model (for fresh holdout gates)."""
        measurement = measurement_from_payload(resource, raw)
        return FreshExperience(
            params={name: float(value) for name, value in params.items()},
            measurement=measurement,
            objective=self.objective.decide(measurement).objective,
        )

    def add(self, params: Mapping[str, float], resource: Mapping[str, Any], raw: Any = None) -> None:
        row = self.build_experience(params, resource, raw)
        signature = self._signature(row.params)
        with self._lock:
            self._rows = [item for item in self._rows if self._signature(item.params) != signature]
            self._rows.append(row)
            self._version += 1
            self._ridge_cache_version = -1
            self._ridge_cache = {}
            self._support_radius_cache_version = -1
            self._support_radius_cache = 0.0

    def snapshot(self) -> List[FreshExperience]:
        with self._lock:
            return copy.deepcopy(self._rows)

    def _snapshot_with_version(self) -> Tuple[List[FreshExperience], int]:
        with self._lock:
            return copy.deepcopy(self._rows), self._version

    def _distance(self, left: Mapping[str, float], right: Mapping[str, float]) -> float:
        squared = 0.0
        count = 0
        for name, (low, high) in self.param_ranges.items():
            width = high - low
            if width <= 0.0:
                continue
            squared += ((float(left[name]) - float(right[name])) / width) ** 2
            count += 1
        return math.sqrt(squared / max(1, count))

    def _normalized_vector(self, params: Mapping[str, float]) -> np.ndarray:
        values: List[float] = []
        for name, (low, high) in self.param_ranges.items():
            width = high - low
            values.append(
                0.0 if width <= 0.0 else (float(params[name]) - low) / width
            )
        return np.asarray(values, dtype=float)

    def _ridge_features(self, params: Mapping[str, float], kind: str) -> np.ndarray:
        vector = self._normalized_vector(params)
        values = [1.0, *vector.tolist()]
        if kind == "ridge_quadratic":
            values.extend((vector * vector).tolist())
        return np.asarray(values, dtype=float)

    def _fit_ridge(
        self,
        rows: Sequence[FreshExperience],
        mode: Mapping[str, Any],
    ) -> Dict[str, np.ndarray]:
        kind = str(mode.get("kind") or "ridge_linear")
        alpha = max(0.0, float(mode.get("alpha") or 0.0))
        matrix = np.vstack([self._ridge_features(row.params, kind) for row in rows])
        penalty = np.eye(matrix.shape[1], dtype=float) * alpha
        penalty[0, 0] = 0.0
        gram = matrix.T @ matrix + penalty
        result: Dict[str, np.ndarray] = {}
        for name in ("db_qps", "cpu", "memory", "io"):
            target = np.asarray(
                [float(getattr(row.measurement, name)) for row in rows],
                dtype=float,
            )
            try:
                coef = np.linalg.solve(gram, matrix.T @ target)
            except np.linalg.LinAlgError:
                coef = np.linalg.pinv(gram) @ matrix.T @ target
            result[name] = coef
        return result

    @staticmethod
    def _fit_affine_calibration(
        rows: Sequence[FreshExperience],
        predicted: Sequence[ScenarioMeasurement],
        alpha: float,
    ) -> Dict[str, Dict[str, float]]:
        """Fit a current-run affine correction on base-model OOF predictions."""
        if len(rows) != len(predicted) or not rows:
            raise ValueError("affine calibration requires matching non-empty rows")
        penalty_value = max(0.0, float(alpha))
        result: Dict[str, Dict[str, float]] = {}
        for name in ("db_qps", "cpu", "memory", "io"):
            estimate = np.asarray(
                [float(getattr(item, name)) for item in predicted],
                dtype=float,
            )
            actual = np.asarray(
                [float(getattr(row.measurement, name)) for row in rows],
                dtype=float,
            )
            matrix = np.column_stack((np.ones(len(estimate), dtype=float), estimate))
            penalty = np.diag([0.0, penalty_value])
            gram = matrix.T @ matrix + penalty
            target = matrix.T @ actual
            try:
                coefficients = np.linalg.solve(gram, target)
            except np.linalg.LinAlgError:
                coefficients = np.linalg.pinv(gram) @ target
            result[name] = {
                "intercept": float(coefficients[0]),
                "slope": float(coefficients[1]),
            }
        return result

    @staticmethod
    def _apply_affine_calibration(
        measurement: ScenarioMeasurement,
        coefficients: Mapping[str, Mapping[str, Any]],
    ) -> ScenarioMeasurement:
        values: Dict[str, float] = {}
        for name in ("db_qps", "cpu", "memory", "io"):
            resource_coefficients = coefficients.get(name)
            if not isinstance(resource_coefficients, Mapping):
                raise ValueError(f"affine calibration is missing {name} coefficients")
            intercept = float(resource_coefficients.get("intercept"))
            slope = float(resource_coefficients.get("slope"))
            values[name] = max(
                0.0,
                intercept + slope * float(getattr(measurement, name)),
            )
        return ScenarioMeasurement(**values)

    def _axis_reference_params(
        self,
        rows: Sequence[FreshExperience],
    ) -> Params:
        """Infer the current design anchor from the per-parameter modal values.

        Fresh calibration is generated around one baseline vector.  Coordinate
        rows change one parameter and pairwise rows change two, so the baseline
        value is the deterministic modal value of every parameter even when the
        baseline row itself is the leave-one-out target.
        """
        reference: Params = {}
        for name in self.param_ranges:
            counts: Dict[float, int] = {}
            originals: Dict[float, float] = {}
            for row in rows:
                value = float(row.params[name])
                key = round(value, 12)
                counts[key] = counts.get(key, 0) + 1
                originals.setdefault(key, value)
            selected = min(
                counts,
                key=lambda key: (-counts[key], key),
            )
            reference[name] = originals[selected]
        return reference

    @staticmethod
    def _nearest_axis_value(
        rows: Sequence[FreshExperience],
        indices: Sequence[int],
        parameter: str,
        query_value: float,
        resource_name: str,
    ) -> float:
        selected = min(
            indices,
            key=lambda index: (
                abs(float(rows[index].params[parameter]) - query_value),
                index,
            ),
        )
        return float(getattr(rows[selected].measurement, resource_name))

    def _predict_axis_nearest_additive(
        self,
        rows: Sequence[FreshExperience],
        params: Mapping[str, float],
    ) -> Tuple[ScenarioMeasurement, List[int]]:
        """Predict nonlinear coordinate effects without changing score semantics.

        Each resource is represented as the baseline value plus the sum of
        per-parameter deltas.  A delta is read from the nearest fresh point on
        that parameter's coordinate axis.  Pairwise calibration rows are not
        treated as one-dimensional evidence, preventing interaction samples
        from contaminating the coordinate curves.
        """
        reference = self._axis_reference_params(rows)
        axis_indices: Dict[str, List[int]] = {}
        for name in self.param_ranges:
            axis_indices[name] = [
                index
                for index, row in enumerate(rows)
                if all(
                    other == name
                    or math.isclose(
                        float(row.params[other]),
                        float(reference[other]),
                        rel_tol=0.0,
                        abs_tol=1e-12,
                    )
                    for other in self.param_ranges
                )
            ]
        if any(not indices for indices in axis_indices.values()):
            raise RuntimeError("axis-nearest model has an uncovered parameter axis")

        reference_index = next(
            (
                index
                for index, row in enumerate(rows)
                if self._signature(row.params) == self._signature(reference)
            ),
            None,
        )
        used_indices = set()
        values: Dict[str, float] = {}
        for resource_name in ("db_qps", "cpu", "memory", "io"):
            if reference_index is not None:
                baseline_value = float(
                    getattr(rows[reference_index].measurement, resource_name)
                )
                used_indices.add(reference_index)
            else:
                baseline_candidates = []
                for name, indices in axis_indices.items():
                    baseline_candidates.append(
                        self._nearest_axis_value(
                            rows,
                            indices,
                            name,
                            float(reference[name]),
                            resource_name,
                        )
                    )
                    used_indices.add(
                        min(
                            indices,
                            key=lambda index: (
                                abs(
                                    float(rows[index].params[name])
                                    - float(reference[name])
                                ),
                                index,
                            ),
                        )
                    )
                baseline_value = float(np.median(baseline_candidates))

            predicted = baseline_value
            for name, indices in axis_indices.items():
                query_value = float(params[name])
                reference_value = float(reference[name])
                if math.isclose(
                    query_value,
                    reference_value,
                    rel_tol=0.0,
                    abs_tol=1e-12,
                ):
                    continue
                query_index = min(
                    indices,
                    key=lambda index: (
                        abs(float(rows[index].params[name]) - query_value),
                        index,
                    ),
                )
                base_index = min(
                    indices,
                    key=lambda index: (
                        abs(float(rows[index].params[name]) - reference_value),
                        index,
                    ),
                )
                used_indices.update((query_index, base_index))
                predicted += float(
                    getattr(rows[query_index].measurement, resource_name)
                ) - float(getattr(rows[base_index].measurement, resource_name))
            values[resource_name] = max(0.0, predicted)
        return ScenarioMeasurement(**values), sorted(used_indices)

    @staticmethod
    def _mode_signature(mode: Mapping[str, Any]) -> Tuple[Tuple[str, Any], ...]:
        return tuple(sorted((str(key), value) for key, value in mode.items()))

    def _empirical_support_radius(
        self,
        rows: Sequence[FreshExperience],
        *,
        version: Optional[int] = None,
    ) -> float:
        """Return the robust nearest-neighbour spacing of measured vectors.

        A polynomial ridge model is useful for interpolation but has no physical
        evidence for an arbitrarily distant parameter combination.  The 95th
        percentile of each measured vector's nearest-neighbour distance defines
        the empirical support radius.  It depends only on parameter coordinates,
        never on resources, Score, QPS, or an old optimizer decision.
        """
        if version is not None:
            with self._lock:
                if self._support_radius_cache_version == version:
                    return self._support_radius_cache
        if len(rows) < 2:
            radius = 0.0
        else:
            nearest_distances = sorted(
                min(
                    self._distance(row.params, other.params)
                    for other_index, other in enumerate(rows)
                    if other_index != row_index
                )
                for row_index, row in enumerate(rows)
            )
            percentile_index = min(
                len(nearest_distances) - 1,
                max(0, math.ceil(0.95 * len(nearest_distances)) - 1),
            )
            radius = float(nearest_distances[percentile_index])
        if version is not None:
            with self._lock:
                if self._version == version:
                    self._support_radius_cache_version = version
                    self._support_radius_cache = radius
        return radius

    def _predict_measurement_from_rows(
        self,
        rows: Sequence[FreshExperience],
        params: Mapping[str, float],
        mode: Mapping[str, Any],
        *,
        version: Optional[int] = None,
    ) -> Tuple[ScenarioMeasurement, List[int], float]:
        if not rows:
            raise RuntimeError("fresh experience model has no current-run rows")
        distances = [self._distance(params, row.params) for row in rows]
        exact_index = next(
            (idx for idx, value in enumerate(distances) if value <= 1e-12),
            None,
        )
        if exact_index is not None:
            return rows[exact_index].measurement, [exact_index], 0.0

        kind = str(mode.get("kind") or "idw")
        novelty = min(distances)
        if kind == "idw":
            neighbor_count = min(
                len(rows),
                max(1, int(mode.get("max_neighbors") or self.max_neighbors)),
            )
            distance_power = max(
                1.0,
                float(mode.get("distance_power") or self.distance_power),
            )
            neighbor_indices = sorted(
                range(len(rows)),
                key=lambda index: distances[index],
            )[:neighbor_count]
            weights = [
                1.0 / (max(distances[index], 1e-9) ** distance_power)
                for index in neighbor_indices
            ]
            weight_sum = sum(weights)
            measurement = ScenarioMeasurement(
                **{
                    name: max(
                        0.0,
                        sum(
                            weight * float(getattr(rows[index].measurement, name))
                            for weight, index in zip(weights, neighbor_indices)
                        )
                        / weight_sum,
                    )
                    for name in ("db_qps", "cpu", "memory", "io")
                }
            )
            return measurement, neighbor_indices, novelty

        if kind == "axis_nearest_additive":
            measurement, neighbor_indices = self._predict_axis_nearest_additive(
                rows,
                params,
            )
            return measurement, neighbor_indices, novelty

        if kind == "affine_calibrated":
            base_mode = mode.get("base_mode")
            coefficients = mode.get("coefficients")
            if not isinstance(base_mode, Mapping):
                raise ValueError("affine-calibrated mode requires a base_mode")
            if str(base_mode.get("kind") or "") == "affine_calibrated":
                raise ValueError("nested affine-calibrated modes are forbidden")
            if not isinstance(coefficients, Mapping):
                raise ValueError("affine-calibrated mode requires fitted coefficients")
            base_measurement, neighbor_indices, base_novelty = (
                self._predict_measurement_from_rows(
                    rows,
                    params,
                    base_mode,
                    version=version,
                )
            )
            return (
                self._apply_affine_calibration(base_measurement, coefficients),
                neighbor_indices,
                base_novelty,
            )

        if kind not in {"ridge_linear", "ridge_quadratic"}:
            raise ValueError(f"unsupported fresh experience model mode: {kind}")
        support_radius = self._empirical_support_radius(rows, version=version)
        if novelty > support_radius + 1e-12:
            # Never extrapolate a polynomial resource surface beyond the region
            # supported by real parameter->resource measurements.  Returning the
            # nearest measured resource vector changes only resource estimation;
            # the shared resource Score formula and all Top-K semantics remain
            # exactly the same.
            nearest_index = min(
                range(len(rows)),
                key=lambda index: (distances[index], index),
            )
            return rows[nearest_index].measurement, [nearest_index], novelty
        mode_signature = self._mode_signature(mode)
        coefficients: Dict[str, np.ndarray]
        if (
            version is not None
            and self._ridge_cache_version == version
            and self._ridge_cache_mode == mode_signature
            and self._ridge_cache
        ):
            coefficients = self._ridge_cache
        else:
            coefficients = self._fit_ridge(rows, mode)
            if version is not None:
                with self._lock:
                    if self._version == version:
                        self._ridge_cache_version = version
                        self._ridge_cache_mode = mode_signature
                        self._ridge_cache = coefficients
        features = self._ridge_features(params, kind)
        values = {
            name: max(0.0, float(features @ coefficients[name]))
            for name in ("db_qps", "cpu", "memory", "io")
        }
        return ScenarioMeasurement(**values), [], novelty

    @staticmethod
    def _pairwise_inversion(
        estimated_scores: Sequence[float],
        real_scores: Sequence[float],
        *,
        tie_margin: float = 0.0,
    ) -> Tuple[int, int, Optional[float]]:
        """Return order errors among pairs distinguishable in real Score.

        A real-score delta within ``tie_margin`` is physically indistinguishable
        and therefore carries no ordering requirement.  The margin must not be
        reused on the estimated delta: ranking compares direction, not whether
        the surrogate reproduces the real score gap.  A numerically exact
        estimated tie is still a miss, which prevents a constant predictor from
        passing.  If every real pair is tied, there is no ordering to recover.
        """
        margin = max(0.0, float(tie_margin))
        pairs = 0
        inversions = 0
        for left in range(len(real_scores)):
            for right in range(left + 1, len(real_scores)):
                estimated_delta = estimated_scores[left] - estimated_scores[right]
                real_delta = real_scores[left] - real_scores[right]
                if abs(real_delta) <= margin:
                    continue
                pairs += 1
                if (
                    abs(estimated_delta) <= 1e-12
                    or estimated_delta * real_delta < 0.0
                ):
                    inversions += 1
        return inversions, pairs, inversions / pairs if pairs else 0.0

    def _evaluate_mode(
        self,
        rows: Sequence[FreshExperience],
        mode: Mapping[str, Any],
        thresholds: FreshModelQualityThresholds,
        *,
        fixed_training_rows: Optional[Sequence[FreshExperience]] = None,
        always_training_rows: Optional[Sequence[FreshExperience]] = None,
    ) -> Dict[str, Any]:
        test_sample_count = len(rows)
        required_test_sample_count = max(15, thresholds.min_samples)
        if fixed_training_rows is None:
            # Leave-one-out keeps every validation target strictly outside the
            # corresponding fit while using the current-run rows only.
            validation_kind = "leave_one_out_current_run_only"
            required_train_sample_count_per_fold = max(
                1,
                required_test_sample_count - 1,
            )
        else:
            # Historical parameter->resource rows are only an anchored prior.
            # Each fold removes the held-out Top15 signature from that prior,
            # then lets the other 14 fresh rows replace matching stale rows.
            # This is a real current-Top15 leave-one-out check: an old exact
            # parameter match can never answer the held-out query directly.
            validation_kind = (
                "anchored_historical_seed_plus_current_top15_leave_one_out"
            )
            required_train_sample_count_per_fold = max(
                1,
                required_test_sample_count - 1,
            )

        fixed_current_rows = list(copy.deepcopy(always_training_rows or []))
        fold_training_rows: List[List[FreshExperience]] = []
        for held_out_index, held_out in enumerate(rows):
            held_out_signature = self._signature(held_out.params)
            by_signature: Dict[
                Tuple[Tuple[str, float], ...], FreshExperience
            ] = {}
            if fixed_training_rows is None:
                for row in fixed_current_rows:
                    signature = self._signature(row.params)
                    if signature != held_out_signature:
                        by_signature[signature] = row
            else:
                for row in fixed_training_rows:
                    signature = self._signature(row.params)
                    if signature != held_out_signature:
                        by_signature[signature] = row
                for row in fixed_current_rows:
                    signature = self._signature(row.params)
                    if signature != held_out_signature:
                        # The current invocation's fresh baseline overrides an
                        # anchored historical baseline at the same vector.
                        by_signature[signature] = row
            for index, row in enumerate(rows):
                if index == held_out_index:
                    continue
                signature = self._signature(row.params)
                if signature != held_out_signature:
                    # A fresh measurement is stronger evidence than either a
                    # fixed current anchor or an anchored historical row.
                    by_signature[signature] = row
            train = list(by_signature.values())
            fold_training_rows.append(train)

        train_sample_counts_per_fold = [
            len(train) for train in fold_training_rows
        ]
        train_sample_count_per_fold = min(
            train_sample_counts_per_fold,
            default=0,
        )
        sample_count_gate_passed = (
            test_sample_count >= required_test_sample_count
            and train_sample_count_per_fold >= required_train_sample_count_per_fold
        )
        mode_kind = str(mode.get("kind") or "idw")
        prediction_mode: Mapping[str, Any] = mode
        affine_alpha: Optional[float] = None
        if mode_kind == "affine_calibrated":
            base_mode = mode.get("base_mode")
            if not isinstance(base_mode, Mapping):
                raise ValueError("affine-calibrated evaluation requires a base_mode")
            if fixed_training_rows is not None:
                raise ValueError(
                    "current-run affine calibration cannot use a fixed historical seed"
                )
            prediction_mode = base_mode
            affine_alpha = max(0.0, float(mode.get("calibration_alpha") or 0.0))

        evaluated_mode = copy.deepcopy(dict(mode))
        affine_coefficients: Optional[Dict[str, Dict[str, float]]] = None
        predicted: List[ScenarioMeasurement] = []
        if affine_alpha is None:
            for held_out_index, held_out in enumerate(rows):
                train = fold_training_rows[held_out_index]
                measurement, _, _ = self._predict_measurement_from_rows(
                    train,
                    held_out.params,
                    prediction_mode,
                )
                predicted.append(measurement)
        else:
            # Nested leave-one-out is mandatory here.  Fitting one affine
            # correction on all outer-fold predictions would let held-out
            # target i influence the coefficients subsequently applied back to
            # prediction i.  In particular, a strongly regularized base model
            # can then create the artificial slope ``-(n - 1)`` and appear
            # perfect.  Each outer fold therefore learns its affine correction
            # only from inner OOF predictions of that outer fold's training
            # rows; the outer target never participates in either fit.
            for held_out_index, held_out in enumerate(rows):
                outer_train = fold_training_rows[held_out_index]
                inner_predicted: List[ScenarioMeasurement] = []
                for calibration_index, calibration_row in enumerate(outer_train):
                    calibration_signature = self._signature(calibration_row.params)
                    inner_train = [
                        row
                        for index, row in enumerate(outer_train)
                        if index != calibration_index
                        and self._signature(row.params) != calibration_signature
                    ]
                    if not inner_train:
                        raise RuntimeError(
                            "nested affine calibration needs at least two outer-training rows"
                        )
                    inner_measurement, _, _ = self._predict_measurement_from_rows(
                        inner_train,
                        calibration_row.params,
                        prediction_mode,
                    )
                    inner_predicted.append(inner_measurement)
                outer_coefficients = self._fit_affine_calibration(
                    outer_train,
                    inner_predicted,
                    affine_alpha,
                )
                outer_measurement, _, _ = self._predict_measurement_from_rows(
                    outer_train,
                    held_out.params,
                    prediction_mode,
                )
                predicted.append(
                    self._apply_affine_calibration(
                        outer_measurement,
                        outer_coefficients,
                    )
                )

            # Runtime prediction is for a genuinely unseen optimizer candidate.
            # Fit its correction from OOF predictions of every currently known
            # row, including fixed current anchors such as the fresh baseline.
            by_signature = {
                self._signature(row.params): row
                for row in [*fixed_current_rows, *rows]
            }
            calibration_rows = list(by_signature.values())
            calibration_predictions: List[ScenarioMeasurement] = []
            for calibration_index, calibration_row in enumerate(calibration_rows):
                calibration_signature = self._signature(calibration_row.params)
                calibration_train = [
                    row
                    for index, row in enumerate(calibration_rows)
                    if index != calibration_index
                    and self._signature(row.params) != calibration_signature
                ]
                measurement, _, _ = self._predict_measurement_from_rows(
                    calibration_train,
                    calibration_row.params,
                    prediction_mode,
                )
                calibration_predictions.append(measurement)
            affine_coefficients = self._fit_affine_calibration(
                calibration_rows,
                calibration_predictions,
                affine_alpha,
            )
            evaluated_mode["coefficients"] = copy.deepcopy(affine_coefficients)
            evaluated_mode["calibration_provenance"] = (
                "nested_current_run_leave_one_out_predictions_only"
            )
            validation_kind = (
                "nested_leave_one_out_base_predictions_with_current_run_affine_calibration"
            )

        resource_metrics: Dict[str, Dict[str, Any]] = {}
        resource_passed = True
        for name in ("cpu", "memory", "io"):
            actual = np.asarray(
                [float(getattr(row.measurement, name)) for row in rows],
                dtype=float,
            )
            estimate = np.asarray(
                [float(getattr(item, name)) for item in predicted],
                dtype=float,
            )
            residual = actual - estimate
            relative = np.abs(residual) / np.maximum(
                np.abs(actual),
                thresholds.signal_floor,
            )
            denominator = float(np.sum((actual - np.mean(actual)) ** 2))
            numerator = float(np.sum(residual * residual))
            r2 = (
                1.0 if denominator <= thresholds.signal_floor and numerator <= thresholds.signal_floor
                else float("-inf") if denominator <= thresholds.signal_floor
                else 1.0 - numerator / denominator
            )
            p95 = float(np.quantile(relative, 0.95))
            maximum = float(np.max(relative))
            passed = (
                p95 <= thresholds.max_relative_error_p95
                and maximum <= thresholds.max_relative_error_max
                and r2 >= thresholds.min_r2
            )
            resource_passed = resource_passed and passed
            resource_metrics[name] = {
                "sample_count": len(rows),
                "train_sample_count_per_fold": train_sample_count_per_fold,
                "test_sample_count": test_sample_count,
                "mae": float(np.mean(np.abs(residual))),
                "relative_error_p95": p95,
                "relative_error_max": maximum,
                "r2": r2,
                "signed_residual_lower": float(min(0.0, float(np.min(residual)))),
                "signed_residual_upper": float(max(0.0, float(np.max(residual)))),
                "passed": passed,
            }

        estimated_scores = [
            float(self.objective.resource_score_details(item)["total"])
            for item in predicted
        ]
        real_scores = [float(row.objective) for row in rows]
        inversion_count, pair_count, inversion_rate = self._pairwise_inversion(
            estimated_scores,
            real_scores,
            tie_margin=thresholds.score_tie_margin,
        )
        overall_pairwise_gate_passed = (
            inversion_rate is not None
            and inversion_rate <= thresholds.max_pairwise_inversion_rate
        )
        estimated_order = sorted(
            range(len(estimated_scores)),
            key=lambda index: (-estimated_scores[index], index),
        )
        ranking_prefixes: Dict[str, Dict[str, Any]] = {}
        for prefix_size in (10, 15):
            if len(estimated_order) < prefix_size:
                ranking_prefixes[f"top{prefix_size}"] = {
                    "required_count": prefix_size,
                    "actual_count": len(estimated_order),
                    "pairwise_inversion_count": None,
                    "pairwise_pair_count": None,
                    "pairwise_inversion_rate": None,
                    "passed": False,
                    "reason": "insufficient_held_out_predictions",
                }
                continue
            prefix = estimated_order[:prefix_size]
            prefix_inversions, prefix_pairs, prefix_rate = self._pairwise_inversion(
                [estimated_scores[index] for index in prefix],
                [real_scores[index] for index in prefix],
                tie_margin=thresholds.score_tie_margin,
            )
            ranking_prefixes[f"top{prefix_size}"] = {
                "required_count": prefix_size,
                "actual_count": prefix_size,
                "pairwise_inversion_count": prefix_inversions,
                "pairwise_pair_count": prefix_pairs,
                "pairwise_inversion_rate": prefix_rate,
                "passed": (
                    prefix_rate is not None
                    and prefix_rate <= thresholds.max_pairwise_inversion_rate
                ),
            }
        ranking_passed = (
            overall_pairwise_gate_passed
            and all(
                bool(ranking_prefixes[name]["passed"])
                for name in ("top10", "top15")
            )
        )
        worst_ranking_inversion = max(
            [
                float(item["pairwise_inversion_rate"])
                for item in ranking_prefixes.values()
                if item.get("pairwise_inversion_rate") is not None
            ]
            + ([float(inversion_rate)] if inversion_rate is not None else [])
            or [float("inf")]
        )
        worst_p95 = max(
            item["relative_error_p95"] for item in resource_metrics.values()
        )
        worst_max = max(
            item["relative_error_max"] for item in resource_metrics.values()
        )
        worst_r2 = min(item["r2"] for item in resource_metrics.values())
        return {
            "mode": evaluated_mode,
            "validation": validation_kind,
            "train_sample_count_per_fold": train_sample_count_per_fold,
            "train_sample_counts_per_fold": train_sample_counts_per_fold,
            "always_training_row_count": len(fixed_current_rows),
            "test_sample_count": test_sample_count,
            "required_train_sample_count_per_fold": required_train_sample_count_per_fold,
            "required_test_sample_count": required_test_sample_count,
            "sample_count_gate_passed": sample_count_gate_passed,
            "resource_metrics": resource_metrics,
            "estimated_scores": estimated_scores,
            "real_scores": real_scores,
            "pairwise_inversion_count": inversion_count,
            "pairwise_pair_count": pair_count,
            "pairwise_inversion_rate": inversion_rate,
            "overall_pairwise_gate_passed": overall_pairwise_gate_passed,
            "ranking_prefixes": ranking_prefixes,
            "resource_error_gate_passed": resource_passed,
            "score_ranking_gate_passed": ranking_passed,
            "affine_calibration_fit_sample_count": (
                len({
                    self._signature(row.params)
                    for row in [*fixed_current_rows, *rows]
                }) if affine_coefficients is not None else 0
            ),
            "affine_calibration_current_run_only": (
                affine_coefficients is not None
            ),
            "passed": sample_count_gate_passed and resource_passed and ranking_passed,
            "selection_key": [
                0 if sample_count_gate_passed and resource_passed and ranking_passed else 1,
                worst_ranking_inversion,
                worst_p95,
                worst_max,
                -worst_r2,
            ],
        }

    @staticmethod
    def candidate_modes(
        sample_count: int,
        *,
        include_affine_calibration: bool = True,
    ) -> List[Dict[str, Any]]:
        """Deterministic current-run modes; no historical model/artifact labels."""
        result: List[Dict[str, Any]] = [{"kind": "axis_nearest_additive"}]
        for neighbor_count in (1, 2, 3, 4, 6, 8, 12):
            if neighbor_count > max(1, sample_count - 1):
                continue
            for power in (1.0, 2.0, 3.0, 4.0):
                result.append(
                    {
                        "kind": "idw",
                        "max_neighbors": neighbor_count,
                        "distance_power": power,
                    }
                )
        for kind in ("ridge_linear", "ridge_quadratic"):
            for alpha in (1e-6, 1e-4, 1e-2, 0.1, 1.0, 10.0, 100.0):
                result.append({"kind": kind, "alpha": alpha})
        if include_affine_calibration:
            base_modes = copy.deepcopy(result)
            for base_mode in base_modes:
                for alpha in (0.0, 1e-6, 1e-4, 1e-2, 0.1, 1.0, 10.0, 100.0):
                    result.append(
                        {
                            "kind": "affine_calibrated",
                            "base_mode": copy.deepcopy(base_mode),
                            "calibration_alpha": alpha,
                        }
                    )
        return result

    def select_quality_mode(
        self,
        thresholds: FreshModelQualityThresholds,
        *,
        validation_rows: Optional[Sequence[FreshExperience]] = None,
        fixed_training_rows: Optional[Sequence[FreshExperience]] = None,
        always_training_rows: Optional[Sequence[FreshExperience]] = None,
    ) -> Dict[str, Any]:
        """Train/select a mode and enforce error plus score-ranking gates."""
        rows = (
            list(copy.deepcopy(validation_rows))
            if validation_rows is not None
            else self.snapshot()
        )
        threshold_record = {
            "min_samples": thresholds.min_samples,
            "max_relative_error_p95": thresholds.max_relative_error_p95,
            "max_relative_error_max": thresholds.max_relative_error_max,
            "min_r2": thresholds.min_r2,
            "max_pairwise_inversion_rate": thresholds.max_pairwise_inversion_rate,
            "score_tie_margin": thresholds.score_tie_margin,
            "signal_floor": thresholds.signal_floor,
        }
        effective_min_samples = max(15, thresholds.min_samples)
        threshold_record["effective_min_samples_for_top15"] = effective_min_samples
        if len(rows) < effective_min_samples:
            return {
                "passed": False,
                "reason": "minimum_current_run_calibration_samples_not_reached",
                "sample_count": len(rows),
                "required_sample_count": effective_min_samples,
                "thresholds": threshold_record,
                "mode_evaluations": [],
                "current_run_only": True,
            }
        evaluations: List[Dict[str, Any]] = []
        anchored_evaluations: List[Dict[str, Any]] = []

        def evaluate_mode_safely(
            mode: Mapping[str, Any],
            *,
            strategy: str,
            fixed_rows: Optional[Sequence[FreshExperience]],
        ) -> Dict[str, Any]:
            """Let an inapplicable model mode fail without aborting selection."""
            try:
                evaluation = self._evaluate_mode(
                    rows,
                    mode,
                    thresholds,
                    fixed_training_rows=fixed_rows,
                    always_training_rows=always_training_rows,
                )
            except Exception as exc:
                evaluation = {
                    "mode": copy.deepcopy(dict(mode)),
                    "validation": "mode_evaluation_failed",
                    "sample_count_gate_passed": False,
                    "resource_metrics": {},
                    "ranking_prefixes": {},
                    "resource_error_gate_passed": False,
                    "score_ranking_gate_passed": False,
                    "passed": False,
                    "evaluation_error": f"{type(exc).__name__}: {exc}",
                    "selection_key": [
                        2,
                        float("inf"),
                        float("inf"),
                        float("inf"),
                        float("inf"),
                    ],
                }
            evaluation["training_strategy"] = strategy
            return evaluation

        if fixed_training_rows is not None:
            for mode in self.candidate_modes(
                len(rows),
                include_affine_calibration=False,
            ):
                evaluation = evaluate_mode_safely(
                    mode,
                    strategy=(
                        "anchored_historical_seed_plus_current_top15_leave_one_out"
                    ),
                    fixed_rows=fixed_training_rows,
                )
                anchored_evaluations.append(evaluation)
            evaluations.extend(anchored_evaluations)

        # A historical parameter->resource memory is optional evidence, never
        # a reason to run a model that fails the current Top15 gate.  Always
        # evaluate the current-run-only alternative so drift can be handled
        # without buying more calibration replays or weakening thresholds.
        if fixed_training_rows is None or not any(
            bool(item["passed"]) for item in anchored_evaluations
        ):
            for mode in self.candidate_modes(
                len(rows),
                include_affine_calibration=True,
            ):
                evaluation = evaluate_mode_safely(
                    mode,
                    strategy="current_top15_only",
                    fixed_rows=None,
                )
                evaluations.append(evaluation)
        evaluations.sort(key=lambda item: tuple(item["selection_key"]))
        best = evaluations[0]
        selected_training_strategy = str(best["training_strategy"])
        historical_training_rows_selected = (
            selected_training_strategy
            == "anchored_historical_seed_plus_current_top15_leave_one_out"
        )
        if best["passed"]:
            with self._lock:
                self._mode = copy.deepcopy(best["mode"])
                self._ridge_cache_version = -1
                self._ridge_cache = {}
        return {
            "passed": bool(best["passed"]),
            "reason": (
                "resource_error_and_score_ranking_gates_passed"
                if best["passed"]
                else "no_current_run_model_mode_passed_all_quality_gates"
            ),
            "sample_count": len(rows),
            "thresholds": threshold_record,
            "selected_mode": copy.deepcopy(best["mode"]),
            "selected_evaluation": copy.deepcopy(best),
            "mode_evaluations": evaluations,
            "selected_training_strategy": selected_training_strategy,
            "current_run_only": not historical_training_rows_selected,
            "historical_training_rows_used": historical_training_rows_selected,
            "historical_training_rows_evaluated": fixed_training_rows is not None,
            "historical_seed_discarded_after_quality_failure": (
                fixed_training_rows is not None
                and not historical_training_rows_selected
            ),
            "score_formula": "sigmoid_linear_sweet_band_weighted_mean_v2",
        }

    def predict(self, _sql_set: Any, params: Params) -> Dict[str, Any]:
        rows, version = self._snapshot_with_version()
        if not rows:
            raise RuntimeError("fresh experience model has no current-run baseline")
        distances = [self._distance(params, row.params) for row in rows]
        predicted_measurement, neighbor_indices, novelty = (
            self._predict_measurement_from_rows(
                rows,
                params,
                self._mode,
                version=version,
            )
        )
        predicted_resource = predicted_measurement.as_resource()
        predicted_objective = self.objective.decide(predicted_measurement).objective
        support_radius = self._empirical_support_radius(rows, version=version)
        selected_kind = str(self._mode.get("kind") or "idw")
        outside_empirical_support = novelty > support_radius + 1e-12
        support_fallback_applied = (
            selected_kind in {"ridge_linear", "ridge_quadratic"}
            and outside_empirical_support
            and bool(neighbor_indices)
        )
        baseline_params = rows[0].params
        directional_delta = 0.0
        directional_count = 0
        for name, (low, high) in self.param_ranges.items():
            width = high - low
            if width <= 0.0:
                continue
            directional_delta += (
                float(params[name]) - float(baseline_params[name])
            ) / width
            directional_count += 1
        directional_delta /= max(1, directional_count)
        # Grid search has no gradient warm-start acquisition term.  Every model
        # output exposed as a score is the exact same resource-only formula used
        # for real replay; novelty/direction remain audit metadata only.
        search_score = predicted_objective
        return {
            "resource": predicted_resource,
            "db_qps": predicted_measurement.db_qps,
            "score": search_score,
            "search_score": search_score,
            "estimated_score": predicted_objective,
            "model": {
                "kind": "current_run_resource_vector_selected_mode",
                "selected_mode": copy.deepcopy(self._mode),
                "training_target": "resource_vector",
                "score_formula": "sigmoid_linear_sweet_band_weighted_mean_v2",
                "estimated_and_real_share_formula": True,
                "acquisition_enabled": False,
                "experience_count": len(rows),
                "neighbor_count": len(neighbor_indices),
                "max_neighbors": self.max_neighbors,
                "novelty": novelty,
                "empirical_support_radius": support_radius,
                "outside_empirical_support": outside_empirical_support,
                "support_fallback_applied": support_fallback_applied,
                "support_fallback": "nearest_measured_resource_vector",
                "directional_tiebreak": directional_delta,
                "predicted_resource": predicted_resource,
                "predicted_resource_score_details": self.objective.resource_score_details(
                    predicted_measurement
                ),
                "predicted_objective": predicted_objective,
                "search_score": search_score,
            },
        }
