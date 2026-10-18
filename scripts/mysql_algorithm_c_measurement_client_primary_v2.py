#!/usr/bin/env python3
"""Algorithm-C client-primary adapter for XP and full-suite TPC-H.

XP keeps native XAP QPS as the outer objective.  TPC-H exposes fixed Q1--Q22
client QPS through the common outer-QPS field and normalizes candidate values
to the fresh two-worker baseline cohort.  Server Questions QPS remains audit
telemetry in both cases.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping, MutableMapping

from scripts import mysql_algorithm_c_measurement_client_primary_v1 as predecessor
from scripts import mysql_tpch_client_qps_measurement_v2 as tpch_metric
from scripts import mysql_tpch_worker_qps_normalization_v1 as normalization


POLICY_ID = "mysql_xp_and_tpch_client_primary_outer_v2"
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
    scenes = {str(row.get("scene") or "") for row in measured.values()}
    if scenes == {tpch_metric.SCENE}:
        measured = tpch_metric.rebind_measurements(measured)
        baseline = output.name == "formal_incumbent_baseline"
        if baseline:
            _NORMALIZATION.clear()
            _NORMALIZATION.update(normalization.baseline_contract(
                {
                    "status": "PASS",
                    "failures": [],
                    "measurements": measured,
                },
                scene=tpch_metric.SCENE,
            ))
        if not _NORMALIZATION:
            raise RuntimeError("TPC-H C fresh baseline normalization is absent")
        for row in measured.values():
            row["eligible_for_outer_acceptance"] = _valid(row)
            row["accepted_state_credit_increment"] = 0
            if baseline:
                continue
            if _valid(row):
                normalization.normalize_candidate_in_place(row, _NORMALIZATION)
            else:
                row["formally_infeasible_candidate"] = True
                row["infeasible_row_normalized"] = False

        batch_path = output / "batch_result.json"
        document = copy.deepcopy(dict(predecessor.predecessor.load(batch_path)))
        document["measurements"] = copy.deepcopy(measured)
        document.update({
            "schema_version": "mysql_algorithm_c_client_primary_batch_v2",
            "qps_metric": tpch_metric.METRIC,
            "qps_metric_schema_version": tpch_metric.SCHEMA_VERSION,
            "server_db_qps_preserved_as_audit": True,
            "client_qps_controls_outer_acceptance": True,
            "same_worker_qps_normalization": copy.deepcopy(_NORMALIZATION),
            "same_worker_qps_normalization_applied": not baseline,
            "valid_rows_explicitly_eligible": True,
            "hard_gate_failed_rows_classified_infeasible": True,
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
                })
                predecessor.predecessor.atomic(state_path, state)
    elif tpch_metric.SCENE in scenes:
        raise RuntimeError("TPC-H client-QPS adapter received a mixed-scene batch")
    return measured


def normalization_contract() -> dict[str, Any]:
    return copy.deepcopy(_NORMALIZATION)


__all__ = ["POLICY_ID", "measure_batch", "normalization_contract"]
