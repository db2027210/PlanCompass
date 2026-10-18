#!/usr/bin/env python3
"""Single-scene measurement with the frozen client-primary XP objective.

The physical measurement remains exactly the v2 implementation.  For native
HyBench XP only, this adapter exposes client XAP QPS through the historical
``db_qps`` field consumed by the B/C outer loops.  MySQL ``Questions`` QPS is
retained as an audit metric and is never used for XP candidate acceptance or
ranking.  JOB and TPC-H metric semantics are unchanged.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Mapping

from scripts import mysql_single_scene_measurement_v2 as predecessor


XP_SCENES = {"hybench_xp_cpu", "hybench_xp_io"}
POLICY_ID = "mysql_xp_client_xap_qps_outer_primary_v1"


def _finite_positive(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise RuntimeError(f"{label} must be numeric") from error
    if not math.isfinite(number) or number <= 0.0:
        raise RuntimeError(f"{label} must be finite and positive")
    return number


def _promote_xp_client_primary(row: dict[str, Any]) -> None:
    server_questions_qps = _finite_positive(row.get("db_qps"), "server Questions QPS")
    workload = row.get("workload_result")
    if not isinstance(workload, Mapping):
        raise RuntimeError("XP workload_result object required")
    formal = workload.get("formal_metrics")
    if not isinstance(formal, Mapping):
        raise RuntimeError("XP formal_metrics object required")
    xap_qps = _finite_positive(formal.get("xap_qps"), "client XAP QPS")
    legacy_client = _finite_positive(row.get("client_qps_audit"), "legacy client QPS")
    if not math.isclose(xap_qps, legacy_client, rel_tol=1e-9, abs_tol=1e-12):
        raise RuntimeError("XP client_qps_audit differs from formal_metrics.xap_qps")

    row["server_questions_qps_audit"] = server_questions_qps
    row["client_xap_qps"] = xap_qps
    row["db_qps"] = xap_qps
    row["outer_primary_qps"] = xap_qps
    row["outer_primary_metric"] = "native_hybench_xp_client_xap_qps"
    row["outer_primary_metric_policy"] = POLICY_ID
    checks = row.get("hard_gate_checks")
    if isinstance(checks, dict):
        checks["outer_primary_client_xap_qps_positive"] = True
        checks["server_questions_qps_audit_positive"] = True


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
    for row in measured.values():
        if str(row.get("scene")) in XP_SCENES:
            _promote_xp_client_primary(row)

    batch_path = output / "batch_result.json"
    document = predecessor.alg.load(batch_path)
    rows = document.get("measurements")
    if not isinstance(rows, dict) or set(rows) != set(measured):
        raise RuntimeError("persisted batch measurements differ from returned rows")
    for worker_id, row in measured.items():
        rows[worker_id] = row
    document.update({
        "schema_version": "mysql_single_scene_client_primary_batch_v1",
        "xp_outer_primary_metric": "native_hybench_xp_client_xap_qps",
        "xp_server_questions_role": "audit_only",
        "xp_metric_policy": POLICY_ID,
        "job_tpch_metric_semantics_changed": False,
    })
    predecessor.alg.atomic(batch_path, document)
    return measured


__all__ = ["POLICY_ID", "XP_SCENES", "measure_batch"]
