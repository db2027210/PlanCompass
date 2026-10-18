#!/usr/bin/env python3
"""Client-primary Algorithm-C adapter that supports paired memory batches.

The physical measurement producer may return JOB and TPC-H rows together.
Only the TPC-H subset is rebound and normalized; JOB rows retain their native
client-QPS semantics.  Single-scene XP and TPC-H batches remain supported.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping

from scripts import mysql_algorithm_c_measurement_client_primary_v1 as predecessor
from scripts import mysql_tpch_client_qps_measurement_v2 as tpch_metric
from scripts import mysql_tpch_worker_qps_normalization_v1 as normalization


POLICY_ID = "mysql_xp_tpch_and_memory_pair_client_primary_outer_v3"
_NORMALIZATION: dict[str, Any] = {}


def _valid(row: Mapping[str, Any]) -> bool:
    return (
        row.get("hard_gate_passed") is True
        and row.get("coverage_complete") is True
        and float(row.get("db_qps") or 0.0) > 0.0
    )


def measure_batch(
    freeze: Mapping[str, Any], workers: dict[str, dict[str, Any]],
    assignments: Mapping[str, Mapping[str, Any]], output: Path,
    reset_token: str, *, state_path: Path | None = None,
    stage_prefix: str = "FORMAL_POINT",
) -> dict[str, dict[str, Any]]:
    measured = predecessor.measure_batch(
        freeze, workers, assignments, output, reset_token,
        state_path=state_path, stage_prefix=stage_prefix,
    )
    tpch_rows = {
        worker_id: copy.deepcopy(dict(row))
        for worker_id, row in measured.items()
        if row.get("scene") == tpch_metric.SCENE
    }
    if not tpch_rows:
        return measured

    # A formal TPC-H scene always has exactly two workers, whether it is run
    # alone or inside the JOB/TPC-H memory-pair controller.
    if len(tpch_rows) != 2:
        raise RuntimeError("TPC-H client-QPS adapter requires two TPC-H rows")
    rebound = tpch_metric.rebind_measurements(tpch_rows)
    baseline = output.name == "formal_incumbent_baseline"
    if baseline:
        _NORMALIZATION.clear()
        _NORMALIZATION.update(normalization.baseline_contract(
            {"status": "PASS", "failures": [], "measurements": rebound},
            scene=tpch_metric.SCENE,
        ))
    if not _NORMALIZATION:
        raise RuntimeError("TPC-H C fresh baseline normalization is absent")

    for row in rebound.values():
        row["eligible_for_outer_acceptance"] = _valid(row)
        row["accepted_state_credit_increment"] = 0
        if baseline:
            continue
        if _valid(row):
            normalization.normalize_candidate_in_place(row, _NORMALIZATION)
        else:
            row["formally_infeasible_candidate"] = True
            row["infeasible_row_normalized"] = False
    measured.update(rebound)

    batch_path = output / "batch_result.json"
    document = copy.deepcopy(dict(predecessor.predecessor.load(batch_path)))
    if set(document.get("measurements") or {}) != set(measured):
        raise RuntimeError("client-primary batch artifact/return worker sets differ")
    document["measurements"] = copy.deepcopy(measured)
    document.update({
        "schema_version": "mysql_algorithm_c_client_primary_batch_v3",
        "qps_metric": "per_scene_client_primary",
        "tpch_qps_metric": tpch_metric.METRIC,
        "qps_metric_schema_version": tpch_metric.SCHEMA_VERSION,
        "server_db_qps_preserved_as_audit": True,
        "client_qps_controls_outer_acceptance": True,
        "same_worker_qps_normalization": copy.deepcopy(_NORMALIZATION),
        "same_worker_qps_normalization_applied": not baseline,
        "valid_rows_explicitly_eligible": True,
        "hard_gate_failed_rows_classified_infeasible": True,
        "mixed_memory_scene_batch_supported": True,
        "non_tpch_rows_modified": False,
    })
    predecessor.predecessor.atomic(batch_path, document)

    if baseline:
        audit_path = output / "same_worker_qps_normalization.json"
        predecessor.predecessor.atomic(audit_path, {
            **copy.deepcopy(_NORMALIZATION),
            "status": "PASS",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "baseline_batch_path": str(batch_path),
            "baseline_batch_sha256": predecessor.predecessor.sha(batch_path),
            "memory_pair_batch": len(measured) > len(rebound),
        })
        if state_path is not None:
            state = copy.deepcopy(dict(predecessor.predecessor.load(state_path)))
            state.update({
                "qps_metric_contract": tpch_metric.contract(),
                "same_worker_qps_normalization": copy.deepcopy(_NORMALIZATION),
                "same_worker_qps_normalization_path": str(audit_path),
                "same_worker_qps_normalization_sha256": (
                    predecessor.predecessor.sha(audit_path)
                ),
                "outer_acceptance_uses_same_worker_normalized_client_qps": True,
                "server_questions_qps_role": "audit_only",
                "mixed_memory_scene_batch_supported": True,
            })
            predecessor.predecessor.atomic(state_path, state)
    return measured


def normalization_contract() -> dict[str, Any]:
    return copy.deepcopy(_NORMALIZATION)


__all__ = ["POLICY_ID", "measure_batch", "normalization_contract"]
