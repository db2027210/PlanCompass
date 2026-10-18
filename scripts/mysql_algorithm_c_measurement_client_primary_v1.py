#!/usr/bin/env python3
"""Algorithm-C measurement adapter with client XAP QPS as XP objective."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Mapping

from scripts import run_mysql_four_scene_algorithm_c_v2 as predecessor


XP_SCENES = {"hybench_xp_cpu", "hybench_xp_io"}
POLICY_ID = "mysql_xp_client_xap_qps_outer_primary_v1"
_ORIGINAL_MEASURE_BATCH = predecessor.measure_batch


def finite_positive(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise RuntimeError(f"{label} must be numeric") from error
    if not math.isfinite(number) or number <= 0.0:
        raise RuntimeError(f"{label} must be finite and positive")
    return number


def promote(row: dict[str, Any]) -> None:
    server = finite_positive(row.get("db_qps"), "server Questions QPS")
    workload = row.get("workload_result")
    if not isinstance(workload, Mapping):
        raise RuntimeError("XP workload_result object required")
    formal = workload.get("formal_metrics")
    if not isinstance(formal, Mapping):
        raise RuntimeError("XP formal_metrics object required")
    xap = finite_positive(formal.get("xap_qps"), "client XAP QPS")
    legacy = finite_positive(row.get("client_qps_audit"), "legacy client QPS")
    if not math.isclose(xap, legacy, rel_tol=1e-9, abs_tol=1e-12):
        raise RuntimeError("XP client_qps_audit differs from formal XAP QPS")
    row.update({
        "server_questions_qps_audit": server,
        "client_xap_qps": xap,
        "db_qps": xap,
        "outer_primary_qps": xap,
        "outer_primary_metric": "native_hybench_xp_client_xap_qps",
        "outer_primary_metric_policy": POLICY_ID,
    })
    checks = row.get("hard_gate_checks")
    if isinstance(checks, dict):
        checks.update({
            "outer_primary_client_xap_qps_positive": True,
            "server_questions_qps_audit_positive": True,
        })


def measure_batch(
    freeze: Mapping[str, Any], workers: dict[str, dict[str, Any]],
    assignments: Mapping[str, Mapping[str, Any]], output: Path,
    reset_token: str, *, state_path: Path | None = None,
    stage_prefix: str = "FORMAL_POINT",
) -> dict[str, dict[str, Any]]:
    measured = _ORIGINAL_MEASURE_BATCH(
        freeze, workers, assignments, output, reset_token,
        state_path=state_path, stage_prefix=stage_prefix,
    )
    for row in measured.values():
        if str(row.get("scene")) in XP_SCENES:
            promote(row)
    batch_path = output / "batch_result.json"
    document = predecessor.load(batch_path)
    rows = document.get("measurements")
    if not isinstance(rows, dict) or set(rows) != set(measured):
        raise RuntimeError("persisted Algorithm-C measurements differ")
    document["measurements"] = measured
    document.update({
        "schema_version": "mysql_algorithm_c_client_primary_batch_v1",
        "xp_outer_primary_metric": "native_hybench_xp_client_xap_qps",
        "xp_server_questions_role": "audit_only",
        "xp_metric_policy": POLICY_ID,
        "job_tpch_metric_semantics_changed": False,
    })
    predecessor.atomic(batch_path, document)
    return measured


__all__ = ["POLICY_ID", "XP_SCENES", "measure_batch"]
