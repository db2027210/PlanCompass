#!/usr/bin/env python3
"""Normalize TPC-H candidate client QPS to a two-worker baseline cohort."""

from __future__ import annotations

import copy
import math
from statistics import fmean
from typing import Any, Mapping, MutableMapping


SCHEMA_VERSION = "mysql_tpch_same_worker_client_qps_normalization_v1"


def baseline_contract(
    document: Mapping[str, Any], *, scene: str,
) -> dict[str, Any]:
    measurements = document.get("measurements")
    if (
        document.get("status") != "PASS"
        or document.get("failures") not in ([], None)
        or not isinstance(measurements, Mapping)
        or len(measurements) != 2
    ):
        raise RuntimeError("two-worker TPC-H baseline differs")
    worker_qps: dict[str, float] = {}
    for worker_id, raw in measurements.items():
        if not isinstance(raw, Mapping):
            raise RuntimeError("TPC-H baseline row is not an object")
        qps = float(raw.get("db_qps") or 0.0)
        if (
            raw.get("scene") != scene
            or raw.get("hard_gate_passed") is not True
            or raw.get("coverage_complete") is not True
            or not math.isfinite(qps)
            or qps <= 0.0
        ):
            raise RuntimeError("TPC-H worker baseline is invalid")
        worker_qps[str(worker_id)] = qps
    canonical = float(fmean(worker_qps.values()))
    return {
        "schema_version": SCHEMA_VERSION,
        "scene": scene,
        "canonical_baseline_client_qps": canonical,
        "worker_baseline_client_qps": copy.deepcopy(worker_qps),
        "worker_to_canonical_factors": {
            worker_id: canonical / qps
            for worker_id, qps in worker_qps.items()
        },
        "comparison_semantics": (
            "candidate_raw_client_qps_times_canonical_over_same_worker_baseline"
        ),
    }


def normalize_candidate_in_place(
    row: MutableMapping[str, Any], contract: Mapping[str, Any],
) -> MutableMapping[str, Any]:
    if row.get("same_worker_qps_normalization_applied") is True:
        return row
    worker_id = str(row.get("worker_id") or "")
    worker_qps = dict(contract.get("worker_baseline_client_qps") or {})
    factors = dict(contract.get("worker_to_canonical_factors") or {})
    if worker_id not in worker_qps or worker_id not in factors:
        raise RuntimeError(f"TPC-H candidate worker baseline absent: {worker_id}")
    raw_qps = float(row.get("db_qps") or 0.0)
    factor = float(factors[worker_id])
    normalized = raw_qps * factor
    if (
        not math.isfinite(raw_qps) or raw_qps <= 0.0
        or not math.isfinite(factor) or factor <= 0.0
        or not math.isfinite(normalized) or normalized <= 0.0
    ):
        raise RuntimeError("TPC-H candidate QPS normalization is invalid")
    row.update({
        "raw_worker_client_qps": raw_qps,
        "same_worker_baseline_client_qps": float(worker_qps[worker_id]),
        "canonical_baseline_client_qps": float(
            contract["canonical_baseline_client_qps"]
        ),
        "same_worker_qps_normalization_factor": factor,
        "same_worker_normalized_client_qps": normalized,
        "same_worker_qps_normalization_applied": True,
        "same_worker_qps_normalization_schema_version": SCHEMA_VERSION,
        "db_qps": normalized,
    })
    return row


__all__ = ["SCHEMA_VERSION", "baseline_contract", "normalize_candidate_in_place"]
