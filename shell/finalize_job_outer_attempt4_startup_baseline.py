#!/usr/bin/env python3
"""Validate point 28 DB QPS/R0 and arm attempt4 search only after it passes."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Dict


LANE = Path("/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/job_node02")
VARIANT = os.environ.get("RA_ATTEMPT4_VARIANT", "attempt_4_formal_dbqps_v1")
RUN_ID = os.environ.get("RA_ATTEMPT4_RUN_ID", "formal_job_outer_attempt4_formal_dbqps_v1")
WORKSPACE_NAME = os.environ.get("RA_ATTEMPT4_WORKSPACE", "workspace_outer_attempt4_formal_dbqps_v1")
GLOBAL_BEFORE = int(os.environ.get("RA_ATTEMPT4_GLOBAL_BEFORE", "27"))
EXPECTED_POINT_ID = os.environ.get(
    "RA_ATTEMPT4_EXPECTED_POINT_ID",
    "job_experience_table_outer_attempt4_formal_dbqps_v1_0028",
)
ROOT = LANE / "history/job/outer" / VARIANT
WORKSPACE = LANE / WORKSPACE_NAME
CONFIG = ROOT / "generated_config" / RUN_ID / "memory/job.json"
MANIFEST = ROOT / "attempt4_contract_manifest.json"
POINT_ORDINAL = GLOBAL_BEFORE + 1
POINT_ROOT = ROOT / "formal_points" / f"point_{POINT_ORDINAL:04d}"
RESULT = POINT_ROOT / "point_result.json"
VALIDATION = ROOT / "startup_baseline_formal_dbqps_validation.json"
EVIDENCE = ROOT / "startup_baseline_reclassification_evidence.json"
EXPECTED_INTERNAL_SOURCE = "pg_stat_statements_sum_calls_delta_over_formal_measurement_seconds"
P_REAL = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_immutable(path: Path, value: Dict[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise RuntimeError(f"refusing to overwrite immutable attempt4 evidence: {path}")
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def main() -> int:
    for path in (CONFIG, MANIFEST, RESULT):
        if not path.is_file():
            raise RuntimeError(f"required attempt4 baseline artifact missing: {path}")
    result = json.loads(RESULT.read_text(encoding="utf-8"))
    measurement = result.get("measurement") or {}
    if result.get("status") != "complete":
        raise RuntimeError("attempt4 startup point is not complete")
    if result.get("point_id") != EXPECTED_POINT_ID:
        raise RuntimeError(f"unexpected attempt4 startup point id: {result.get('point_id')}")
    if measurement.get("correctness_passed") is not True:
        raise RuntimeError("attempt4 startup correctness failed")
    stability = measurement.get("resource_stability") or {}
    if stability.get("passed") is not True or stability.get("stable") is not True:
        raise RuntimeError("attempt4 startup resource stability failed")
    if result.get("oom_killed") is not False or int(result.get("database_restart_count") or 0) != 0:
        raise RuntimeError("attempt4 startup had an OOM or database restart")
    actual_params = result.get("cost_parameters") or {}
    if set(actual_params) != set(P_REAL) or any(float(actual_params[key]) != value for key, value in P_REAL.items()):
        raise RuntimeError("attempt4 startup point is not exact p_real")

    db_qps = measurement.get("db_qps")
    if isinstance(db_qps, bool) or not isinstance(db_qps, (int, float)):
        raise RuntimeError("measurement.db_qps is missing or non-numeric")
    db_qps = float(db_qps)
    if not math.isfinite(db_qps) or db_qps <= 0.0:
        raise RuntimeError(f"measurement.db_qps is invalid: {db_qps}")
    if measurement.get("db_qps_source") != EXPECTED_INTERNAL_SOURCE:
        raise RuntimeError("measurement.db_qps_source is not the formal pg_stat_statements source")
    calls = float(measurement.get("db_statement_calls_delta") or 0.0)
    seconds = float(measurement.get("db_qps_effective_seconds") or 0.0)
    if calls <= 0.0 or seconds <= 0.0 or not math.isclose(db_qps, calls / seconds, rel_tol=1e-9, abs_tol=1e-12):
        raise RuntimeError("measurement.db_qps does not equal statement-call delta / formal seconds")

    derived_path = Path(str(measurement.get("derived_resource_samples_path") or ""))
    if not derived_path.is_file() or sha256(derived_path) != measurement.get("derived_resource_samples_sha256"):
        raise RuntimeError("attempt4 derived-resource evidence hash mismatch")
    import sys

    sys.path.insert(0, str(WORKSPACE))
    from formal_pg_resource_semantics import (  # type: ignore
        normalize_derived_resource_samples,
        summarize_resource_samples,
    )

    derived_rows = [
        json.loads(line)
        for line in derived_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    formal_resource = summarize_resource_samples(
        normalize_derived_resource_samples(derived_rows),
        measurement.get("resource_denominators") or {},
    )
    aggregate = formal_resource["aggregate"]
    utilization_pct = {
        "cpu": float(aggregate["cpu_utilization"]) * 100.0,
        "memory": float(aggregate["memory_utilization"]) * 100.0,
        "io": float(aggregate["io_utilization"]) * 100.0,
    }
    checks = {
        "cpu": 30.0 <= utilization_pct["cpu"] <= 50.0,
        "memory": 85.0 <= utilization_pct["memory"] <= 95.0,
        "io": 30.0 <= utilization_pct["io"] <= 50.0,
    }
    if not all(checks.values()):
        raise RuntimeError(f"attempt4 startup strict R0 profile failed: {utilization_pct}")

    counter_values = {
        "global": int((ROOT / "replay_counter.txt").read_text(encoding="utf-8").strip()),
        "candidate_replay": int((ROOT / "formal_search_replay_counter.txt").read_text(encoding="utf-8").strip()),
        "accepted": int((ROOT / "formal_search_accepted_counter.txt").read_text(encoding="utf-8").strip()),
        "startup_baseline": int((ROOT / "formal_startup_baseline_counter.txt").read_text(encoding="utf-8").strip()),
    }
    if counter_values != {"global": POINT_ORDINAL, "candidate_replay": 0, "accepted": 0, "startup_baseline": 1}:
        raise RuntimeError(f"attempt4 startup accounting mismatch: {counter_values}")

    validation = {
        "schema_version": 1,
        "status": "passed",
        "search_authorized": True,
        "point_id": result["point_id"],
        "point_result_path": str(RESULT),
        "point_result_sha256": sha256(RESULT),
        "formal_db_qps": db_qps,
        "formal_db_qps_source": measurement["db_qps_source"],
        "db_statement_calls_delta": calls,
        "db_qps_effective_seconds": seconds,
        "client_completion_qps": float(measurement["qps"]),
        "client_qps_not_used_as_db_qps": True,
        "strict_r0_utilization_pct": utilization_pct,
        "strict_r0_checks": checks,
        "correctness_passed": True,
        "resource_stability": stability,
        "accounting": counter_values,
        "validated_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    write_immutable(VALIDATION, validation)
    evidence = {
        "schema_version": 1,
        "audit_id": "pg5_job_attempt4_formal_dbqps_startup_baseline_v1",
        "point_id": result["point_id"],
        "point_result_path": str(RESULT),
        "point_result_sha256": sha256(RESULT),
        "classification": "formal_startup_baseline",
        "candidate_replay_budget_after": 0,
        "accepted_budget_after": 0,
        "startup_baseline_count_after": 1,
        "validation_path": str(VALIDATION),
        "validation_sha256": sha256(VALIDATION),
        "sealed_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    write_immutable(EVIDENCE, evidence)

    config = json.loads(CONFIG.read_text(encoding="utf-8"))
    config["startup_baseline_reuse"] = {
        "enabled": True,
        "point_result_path": str(RESULT),
        "point_result_sha256": sha256(RESULT),
        "reclassification_evidence_path": str(EVIDENCE),
        "reclassification_evidence_sha256": sha256(EVIDENCE),
        "startup_baseline_count_before_restart": 1,
        "db_qps_source": "formal_benchmark_db_qps",
    }
    protocol = config["formal_outer_protocol"]
    protocol["global_replay_ordinal_at_binding"] = POINT_ORDINAL
    protocol["formal_startup_baseline_count_start"] = 1
    config["_attempt4_contract"].update(
        {
            "phase": "startup_baseline_validated_search_armed",
            "startup_validation_path": str(VALIDATION),
            "startup_validation_sha256": sha256(VALIDATION),
            "startup_reclassification_evidence_path": str(EVIDENCE),
            "startup_reclassification_evidence_sha256": sha256(EVIDENCE),
        }
    )
    CONFIG.write_text(
        json.dumps(config, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest.update(
        {
            "phase": "startup_baseline_validated_search_armed",
            "config_sha256": sha256(CONFIG),
            "startup_point_result_path": str(RESULT),
            "startup_point_result_sha256": sha256(RESULT),
            "startup_validation_path": str(VALIDATION),
            "startup_validation_sha256": sha256(VALIDATION),
            "startup_reclassification_evidence_path": str(EVIDENCE),
            "startup_reclassification_evidence_sha256": sha256(EVIDENCE),
            "formal_db_qps": db_qps,
            "formal_db_qps_internal_source": measurement["db_qps_source"],
            "candidate_budget_after_startup": 0,
        }
    )
    MANIFEST.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                **validation,
                "config_sha256": sha256(CONFIG),
                "manifest_sha256": sha256(MANIFEST),
                "reclassification_evidence_sha256": sha256(EVIDENCE),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
