#!/usr/bin/env python3
"""Bind TPC-H outer progress to full-suite client throughput.

MySQL ``Questions`` deltas are useful server-side telemetry, but they are not
the throughput of the frozen Q1--Q22 suite: changing an optimizer cost can
change the number of server statements counted without completing the suite
faster.  Formal TPC-H search therefore preserves that counter as audit data
and exposes the fixed five-lane client QPS through the legacy ``db_qps`` slot
consumed by the common B/C outer controller.

The adapter is deliberately TPC-H-only and fails closed for mixed-scene
batches.  JOB and native HyBench XP retain their existing measurement
semantics.
"""

from __future__ import annotations

import copy
import math
from pathlib import Path
from typing import Any, Callable, Mapping


SCENE = "tpch_sf4_memory"
SCHEMA_VERSION = "mysql_tpch_full_suite_client_qps_acceptance_v1"
METRIC = "fixed_five_lane_q1_q22_client_qps"


def _positive_finite(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise RuntimeError(f"{label} must be numeric") from error
    if not math.isfinite(number) or number <= 0.0:
        raise RuntimeError(f"{label} must be finite and positive")
    return number


def rebind_measurements(
    measurements: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Return an audit-preserving client-throughput view of a TPC-H batch."""
    if not measurements:
        raise RuntimeError("TPC-H client-QPS binding requires measurements")
    rebound: dict[str, dict[str, Any]] = {}
    for worker_id, raw in measurements.items():
        row = copy.deepcopy(dict(raw))
        if row.get("scene") != SCENE:
            raise RuntimeError(
                "TPC-H client-QPS binding received a non-TPC-H measurement"
            )
        server_qps = _positive_finite(row.get("db_qps"), "server db_qps")
        client_qps = _positive_finite(
            row.get("client_qps_audit"), "full-suite client QPS",
        )
        coverage = row.get("coverage_complete") is True
        if not coverage:
            raise RuntimeError(
                "TPC-H client-QPS binding requires complete Q1-Q22 coverage"
            )
        row.update({
            "server_db_qps_audit": server_qps,
            "db_qps": client_qps,
            "qps_metric": METRIC,
            "qps_metric_schema_version": SCHEMA_VERSION,
            "server_db_qps_used_for_outer_acceptance": False,
            "client_qps_used_for_outer_acceptance": True,
        })
        checks = copy.deepcopy(dict(row.get("hard_gate_checks") or {}))
        checks["full_suite_client_qps_positive"] = True
        checks["q1_q22_coverage_complete_for_acceptance"] = True
        row["hard_gate_checks"] = checks
        rebound[str(worker_id)] = row
    return rebound


def wrap_measure_batch(
    base_measure_batch: Callable[..., Mapping[str, Mapping[str, Any]]],
    *,
    load: Callable[[Path], Mapping[str, Any]],
    atomic: Callable[[Path, Mapping[str, Any]], None],
) -> Callable[..., dict[str, dict[str, Any]]]:
    """Decorate an existing formal batch producer without changing workload IO."""

    def measure_batch(*args: Any, **kwargs: Any) -> dict[str, dict[str, Any]]:
        measured = base_measure_batch(*args, **kwargs)
        rebound = rebind_measurements(measured)
        output = kwargs.get("output")
        if output is None and len(args) >= 4:
            output = args[3]
        if output is None:
            raise RuntimeError("TPC-H client-QPS adapter cannot resolve output")
        batch_path = Path(output) / "batch_result.json"
        document = copy.deepcopy(dict(load(batch_path)))
        if set(document.get("measurements") or {}) != set(rebound):
            raise RuntimeError("TPC-H batch artifact/return worker sets differ")
        document["measurements"] = copy.deepcopy(rebound)
        document.update({
            "qps_metric": METRIC,
            "qps_metric_schema_version": SCHEMA_VERSION,
            "server_db_qps_preserved_as_audit": True,
            "client_qps_controls_outer_acceptance": True,
        })
        atomic(batch_path, document)
        return rebound

    return measure_batch


def contract() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "scene": SCENE,
        "outer_progress_metric": METRIC,
        "required_query_coverage": "Q1-Q22 exactly once",
        "server_db_qps_role": "audit_only",
        "client_qps_role": "outer_acceptance_and_incumbent_progress",
        "other_scene_measurement_semantics_changed": False,
        "external_algorithm_evidence_used": False,
    }


__all__ = [
    "METRIC",
    "SCENE",
    "SCHEMA_VERSION",
    "contract",
    "rebind_measurements",
    "wrap_measure_batch",
]
