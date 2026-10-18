"""Fail-closed evaluator for the frozen six-session outer acceptance policy.

The functions in this module are pure.  They do not collect measurements and
they never contact a database.  A caller must pass the fully materialized live
replay summaries for the current outer incumbent and candidate.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence


class FrozenAcceptanceError(RuntimeError):
    """A measurement or policy cannot be evaluated under the frozen rules."""


@dataclass(frozen=True)
class LivePoint:
    correctness_passed: bool
    stable: bool
    sample_coverage: float
    error_rate: float
    oom: bool
    database_restarted: bool
    qps: float | None
    xap_qps: float | None
    xtp_tps: float | None
    latency_p95_ms: float
    latency_p99_ms: float
    cpu_utilization: float
    memory_utilization: float
    io_utilization: float


@dataclass(frozen=True)
class OuterDecision:
    accepted: bool
    reason: str
    utility_improvement: float | None
    throughput_ratio: float | None
    target_pressure_drop_ratio: float | None
    branch_throughput_passed: bool
    branch_resource_passed: bool
    hard_gate_failures: tuple[str, ...]
    plan_changed: bool


@dataclass(frozen=True)
class EligibleFinalPoint:
    """A default or previously Outer-accepted point eligible for final selection."""

    label: str
    candidate_index: int
    point: LivePoint


@dataclass(frozen=True)
class FinalSelection:
    """Auditable application of the frozen deterministic final tie-break rule."""

    label: str
    candidate_index: int
    utility_from_default: float
    throughput_ratio_from_default: float
    target_pressure: float
    eligible_point_count: int


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FrozenAcceptanceError(f"{label} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise FrozenAcceptanceError(f"{label} must be finite")
    return number


def _ratio(numerator: float, denominator: float, label: str) -> float:
    if denominator <= 0.0 or numerator < 0.0:
        raise FrozenAcceptanceError(f"{label} requires denominator > 0 and numerator >= 0")
    return numerator / denominator


def _validate_point(point: LivePoint, *, xp: bool, label: str) -> None:
    if type(point.correctness_passed) is not bool or type(point.stable) is not bool:
        raise FrozenAcceptanceError(f"{label} correctness/stability flags must be boolean")
    if type(point.oom) is not bool or type(point.database_restarted) is not bool:
        raise FrozenAcceptanceError(f"{label} failure flags must be boolean")
    numeric = {
        "sample_coverage": point.sample_coverage,
        "error_rate": point.error_rate,
        "latency_p95_ms": point.latency_p95_ms,
        "latency_p99_ms": point.latency_p99_ms,
        "cpu_utilization": point.cpu_utilization,
        "memory_utilization": point.memory_utilization,
        "io_utilization": point.io_utilization,
    }
    for name, value in numeric.items():
        number = _finite(value, f"{label}.{name}")
        if number < 0.0:
            raise FrozenAcceptanceError(f"{label}.{name} must be non-negative")
    for name in ("sample_coverage", "error_rate", "cpu_utilization", "memory_utilization", "io_utilization"):
        if float(getattr(point, name)) > 1.0:
            raise FrozenAcceptanceError(f"{label}.{name} must use a [0,1] ratio")
    if point.latency_p95_ms <= 0.0 or point.latency_p99_ms <= 0.0:
        raise FrozenAcceptanceError(f"{label} latency values must be positive")
    if point.latency_p99_ms < point.latency_p95_ms:
        raise FrozenAcceptanceError(f"{label}.latency_p99_ms cannot be below p95")
    if xp:
        if point.qps is not None:
            raise FrozenAcceptanceError(f"{label}.qps must be null for XP")
        if point.xap_qps is None or point.xtp_tps is None:
            raise FrozenAcceptanceError(f"{label} XP streams must be present")
        if _finite(point.xap_qps, f"{label}.xap_qps") <= 0.0:
            raise FrozenAcceptanceError(f"{label}.xap_qps must be positive")
        if _finite(point.xtp_tps, f"{label}.xtp_tps") <= 0.0:
            raise FrozenAcceptanceError(f"{label}.xtp_tps must be positive")
    else:
        if point.xap_qps is not None or point.xtp_tps is not None:
            raise FrozenAcceptanceError(f"{label} non-XP stream metrics must be null")
        if point.qps is None or _finite(point.qps, f"{label}.qps") <= 0.0:
            raise FrozenAcceptanceError(f"{label}.qps must be positive")


def _strict_policy(path: Path) -> Mapping[str, Any]:
    policy = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(policy, dict):
        raise FrozenAcceptanceError("acceptance policy must be an object")
    expected_top = {
        "schema_version",
        "config_id",
        "immutable",
        "outer_decision_only",
        "comparison_reference",
        "common_hard_gates",
        "throughput",
        "bottleneck_candidate",
        "none_candidate",
        "final_selection",
        "inner_loop",
    }
    if set(policy) != expected_top:
        raise FrozenAcceptanceError("acceptance policy top-level fields differ")
    fixed = {
        "schema_version": 1,
        "config_id": "acceptance_policy_20260721_v1",
        "immutable": True,
        "outer_decision_only": True,
        "comparison_reference": "current_outer_incumbent",
    }
    for name, value in fixed.items():
        if policy.get(name) != value:
            raise FrozenAcceptanceError(f"acceptance policy {name} differs")
    hard = policy["common_hard_gates"]
    throughput = policy["throughput"]
    bottleneck = policy["bottleneck_candidate"]
    none = policy["none_candidate"]
    final = policy["final_selection"]
    inner = policy["inner_loop"]
    if not all(
        isinstance(value, dict)
        for value in (hard, throughput, bottleneck, none, final, inner)
    ):
        raise FrozenAcceptanceError("acceptance policy contains a non-object section")
    required_literals = (
        (hard, "correctness_required", True),
        (hard, "stable_required", True),
        (hard, "sample_coverage_min", 0.9),
        (hard, "error_rate_max", 0.01),
        (hard, "oom_allowed", False),
        (hard, "database_restart_allowed", False),
        (hard, "p95_regression_max_ratio", 0.1),
        (hard, "p99_regression_max_ratio", 0.1),
        (throughput, "normal_metric", "qps"),
        (throughput, "xp_metric", "equal_weight_geometric_mean_of_xap_qps_and_xtp_tps_ratios"),
        (throughput, "xp_each_stream_regression_max_ratio", 0.05),
        (bottleneck, "non_target_resource_utilization_max", 0.8),
        (bottleneck, "utility_min_improvement", 0.005),
        (bottleneck, "accept_if_either_branch_passes", True),
        (bottleneck, "utility_formula", "throughput_log_ratio + target_pressure_drop_ratio - 2*max(0,p95_ratio-1) - 3*max(0,p99_ratio-1) - 4*sum(max(0,non_target_util-0.8))"),
        (bottleneck["branch_throughput"], "throughput_improvement_min_ratio", 0.01),
        (bottleneck["branch_throughput"], "target_pressure_increase_max_ratio", 0.01),
        (bottleneck["branch_resource"], "target_pressure_drop_min_ratio", 0.03),
        (bottleneck["branch_resource"], "throughput_regression_max_ratio", 0.03),
        (none, "all_resource_utilization_max", 0.75),
        (none, "throughput_improvement_min_ratio", 0.01),
        (none, "utility_min_improvement", 0.005),
        (none, "utility_formula", "throughput_log_ratio - 2*max(0,p95_ratio-1) - 3*max(0,p99_ratio-1)"),
        (final, "eligible_points", "default_and_outer_accepted_candidates_only"),
        (final, "rule", "maximum_utility_then_higher_throughput_then_lower_target_pressure_then_earlier_candidate"),
        (final, "default_may_remain_final", True),
        (final, "plan_change_is_required_for_acceptance", False),
        (final, "plan_change_must_be_reported", True),
        (inner, "may_label", "inner_step_accepted"),
        (inner, "may_not_label", "outer_candidate_accepted"),
        (inner, "may_not_change_outer_policy", True),
        (inner, "does_not_consume_real_candidate_budget", True),
    )
    for container, name, value in required_literals:
        if container.get(name) != value:
            raise FrozenAcceptanceError(f"acceptance policy {name} differs")
    return policy


def _throughput_ratios(
    incumbent: LivePoint,
    candidate: LivePoint,
    *,
    xp: bool,
) -> tuple[float, ...]:
    if xp:
        xap_ratio = _ratio(
            float(candidate.xap_qps), float(incumbent.xap_qps), "xap ratio"
        )
        xtp_ratio = _ratio(
            float(candidate.xtp_tps), float(incumbent.xtp_tps), "xtp ratio"
        )
        return math.sqrt(xap_ratio * xtp_ratio), xap_ratio, xtp_ratio
    return _ratio(float(candidate.qps), float(incumbent.qps), "qps ratio"),


def _utility_against_reference(
    reference: LivePoint,
    candidate: LivePoint,
    *,
    bottleneck: str,
    xp: bool,
) -> tuple[float, float, float]:
    throughput_ratio = _throughput_ratios(reference, candidate, xp=xp)[0]
    p95_ratio = _ratio(candidate.latency_p95_ms, reference.latency_p95_ms, "p95 ratio")
    p99_ratio = _ratio(candidate.latency_p99_ms, reference.latency_p99_ms, "p99 ratio")
    resources = {
        "cpu": candidate.cpu_utilization,
        "memory": candidate.memory_utilization,
        "io": candidate.io_utilization,
    }
    if bottleneck == "none":
        utility = (
            math.log(throughput_ratio)
            - 2.0 * max(0.0, p95_ratio - 1.0)
            - 3.0 * max(0.0, p99_ratio - 1.0)
        )
        target_pressure = max(resources.values())
    else:
        reference_target = (
            reference.memory_utilization
            if bottleneck == "memory"
            else reference.io_utilization
        )
        if reference_target <= 0.0:
            raise FrozenAcceptanceError("default target utilization must be positive")
        target_pressure = resources[bottleneck]
        target_drop = (reference_target - target_pressure) / reference_target
        non_target_excess = sum(
            max(0.0, value - 0.8)
            for name, value in resources.items()
            if name != bottleneck
        )
        utility = (
            math.log(throughput_ratio)
            + target_drop
            - 2.0 * max(0.0, p95_ratio - 1.0)
            - 3.0 * max(0.0, p99_ratio - 1.0)
            - 4.0 * non_target_excess
        )
    return utility, throughput_ratio, target_pressure


def select_final_point(
    policy_path: Path,
    default: LivePoint,
    accepted_candidates: Sequence[EligibleFinalPoint],
    *,
    bottleneck: str,
    xp: bool,
) -> FinalSelection:
    """Select final using utility, throughput, pressure, then earlier index."""

    _strict_policy(policy_path)
    if bottleneck not in {"memory", "io", "none"}:
        raise FrozenAcceptanceError("bottleneck must be memory, io, or none")
    _validate_point(default, xp=xp, label="default")
    default_pressure = (
        max(default.cpu_utilization, default.memory_utilization, default.io_utilization)
        if bottleneck == "none"
        else default.memory_utilization
        if bottleneck == "memory"
        else default.io_utilization
    )
    labels = {"default"}
    indices = {0}
    rows: list[tuple[EligibleFinalPoint, float, float, float]] = [
        (EligibleFinalPoint("default", 0, default), 0.0, 1.0, default_pressure)
    ]
    for position, candidate in enumerate(accepted_candidates):
        if not isinstance(candidate, EligibleFinalPoint):
            raise FrozenAcceptanceError(f"eligible candidate {position} has the wrong type")
        if not candidate.label or candidate.label in labels:
            raise FrozenAcceptanceError("eligible final labels must be non-empty and unique")
        if type(candidate.candidate_index) is not int or candidate.candidate_index <= 0:
            raise FrozenAcceptanceError("eligible candidate index must be a positive integer")
        if candidate.candidate_index in indices:
            raise FrozenAcceptanceError("eligible candidate indices must be unique")
        labels.add(candidate.label)
        indices.add(candidate.candidate_index)
        _validate_point(candidate.point, xp=xp, label=candidate.label)
        utility, throughput_ratio, pressure = _utility_against_reference(
            default,
            candidate.point,
            bottleneck=bottleneck,
            xp=xp,
        )
        rows.append((candidate, utility, throughput_ratio, pressure))
    selected, utility, throughput_ratio, pressure = max(
        rows,
        key=lambda row: (row[1], row[2], -row[3], -row[0].candidate_index),
    )
    return FinalSelection(
        label=selected.label,
        candidate_index=selected.candidate_index,
        utility_from_default=utility,
        throughput_ratio_from_default=throughput_ratio,
        target_pressure=pressure,
        eligible_point_count=len(rows),
    )


def decide_outer_candidate(
    policy_path: Path,
    incumbent: LivePoint,
    candidate: LivePoint,
    *,
    bottleneck: str,
    xp: bool,
    plan_changed: bool,
) -> OuterDecision:
    """Evaluate one live candidate against the current live incumbent."""

    policy = _strict_policy(policy_path)
    if bottleneck not in {"memory", "io", "none"}:
        raise FrozenAcceptanceError("bottleneck must be memory, io, or none")
    _validate_point(incumbent, xp=xp, label="incumbent")
    _validate_point(candidate, xp=xp, label="candidate")

    throughput_values = _throughput_ratios(incumbent, candidate, xp=xp)
    throughput_ratio = throughput_values[0]

    hard = policy["common_hard_gates"]
    failures: list[str] = []
    if not candidate.correctness_passed:
        failures.append("correctness_failed")
    if not candidate.stable:
        failures.append("stability_failed")
    if candidate.sample_coverage < hard["sample_coverage_min"]:
        failures.append("sample_coverage_below_minimum")
    if candidate.error_rate > hard["error_rate_max"]:
        failures.append("error_rate_above_maximum")
    if candidate.oom:
        failures.append("oom")
    if candidate.database_restarted:
        failures.append("database_restarted")
    p95_ratio = _ratio(candidate.latency_p95_ms, incumbent.latency_p95_ms, "p95 ratio")
    p99_ratio = _ratio(candidate.latency_p99_ms, incumbent.latency_p99_ms, "p99 ratio")
    if p95_ratio > 1.0 + hard["p95_regression_max_ratio"]:
        failures.append("p95_regression_above_maximum")
    if p99_ratio > 1.0 + hard["p99_regression_max_ratio"]:
        failures.append("p99_regression_above_maximum")
    if xp:
        stream_floor = 1.0 - policy["throughput"]["xp_each_stream_regression_max_ratio"]
        if throughput_values[1] < stream_floor:
            failures.append("xap_regression_above_maximum")
        if throughput_values[2] < stream_floor:
            failures.append("xtp_regression_above_maximum")

    resources = {
        "cpu": candidate.cpu_utilization,
        "memory": candidate.memory_utilization,
        "io": candidate.io_utilization,
    }
    target_drop: float | None = None
    branch_throughput = False
    branch_resource = False
    if bottleneck == "none":
        utility = (
            math.log(throughput_ratio)
            - 2.0 * max(0.0, p95_ratio - 1.0)
            - 3.0 * max(0.0, p99_ratio - 1.0)
        )
        if any(value > policy["none_candidate"]["all_resource_utilization_max"] for value in resources.values()):
            failures.append("none_resource_utilization_above_maximum")
        branch_throughput = throughput_ratio >= (
            1.0 + policy["none_candidate"]["throughput_improvement_min_ratio"]
        )
        if not branch_throughput:
            failures.append("none_throughput_improvement_below_minimum")
        if utility < policy["none_candidate"]["utility_min_improvement"]:
            failures.append("utility_improvement_below_minimum")
    else:
        incumbent_target = (
            incumbent.memory_utilization if bottleneck == "memory" else incumbent.io_utilization
        )
        candidate_target = resources[bottleneck]
        if incumbent_target <= 0.0:
            raise FrozenAcceptanceError("incumbent target utilization must be positive")
        target_drop = (incumbent_target - candidate_target) / incumbent_target
        non_target = [value for name, value in resources.items() if name != bottleneck]
        non_target_excess = sum(max(0.0, value - 0.8) for value in non_target)
        utility = (
            math.log(throughput_ratio)
            + target_drop
            - 2.0 * max(0.0, p95_ratio - 1.0)
            - 3.0 * max(0.0, p99_ratio - 1.0)
            - 4.0 * non_target_excess
        )
        if any(
            value > policy["bottleneck_candidate"]["non_target_resource_utilization_max"]
            for value in non_target
        ):
            failures.append("non_target_resource_utilization_above_maximum")
        branch_throughput = (
            throughput_ratio
            >= 1.0
            + policy["bottleneck_candidate"]["branch_throughput"]["throughput_improvement_min_ratio"]
            and candidate_target / incumbent_target
            <= 1.0
            + policy["bottleneck_candidate"]["branch_throughput"]["target_pressure_increase_max_ratio"]
        )
        branch_resource = (
            target_drop
            >= policy["bottleneck_candidate"]["branch_resource"]["target_pressure_drop_min_ratio"]
            and throughput_ratio
            >= 1.0
            - policy["bottleneck_candidate"]["branch_resource"]["throughput_regression_max_ratio"]
        )
        if not (branch_throughput or branch_resource):
            failures.append("neither_bottleneck_acceptance_branch_passed")
        if utility < policy["bottleneck_candidate"]["utility_min_improvement"]:
            failures.append("utility_improvement_below_minimum")

    accepted = not failures
    if accepted and bottleneck == "none":
        reason = "none_throughput_and_utility_passed"
    elif accepted and branch_throughput and branch_resource:
        reason = "throughput_and_resource_branches_passed"
    elif accepted and branch_throughput:
        reason = "throughput_branch_passed"
    elif accepted:
        reason = "resource_branch_passed"
    else:
        reason = failures[0]
    return OuterDecision(
        accepted=accepted,
        reason=reason,
        utility_improvement=utility,
        throughput_ratio=throughput_ratio,
        target_pressure_drop_ratio=target_drop,
        branch_throughput_passed=branch_throughput,
        branch_resource_passed=branch_resource,
        hard_gate_failures=tuple(failures),
        plan_changed=bool(plan_changed),
    )
