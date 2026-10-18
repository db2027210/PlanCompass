#!/usr/bin/env python3
"""JOB v11 measurement with per-suite elapsed-time validity read-back.

The four JOB suites are intentionally staggered.  The relational completion
gate in the shared adapter measures from the beginning of the whole batch, so
workers two through four otherwise inherit their configured start offset.
This adapter reconciles that diagnostic clock with the already-authoritative
per-suite deadline evidence emitted by v10.  It never rescues an incomplete or
deadline-triggered suite.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import re
from typing import Any, Mapping

from scripts import mysql_job_buffer_pool_preconditioned_measurement_v11 as v11


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = (
    ROOT / "config/formal/frozen_mysql_job_cache_state_20260824_v12"
    / "cache_state_contract.json"
)

alg = v11.alg
legacy = v11.legacy
PRIMER_PARAMS = v11.PRIMER_PARAMS
SCORED_SUITE_TIMEOUT_SECONDS = v11.SCORED_SUITE_TIMEOUT_SECONDS
WORKER_START_STAGGER_SECONDS = v11.WORKER_START_STAGGER_SECONDS


def _validate_contract() -> dict[str, Any]:
    if CONTRACT_PATH.is_symlink() or not CONTRACT_PATH.is_file():
        raise RuntimeError(f"JOB buffer-pool v12 contract absent: {CONTRACT_PATH}")
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    if (
        contract.get("schema_version") != "mysql_job_cache_state_contract_v12"
        or contract.get("release_version") != "v12"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("scene") != "job_memory"
        or contract.get("per_suite_hard_timeout_seconds") != 900
        or contract.get("per_suite_deadline_begins_after_start_offset") is not True
        or contract.get("batch_start_offset_excluded_from_suite_elapsed") is not True
        or contract.get("deadline_triggered_suite_can_be_reclassified_valid") is not False
        or contract.get("incomplete_suite_can_be_reclassified_valid") is not False
        or contract.get("candidate_set_changed") is not False
        or contract.get("outer_loop_semantics_changed") is not False
        or contract.get("resource_envelope_changed") is not False
    ):
        raise RuntimeError("JOB buffer-pool v12 contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        source = ROOT / str(binding.get("path") or "")
        if (
            source.is_symlink() or not source.is_file()
            or legacy._sha(source) != binding.get("sha256")
        ):
            raise RuntimeError(f"JOB buffer-pool v12 source differs: {source}")
    v11._validate_contract()
    return contract


def _worker_for_deadline(record: Mapping[str, Any]) -> str | None:
    match = re.search(r"/pair_([1-4])/job/?$", str(record.get("output_dir") or ""))
    return f"job_w{match.group(1)}" if match else None


def reconcile_document(document: Mapping[str, Any]) -> dict[str, Any]:
    """Return a copy using each suite's own v10 monotonic deadline clock."""
    corrected = copy.deepcopy(dict(document))
    measurements = corrected.get("measurements")
    deadline_block = corrected.get("scored_suite_hard_timeout")
    if not isinstance(measurements, Mapping) or not isinstance(deadline_block, Mapping):
        return corrected
    deadline_rows = deadline_block.get("worker_suites")
    if not isinstance(deadline_rows, list):
        return corrected
    by_worker = {
        worker_id: row for row in deadline_rows
        if isinstance(row, Mapping)
        and (worker_id := _worker_for_deadline(row)) is not None
    }
    corrections: list[dict[str, Any]] = []
    for worker_id, raw in list(measurements.items()):
        if not isinstance(raw, Mapping):
            continue
        row = copy.deepcopy(dict(raw))
        deadline = by_worker.get(str(worker_id))
        stability = row.get("resource_stability")
        checks = row.get("hard_gate_checks")
        workload = row.get("workload_result")
        coverage = workload.get("query_coverage") if isinstance(workload, Mapping) else None
        if (
            not isinstance(deadline, Mapping)
            or not isinstance(stability, Mapping)
            or not isinstance(checks, Mapping)
            or not isinstance(workload, Mapping)
            or not isinstance(coverage, Mapping)
        ):
            continue
        elapsed = float(deadline.get("elapsed_seconds") or 0.0)
        complete = (
            row.get("coverage_complete") is True
            and coverage.get("coverage_complete") is True
            and int(workload.get("query_count") or 0) == 113
            and len(coverage.get("completed_queries") or []) == 113
            and not (coverage.get("missing_queries") or [])
        )
        deadline_ok = (
            deadline.get("deadline_triggered") is False
            and 0.0 < elapsed <= SCORED_SUITE_TIMEOUT_SECONDS
        )
        other_checks_pass = all(
            value is True for name, value in checks.items()
            if name != "target_resource_stability_passed"
        )
        if not (complete and deadline_ok and other_checks_pass):
            continue
        previous_elapsed = stability.get("elapsed_seconds")
        stable = copy.deepcopy(dict(stability))
        stable.update({
            "elapsed_seconds": elapsed,
            "checkpoint_seconds_diagnostic": min(
                int(SCORED_SUITE_TIMEOUT_SECONDS), int(elapsed // 30) * 30,
            ),
            "within_hard_timeout": True,
            "stable": True,
            "reason": "full_suite_completed_within_hard_timeout",
            "elapsed_clock_scope": "individual_scored_job113_suite",
            "configured_batch_start_offset_excluded": True,
        })
        gate_checks = copy.deepcopy(dict(checks))
        gate_checks["target_resource_stability_passed"] = True
        row["resource_stability"] = stable
        row["hard_gate_checks"] = gate_checks
        row["hard_gate_passed"] = all(value is True for value in gate_checks.values())
        row["job_per_suite_timer_reconciliation"] = {
            "schema_version": "mysql_job_per_suite_timer_reconciliation_v1",
            "previous_batch_elapsed_seconds": previous_elapsed,
            "per_suite_elapsed_seconds": elapsed,
            "deadline_triggered": False,
            "full_job113_coverage_verified": True,
            "only_target_resource_stability_check_changed": True,
        }
        measurements[worker_id] = row
        corrections.append({
            "worker_id": worker_id,
            "rank": row.get("rank"),
            "previous_batch_elapsed_seconds": previous_elapsed,
            "per_suite_elapsed_seconds": elapsed,
            "hard_gate_passed": row["hard_gate_passed"],
        })
    corrected["measurements"] = measurements
    corrected["job_per_suite_timer_reconciliation"] = {
        "schema_version": "mysql_job_per_suite_timer_reconciliation_v1",
        "contract_path": str(CONTRACT_PATH),
        "contract_sha256": (
            legacy._sha(CONTRACT_PATH) if CONTRACT_PATH.is_file() else None
        ),
        "batch_start_offset_excluded_from_suite_elapsed": True,
        "deadline_triggered_suite_can_be_reclassified_valid": False,
        "incomplete_suite_can_be_reclassified_valid": False,
        "corrections": corrections,
    }
    return corrected


def measure_batch(
    freeze: Mapping[str, Any], workers: dict[str, dict[str, Any]],
    assignments: Mapping[str, Mapping[str, Any]], output: Path,
    reset_token: str, *, state_path: Path | None = None,
    stage_prefix: str = "FORMAL_POINT",
) -> dict[str, dict[str, Any]]:
    _validate_contract()
    v11.measure_batch(
        freeze, workers, assignments, output, reset_token,
        state_path=state_path, stage_prefix=stage_prefix,
    )
    batch_path = output / "batch_result.json"
    document = reconcile_document(alg.load(batch_path))
    alg.atomic(batch_path, document)
    return copy.deepcopy(dict(document["measurements"]))


__all__ = [
    "CONTRACT_PATH", "PRIMER_PARAMS", "SCORED_SUITE_TIMEOUT_SECONDS",
    "WORKER_START_STAGGER_SECONDS", "measure_batch", "reconcile_document",
]
