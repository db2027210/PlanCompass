#!/usr/bin/env python3
"""Classify only max-duration XP resource instability as candidate-local."""

from __future__ import annotations

from typing import Any, Mapping


def classify(
    document: Mapping[str, Any],
    assignments: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any] | None:
    failures = document.get("failures")
    measurements = document.get("measurements")
    if (
        document.get("status") != "FAILED_CLOSED"
        or failures not in ([], None)
        or not isinstance(measurements, Mapping)
        or set(measurements) != set(assignments)
    ):
        return None
    invalid: list[str] = []
    for worker_id, raw in measurements.items():
        if not isinstance(raw, Mapping):
            return None
        row = dict(raw)
        if row.get("hard_gate_passed") is True:
            if row.get("coverage_complete") is not True:
                return None
            continue
        checks = dict(row.get("hard_gate_checks") or {})
        failed_checks = {name for name, value in checks.items() if value is False}
        stability = dict(row.get("resource_stability") or {})
        if (
            row.get("scene") not in {"hybench_xp_cpu", "hybench_xp_io"}
            or row.get("coverage_complete") is not True
            or failed_checks != {"target_resource_stability_passed"}
            or stability.get("schema_version") != "mysql_xp_adaptive_stop_v1"
            or stability.get("stable") is not False
            or float(stability.get("selected_measurement_seconds") or 0) != 900.0
            or float(stability.get("maximum_measurement_seconds") or 0) != 900.0
            or stability.get("stop_request_published") is not False
        ):
            return None
        invalid.append(str(worker_id))
    if not invalid:
        return None
    return {
        "classification": "TECHNICAL_XP_TARGET_RESOURCE_INSTABILITY_EXCLUDED",
        "timed_out_tasks": [],
        "terminal_drain_tasks": [],
        "direct_failed_worker_ids": sorted(invalid),
        "derived_unavailable_worker_ids": [],
        "failed_worker_ids": sorted(invalid),
        "derived_plan_capture_skips": [],
        "maximum_measurement_seconds": 900,
        "candidate_state_mutation": False,
        "accepted_state_credit_increment": 0,
        "rejected_outer_attempt_increment": 0,
        "acceptance_tier_mutation": False,
        "convergence_mutation": False,
        "replay_history_mutation": False,
        "pruning_evidence_eligible": False,
    }

