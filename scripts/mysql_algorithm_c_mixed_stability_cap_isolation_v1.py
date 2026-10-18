#!/usr/bin/env python3
"""Isolate only stability-capped lanes in a mixed Algorithm-C wave."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping


Classifier = Callable[..., dict[str, Any] | None]


def classify(
    document: Mapping[str, Any],
    assignments: Mapping[str, Mapping[str, Any]],
    *,
    batch_result_path: Path | None = None,
    predecessor: Classifier,
) -> dict[str, Any] | None:
    """Retain valid siblings and exclude only exact 900-second cap failures.

    The predecessor already recognizes workload timeouts and terminal XP drain
    failures.  This successor handles a FAILED_CLOSED wave whose failure list
    is empty because one or more lanes reached the frozen resource-stability
    cap while the remaining lanes completed normally.
    """
    prior = predecessor(
        document,
        assignments,
        batch_result_path=batch_result_path,
    )
    if prior is not None:
        return prior

    failures = document.get("failures")
    measurements = document.get("measurements")
    if (
        document.get("status") != "FAILED_CLOSED"
        or failures not in ([], None)
        or not isinstance(measurements, Mapping)
        or set(measurements) != set(assignments)
    ):
        return None

    capped: list[str] = []
    retained: list[str] = []
    for worker_id, raw in measurements.items():
        if not isinstance(raw, Mapping):
            return None
        row = dict(raw)
        if (
            row.get("hard_gate_passed") is True
            and row.get("coverage_complete") is True
        ):
            retained.append(str(worker_id))
            continue
        checks = row.get("hard_gate_checks")
        stability = row.get("resource_stability")
        if not isinstance(checks, Mapping) or not isinstance(stability, Mapping):
            return None
        try:
            selected = float(stability.get("selected_measurement_seconds") or 0)
            maximum = float(stability.get("maximum_measurement_seconds") or 0)
        except (TypeError, ValueError):
            return None
        if not (
            row.get("coverage_complete") is True
            and row.get("hard_gate_passed") is False
            and checks.get("target_resource_stability_passed") is False
            and all(
                value is True
                for name, value in checks.items()
                if name != "target_resource_stability_passed"
            )
            and stability.get("stable") is False
            and selected == maximum == 900.0
        ):
            return None
        capped.append(str(worker_id))

    if not capped:
        return None
    return {
        "classification": "CANDIDATE_STABILITY_CAP_EXCLUDED",
        "timed_out_tasks": [],
        "terminal_drain_tasks": [],
        "direct_failed_worker_ids": sorted(capped),
        "derived_unavailable_worker_ids": [],
        "failed_worker_ids": sorted(capped),
        "derived_plan_capture_skips": [],
        "retained_valid_worker_ids": sorted(retained),
        "valid_sibling_measurements_retained": bool(retained),
        "mixed_valid_and_stability_capped_wave_supported": True,
        "candidate_state_mutation": False,
        "accepted_state_credit_increment": 0,
        "rejected_outer_attempt_increment": 0,
        "acceptance_tier_mutation": False,
        "convergence_mutation": False,
        "replay_history_mutation": False,
        "pruning_evidence_eligible": False,
    }
