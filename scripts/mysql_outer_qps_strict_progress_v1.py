#!/usr/bin/env python3
"""QPS-only semantic gate for MySQL Algorithm B and Algorithm C.

The inner loop continues to generate and rank candidates exactly as frozen by
each algorithm.  This module changes only the outer semantic gate: a valid
real measurement advances the outer loop if and only if its DB QPS is
strictly greater than the current incumbent (the most recently accepted
point, or the canonical baseline before the first acceptance).

Latency and resource-utilization values remain recorded for analysis.  They
do not veto an otherwise QPS-improving point.  Technical measurement validity
is not an optimization objective and remains fail closed.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

from common_outer_loop import AcceptanceTierProgress, OuterAcceptanceDecision


POLICY_ID = "mysql_outer_qps_strict_progress_v1"
ACCEPTANCE_MODE = "qps_strictly_higher_than_last_accepted"


def policy_contract() -> dict[str, Any]:
    return {
        "schema_version": POLICY_ID,
        "outer_acceptance_mode": ACCEPTANCE_MODE,
        "qps_reference": "last_accepted_incumbent",
        "first_reference": "canonical_baseline",
        "comparison": "candidate_db_qps > incumbent_db_qps",
        "minimum_gain_ratio": 0.0,
        "latency_gate_affects_acceptance": False,
        "target_resource_drop_affects_acceptance": False,
        "non_target_resource_gate_affects_acceptance": False,
        "acceptance_tier_affects_acceptance": False,
        "technical_measurement_validity_required": True,
        "rejected_round_changes_incumbent": False,
        "algorithm_a_changed": False,
    }


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def decide_qps_strict_progress(
    incumbent: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    target_resource: str,
    hard_gate_passed: bool,
    db_qps_key: str = "db_qps",
    utilization_key: str = "utilization_pct",
    non_target_bottleneck_threshold: float = 0.8,
    acceptance_tier: str = ACCEPTANCE_MODE,
) -> OuterAcceptanceDecision:
    """Accept exactly valid candidates whose QPS exceeds the incumbent.

    ``hard_gate_passed`` from older callers may include latency or resource
    policy gates.  Those gates are intentionally ignored.  The candidate's
    own ``hard_gate_passed`` field is the technical validity/read-back gate.
    """

    del hard_gate_passed, acceptance_tier
    incumbent_qps = _finite(incumbent.get(db_qps_key))
    candidate_qps = _finite(candidate.get(db_qps_key))
    incumbent_util = incumbent.get(utilization_key)
    candidate_util = candidate.get(utilization_key)
    incumbent_resources = (
        dict(incumbent_util) if isinstance(incumbent_util, Mapping) else {}
    )
    candidate_resources = (
        dict(candidate_util) if isinstance(candidate_util, Mapping) else {}
    )
    target = str(target_resource).strip().lower()
    technical_valid = bool(
        candidate.get("hard_gate_passed") is True
        and candidate_qps is not None
        and candidate_qps > 0.0
        and incumbent_qps is not None
        and incumbent_qps > 0.0
    )
    qps_up = bool(
        technical_valid
        and candidate_qps is not None
        and incumbent_qps is not None
        and candidate_qps > incumbent_qps
    )

    def resource(resources: Mapping[str, Any], name: str) -> float | None:
        return _finite(resources.get(name))

    non_targets = [name for name in ("cpu", "memory", "io") if name != target]
    incumbent_non_target = {
        name: value for name in non_targets
        if (value := resource(incumbent_resources, name)) is not None
    }
    candidate_non_target = {
        name: value for name in non_targets
        if (value := resource(candidate_resources, name)) is not None
    }
    incumbent_target = resource(incumbent_resources, target)
    candidate_target = resource(candidate_resources, target)
    target_down = (
        candidate_target < incumbent_target
        if candidate_target is not None and incumbent_target is not None
        else None
    )
    all_non_target_up = (
        all(
            name in incumbent_non_target
            and name in candidate_non_target
            and candidate_non_target[name] > incumbent_non_target[name]
            for name in non_targets
        )
        if non_targets else None
    )

    return OuterAcceptanceDecision(
        accepted=qps_up,
        reason=(
            "accepted_qps_strictly_higher_than_last_accepted"
            if qps_up else
            "technical_measurement_invalid"
            if not technical_valid else
            "db_qps_not_strictly_higher"
        ),
        hard_gate_passed=technical_valid,
        incumbent_db_qps=incumbent_qps,
        candidate_db_qps=candidate_qps,
        incumbent_target_utilization=incumbent_target,
        candidate_target_utilization=candidate_target,
        target_resource=target,
        non_target_bottleneck_threshold=non_target_bottleneck_threshold,
        incumbent_non_target_utilization=incumbent_non_target,
        candidate_non_target_utilization=candidate_non_target,
        non_target_bottleneck_resources=[],
        incumbent_non_target_bottlenecks=[],
        candidate_non_target_bottlenecks=[],
        required_acceptance_tier=ACCEPTANCE_MODE,
        required_acceptance_tier_rank=1,
        satisfied_acceptance_tier=(ACCEPTANCE_MODE if qps_up else None),
        satisfied_acceptance_tier_rank=(1 if qps_up else 0),
        qps_strictly_increased=qps_up,
        target_bottleneck_strictly_decreased=target_down,
        all_non_target_resources_strictly_increased=all_non_target_up,
    )


def pg_outer_qps_strict_progress(
    candidate: Mapping[str, Any],
    reference: Mapping[str, Any],
    *,
    canonical_baseline: Mapping[str, Any] | None = None,
    target: str,
) -> dict[str, Any]:
    """Adapter for the newer PG-shaped Algorithm-C outer runtime."""

    del canonical_baseline
    decision = decide_qps_strict_progress(
        reference,
        candidate,
        target_resource=target,
        hard_gate_passed=bool(candidate.get("hard_gate_passed")),
        utilization_key="resource_means",
    ).to_dict()
    incumbent_qps = _finite(reference.get("db_qps"))
    candidate_qps = _finite(candidate.get("db_qps"))
    qps_gain = (
        candidate_qps / incumbent_qps - 1.0
        if candidate_qps is not None
        and incumbent_qps is not None
        and incumbent_qps > 0.0
        else -math.inf
    )
    incumbent_resources = reference.get("resource_means") or {}
    candidate_resources = candidate.get("resource_means") or {}
    incumbent_target = _finite(
        incumbent_resources.get(target)
        if isinstance(incumbent_resources, Mapping) else None
    )
    candidate_target = _finite(
        candidate_resources.get(target)
        if isinstance(candidate_resources, Mapping) else None
    )
    target_drop = (
        (incumbent_target - candidate_target) / incumbent_target
        if incumbent_target is not None
        and candidate_target is not None
        and incumbent_target > 0.0
        else -math.inf
    )
    decision.update({
        "acceptance_tier": (ACCEPTANCE_MODE if decision["accepted"] else None),
        "acceptance_tier_rank": (1 if decision["accepted"] else 0),
        "qps_gate_gain": qps_gain,
        "target_drop_vs_reference": target_drop,
        "non_target_up_resources": [],
        "non_target_io_up_vs_reference": 0.0,
        "qps_reference_mode": "last_accepted",
        "qps_threshold_mode": "strict_greater_no_minimum_ratio",
        "minimum_db_qps_gain_ratio": 0.0,
        "minimum_target_drop_ratio": None,
        "qps_reference_db_qps": incumbent_qps,
        "canonical_baseline_db_qps": None,
        "last_accepted_db_qps": incumbent_qps,
        "accepted_shrink": decision["accepted"],
        "accepted_escape": False,
    })
    return decision


def keep_qps_only_mode(
    *,
    acceptance_tier: str,
    consecutive_failed_rounds: int,
    accepted: bool,
    downgrade_after_rounds: int = 2,
) -> AcceptanceTierProgress:
    """Persist an audit-only tier record without changing QPS semantics."""

    del acceptance_tier, consecutive_failed_rounds
    return AcceptanceTierProgress(
        acceptance_tier=ACCEPTANCE_MODE,
        consecutive_failed_rounds=(0 if accepted else 1),
        downgraded=False,
        downgraded_from=None,
        downgraded_to=None,
        downgrade_after_rounds=downgrade_after_rounds,
    )


__all__ = [
    "ACCEPTANCE_MODE",
    "POLICY_ID",
    "decide_qps_strict_progress",
    "pg_outer_qps_strict_progress",
    "keep_qps_only_mode",
    "policy_contract",
]
