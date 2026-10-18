#!/usr/bin/env python3
"""Run and seal the single fresh default JOB R0 before the v3 formal search."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Dict


LANE_ROOT = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/"
    "pg5_common_outer_bgallowed_20260722_v1/job_node02"
)
OUTER_ROOT = LANE_ROOT / "history/job/outer/attempt_1"
GLOBAL_COUNTER_PATH = OUTER_ROOT / "replay_counter.txt"
SEARCH_REPLAY_COUNTER_PATH = OUTER_ROOT / "formal_search_replay_counter.txt"
SEARCH_ACCEPTED_COUNTER_PATH = OUTER_ROOT / "formal_search_accepted_counter.txt"
STARTUP_BASELINE_COUNTER_PATH = OUTER_ROOT / "formal_startup_baseline_counter.txt"
POINT_ROOT = OUTER_ROOT / "formal_points/point_0014"
RESULT_PATH = POINT_ROOT / "point_result.json"
MARKER_PATH = POINT_ROOT / "formal_replay_validity.json"
POINTER_PATH = OUTER_ROOT / "fresh_restart_r0/latest.json"
RUNNER_PATH = LANE_ROOT / "launch_outer/run_job_formal_point.sh"
MEMORY_BYTES = 3124756480
EXPECTED_DEFAULT_COSTS = {
    "seq_page_cost": 1.0,
    "random_page_cost": 4.0,
    "cpu_tuple_cost": 0.01,
    "cpu_index_tuple_cost": 0.005,
    "cpu_operator_cost": 0.0025,
    "parallel_setup_cost": 1000.0,
    "parallel_tuple_cost": 0.1,
}
PROFILE_BANDS = {
    "cpu": (0.30, 0.50),
    "memory": (0.85, 0.95),
    "io": (0.30, 0.50),
}


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def write_json_immutable(path: Path, value: Dict[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise RuntimeError(f"refusing to overwrite immutable fresh R0 evidence: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_counter_if_present(path: Path) -> int:
    return int(path.read_text(encoding="utf-8").strip()) if path.is_file() else 0


def main() -> int:
    if int(GLOBAL_COUNTER_PATH.read_text(encoding="utf-8").strip()) != 13:
        raise RuntimeError("fresh restart R0 must chain from immutable global ordinal 13")
    if read_counter_if_present(SEARCH_REPLAY_COUNTER_PATH) != 0:
        raise RuntimeError("formal-search replay budget must start at zero")
    if read_counter_if_present(SEARCH_ACCEPTED_COUNTER_PATH) != 0:
        raise RuntimeError("formal-search accepted budget must start at zero")
    if read_counter_if_present(STARTUP_BASELINE_COUNTER_PATH) != 0:
        raise RuntimeError("formal startup-baseline count must start at zero")
    if POINT_ROOT.exists() or POINTER_PATH.exists() or POINTER_PATH.is_symlink():
        raise RuntimeError("fresh restart R0 evidence already exists; refusing replay")

    environment = os.environ.copy()
    environment.update(
        {
            "RA_PG_REPLAY_ACCOUNTING_SCOPE": "fresh_default_r0",
            "RA_PG_FORMAL_SEARCH_MAX_REPLAYS": "10",
            "RA_PG_R0_MEAN_PROFILE_GATE": "1",
            "RA_PG_MEMORY_BYTES": str(MEMORY_BYTES),
            "RA_PG_RUN_ATTEMPT_ID": "job_v3_fresh_default_r0_global_0014",
            "RA_PG_QUALITY_CALIBRATION_ROLE": "fresh_default_r0_before_formal_search",
            "RA_PG_EXPLICIT_COST_PARAMETERS_JSON": json.dumps(
                EXPECTED_DEFAULT_COSTS,
                sort_keys=True,
                separators=(",", ":"),
            ),
        }
    )
    subprocess.run([str(RUNNER_PATH)], check=True, env=environment, timeout=1800)

    result = read_json(RESULT_PATH)
    measurement = result.get("measurement") or {}
    replay_accounting = (result.get("evidence_context") or {}).get(
        "replay_accounting"
    ) or {}
    costs = {name: float(value) for name, value in (result.get("cost_parameters") or {}).items()}
    gate = measurement.get("r0_mean_profile_gate") or {}
    means = gate.get("means") or {}
    field_passed = {
        resource: lower <= float(means.get(resource, -1.0)) <= upper
        for resource, (lower, upper) in PROFILE_BANDS.items()
    }
    checks = {
        "status_complete": result.get("status") == "complete",
        "correctness_passed": measurement.get("correctness_passed") is True,
        "zero_error_rate": float(measurement.get("error_rate") or 0.0) == 0.0,
        "zero_error_count": int(measurement.get("error_count") or 0) == 0,
        "measurement_at_least_180_seconds": int(
            measurement.get("effective_measurement_seconds") or 0
        ) >= 180,
        "stability_passed": (
            (measurement.get("resource_stability") or {}).get("passed") is True
            and (measurement.get("resource_stability") or {}).get("stable") is True
        ),
        "mean_first_gate_enabled": gate.get("enabled") is True,
        "mean_first_gate_passed": gate.get("passed") is True,
        "profile_means_in_range": all(field_passed.values()),
        "no_oom": result.get("oom_killed") is False,
        "no_database_restart": int(result.get("database_restart_count") or 0) == 0,
        "exact_default_cost_vector": costs == EXPECTED_DEFAULT_COSTS,
        "fresh_scope": replay_accounting.get("scope") == "fresh_default_r0",
        "global_ordinal_13_to_14": (
            replay_accounting.get("global_ordinal_before") == 13
            and replay_accounting.get("global_ordinal") == 14
        ),
        "formal_replay_budget_unchanged": (
            replay_accounting.get("formal_search_replay_count_before") == 0
            and replay_accounting.get("formal_search_replay_count") == 0
        ),
    }
    if not all(checks.values()):
        raise RuntimeError(f"fresh restart R0 failed closed: {checks}")
    if int(GLOBAL_COUNTER_PATH.read_text(encoding="utf-8").strip()) != 14:
        raise RuntimeError("global ordinal did not advance exactly once to 14")
    if read_counter_if_present(SEARCH_REPLAY_COUNTER_PATH) != 0:
        raise RuntimeError("fresh R0 consumed formal-search replay budget")
    if read_counter_if_present(SEARCH_ACCEPTED_COUNTER_PATH) != 0:
        raise RuntimeError("fresh R0 changed formal-search accepted budget")

    profile_gate = {
        "gate_seconds": int(gate["gate_seconds"]),
        "means": {resource: float(means[resource]) for resource in PROFILE_BANDS},
        "bands": {resource: list(band) for resource, band in PROFILE_BANDS.items()},
        "field_passed": field_passed,
        "passed": True,
        "policy": gate.get("policy"),
    }
    marker = {
        "schema_version": 1,
        "campaign_id": "pg5_common_outer_bgallowed_20260722_v1",
        "fresh_r0_id": "job_v3_fresh_default_r0_global_0014",
        "point_id": result.get("point_id"),
        "global_replay_ordinal": 14,
        "status": "valid_fresh_default_r0_for_v3_formal_search",
        "reuse_as_restart_baseline": True,
        "eligible_for_benchmark_scoped_training": False,
        "formal_search_budget_consumed": False,
        "checks": checks,
        "profile_gate": profile_gate,
        "resource_profile": {
            "cpu_quota_cores": 3.0,
            "memory_bytes": MEMORY_BYTES,
            "io_read_bps": 134217728,
            "io_write_bps": 134217728,
            "job_terminals": 2,
        },
        "cost_parameters": EXPECTED_DEFAULT_COSTS,
        "qps": float(measurement["qps"]),
        "point_result_path": str(RESULT_PATH),
        "point_result_sha256": sha256(RESULT_PATH),
        "sealed_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    write_json_immutable(MARKER_PATH, marker)
    pointer = {
        "schema_version": 1,
        "campaign_id": "pg5_common_outer_bgallowed_20260722_v1",
        "fresh_r0_id": marker["fresh_r0_id"],
        "global_ordinal_before_fresh_r0": 13,
        "global_replay_ordinal": 14,
        "global_counter_reset": False,
        "formal_search_replay_count": 0,
        "formal_search_accepted_count": 0,
        "point_result_path": str(RESULT_PATH),
        "point_result_sha256": marker["point_result_sha256"],
        "validity_marker_path": str(MARKER_PATH),
        "validity_marker_sha256": sha256(MARKER_PATH),
        "profile_gate": profile_gate,
        "sealed_at_utc": marker["sealed_at_utc"],
    }
    write_json_immutable(POINTER_PATH, pointer)
    print(json.dumps(pointer, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
