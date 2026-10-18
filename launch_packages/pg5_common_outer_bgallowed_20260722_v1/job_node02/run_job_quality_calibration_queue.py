#!/usr/bin/env python3
"""Measure four selected JOB calibration vectors with fail-closed replacement."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Dict, List, Mapping


LANE_ROOT = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/"
    "pg5_common_outer_bgallowed_20260722_v1/job_node02"
)
OUTER_ROOT = LANE_ROOT / "history/job/outer/attempt_1"
CALIBRATION_ROOT = OUTER_ROOT / "job_quality_calibration"
SELECTION_PATH = CALIBRATION_ROOT / "calibration_selection.json"
QUEUE_STATE_PATH = CALIBRATION_ROOT / "measurement_queue_state.json"
QUEUE_LEDGER_PATH = CALIBRATION_ROOT / "measurement_queue_ledger.jsonl"
COUNTER_PATH = OUTER_ROOT / "replay_counter.txt"
RUNNER = LANE_ROOT / "launch_outer/run_job_formal_point.sh"
EXPECTED_MEMORY_BYTES = 3124756480
EXPECTED_CPU_CORES = 3.0
EXPECTED_IO_BPS = 134217728
EXPECTED_TERMINALS = 2
MAX_REPLAY_ORDINAL = 10
REQUIRED_VALID_POINTS = 4
ACTIVE_PARAMS = (
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
)


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    temporary.replace(path)


def append_ledger(value: Mapping[str, Any]) -> None:
    QUEUE_LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with QUEUE_LEDGER_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(value), ensure_ascii=False, sort_keys=True) + "\n")


def current_counter() -> int:
    return int(COUNTER_PATH.read_text(encoding="utf-8").strip())


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def exact_params_equal(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return all(float(left[name]).hex() == float(right[name]).hex() for name in ACTIVE_PARAMS)


def validate_result(result: Mapping[str, Any], candidate: Mapping[str, Any]) -> List[str]:
    reasons: List[str] = []
    measurement = result.get("measurement") or {}
    means = measurement.get("resource_mean_utilization") or {}
    denominators = measurement.get("resource_denominators") or {}
    requested = (result.get("readback") or {}).get("requested_resource_profile") or {}
    checks = {
        "status_complete": result.get("status") == "complete",
        "cost_parameters_exact": exact_params_equal(
            result.get("cost_parameters") or {},
            candidate.get("params") or {},
        ),
        "cpu_cap": float(requested.get("cpu_quota_cores") or 0.0) == EXPECTED_CPU_CORES,
        "memory_cap": int(requested.get("memory_bytes") or 0) == EXPECTED_MEMORY_BYTES,
        "memory_denominator": int(denominators.get("memory_bytes") or 0) == EXPECTED_MEMORY_BYTES,
        "read_cap": int(requested.get("io_read_bps") or 0) == EXPECTED_IO_BPS,
        "write_cap": int(requested.get("io_write_bps") or 0) == EXPECTED_IO_BPS,
        "terminals": int((result.get("warmup") or {}).get("configured_terminals") or 0)
        == EXPECTED_TERMINALS,
        "correctness": measurement.get("correctness_passed") is True,
        "error_rate": float(measurement.get("error_rate") or 0.0) == 0.0,
        "minimum_seconds": int(measurement.get("effective_measurement_seconds") or 0) >= 180,
        "stability": (measurement.get("resource_stability") or {}).get("passed") is True,
        "no_oom": result.get("oom_killed") is False,
        "no_restart": int(result.get("database_restart_count") or 0) == 0,
        "cpu_profile_30_50": 0.30 <= float(means.get("cpu") or 0.0) <= 0.50,
        "memory_profile_85_95": 0.85 <= float(means.get("memory") or 0.0) <= 0.95,
        "io_profile_30_50": 0.30 <= float(means.get("io") or 0.0) <= 0.50,
    }
    for name, passed in checks.items():
        if not passed:
            reasons.append(name)
    return reasons


def marker_for(
    *,
    ordinal: int,
    candidate: Mapping[str, Any],
    status: str,
    result_path: Path,
    failure_reasons: List[str],
    runner_exit_code: int,
) -> Dict[str, Any]:
    result = read_json(result_path) if result_path.is_file() else {}
    measurement = result.get("measurement") or {}
    return {
        "schema_version": 1,
        "point_id": f"experience_table_outer_{ordinal:04d}",
        "replay_ordinal": ordinal,
        "status": status,
        "formal_baseline_eligible_for_original_run": status == "valid_quality_calibration_point",
        "reuse_as_restart_baseline": False,
        "eligible_for_benchmark_scoped_training": status == "valid_quality_calibration_point",
        "independent_parameter_vector_sample": status == "valid_quality_calibration_point",
        "selection_id": "pg5_job_quality_calibration_plan_only_v1",
        "selection_subset_id": candidate.get("subset_id"),
        "selection_rank": candidate.get("rank"),
        "selection_structural_fingerprint": candidate.get("structural_fingerprint"),
        "cost_parameters": candidate.get("params"),
        "runner_exit_code": runner_exit_code,
        "failure_reasons": failure_reasons,
        "measurement_completed": bool(result_path.is_file()),
        "measurement_stability_passed": (
            (measurement.get("resource_stability") or {}).get("passed") is True
        ),
        "resource_mean_utilization": measurement.get("resource_mean_utilization"),
        "qps": measurement.get("qps"),
        "point_result_sha256": sha256(result_path) if result_path.is_file() else None,
        "budget_disposition": "consumed_by_formal_replay_counter",
        "marked_at_utc": now(),
    }


def main() -> int:
    selection = read_json(SELECTION_PATH)
    selected = list(selection.get("selected") or [])
    replacements = list(selection.get("replacement_queue") or [])
    if len(selected) != REQUIRED_VALID_POINTS:
        raise RuntimeError(f"selection must contain four primary points, got {len(selected)}")
    queue: List[Dict[str, Any]] = [*selected, *replacements]
    valid: List[Dict[str, Any]] = []
    attempted_subset_ids = set()
    state: Dict[str, Any] = {
        "schema_version": 1,
        "status": "running",
        "started_at_utc": now(),
        "required_valid_points": REQUIRED_VALID_POINTS,
        "valid_points": valid,
        "attempts": [],
    }
    write_json(QUEUE_STATE_PATH, state)

    for candidate in queue:
        if len(valid) >= REQUIRED_VALID_POINTS:
            break
        subset_id = str(candidate.get("subset_id") or "")
        if not subset_id or subset_id in attempted_subset_ids:
            continue
        attempted_subset_ids.add(subset_id)
        counter_before = current_counter()
        if counter_before >= MAX_REPLAY_ORDINAL:
            break
        ordinal = counter_before + 1
        point_dir = OUTER_ROOT / "formal_points" / f"point_{ordinal:04d}"
        log_path = LANE_ROOT / "logs" / f"job_quality_calibration_point_{ordinal:04d}.log"
        env = os.environ.copy()
        env.update(
            {
                "RA_PG_MEMORY_BYTES": str(EXPECTED_MEMORY_BYTES),
                "RA_PG_RUN_ATTEMPT_ID": f"job_quality_calibration_point_{ordinal:04d}",
                "RA_PG_QUALITY_CALIBRATION_ROLE": "nondefault_model_quality_calibration",
                "RA_PG_CALIBRATION_SELECTION_ID": str(selection.get("selection_id") or ""),
                "RA_PG_CALIBRATION_SUBSET_ID": subset_id,
                "RA_PG_EXPLICIT_COST_PARAMETERS_JSON": json.dumps(
                    candidate.get("params") or {},
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            }
        )
        attempt = {
            "ordinal": ordinal,
            "subset_id": subset_id,
            "rank": candidate.get("rank"),
            "structural_fingerprint": candidate.get("structural_fingerprint"),
            "params": candidate.get("params"),
            "status": "running",
            "started_at_utc": now(),
            "log_path": str(log_path),
        }
        state["current"] = attempt
        state["attempts"].append(attempt)
        write_json(QUEUE_STATE_PATH, state)
        append_ledger({"event": "calibration_point_start", **attempt})
        print("CALIBRATION_POINT_START=" + json.dumps(attempt, sort_keys=True), flush=True)

        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w", encoding="utf-8") as log_handle:
            completed = subprocess.run(
                [str(RUNNER)],
                check=False,
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
            )
        actual_counter = current_counter()
        result_path = point_dir / "point_result.json"
        failure_reasons: List[str] = []
        if actual_counter != ordinal:
            failure_reasons.append(
                f"counter_mismatch_expected_{ordinal}_actual_{actual_counter}"
            )
        if completed.returncode != 0:
            failure_reasons.append(f"runner_exit_{completed.returncode}")
        if result_path.is_file():
            failure_reasons.extend(
                reason
                for reason in validate_result(read_json(result_path), candidate)
                if reason not in failure_reasons
            )
        else:
            failure_reasons.append("missing_point_result")
        status = (
            "valid_quality_calibration_point"
            if not failure_reasons
            else "invalid_quality_calibration_point"
        )
        marker = marker_for(
            ordinal=ordinal,
            candidate=candidate,
            status=status,
            result_path=result_path,
            failure_reasons=failure_reasons,
            runner_exit_code=completed.returncode,
        )
        write_json(point_dir / "formal_replay_validity.json", marker)
        attempt.update(
            {
                "status": status,
                "completed_at_utc": now(),
                "failure_reasons": failure_reasons,
                "point_result_path": str(result_path) if result_path.is_file() else None,
                "point_result_sha256": marker.get("point_result_sha256"),
                "resource_mean_utilization": marker.get("resource_mean_utilization"),
                "qps": marker.get("qps"),
            }
        )
        if not failure_reasons:
            valid.append(dict(attempt))
        state["valid_points"] = valid
        state["current"] = None
        write_json(QUEUE_STATE_PATH, state)
        append_ledger({"event": "calibration_point_complete", **attempt})
        print("CALIBRATION_POINT_COMPLETE=" + json.dumps(attempt, sort_keys=True), flush=True)

    state["completed_at_utc"] = now()
    state["current"] = None
    if len(valid) == REQUIRED_VALID_POINTS:
        state["status"] = "complete"
        write_json(QUEUE_STATE_PATH, state)
        print(
            "CALIBRATION_QUEUE_COMPLETE="
            + json.dumps(
                {
                    "valid_count": len(valid),
                    "valid_ordinals": [item["ordinal"] for item in valid],
                    "counter": current_counter(),
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 0
    state["status"] = "failed_insufficient_budget_or_candidates"
    write_json(QUEUE_STATE_PATH, state)
    raise RuntimeError(
        f"only {len(valid)} valid calibration points before replay/candidate exhaustion"
    )


if __name__ == "__main__":
    raise SystemExit(main())
