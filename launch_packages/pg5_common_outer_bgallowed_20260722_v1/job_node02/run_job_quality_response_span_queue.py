#!/usr/bin/env python3
"""Measure the second four-point JOB quality-calibration batch."""

from __future__ import annotations

import datetime as dt
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import subprocess
from typing import Any, Dict, List, Mapping, Sequence, Tuple


LANE_ROOT = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/"
    "pg5_common_outer_bgallowed_20260722_v1/job_node02"
)
OUTER_ROOT = LANE_ROOT / "history/job/outer/attempt_1"
CALIBRATION_ROOT = OUTER_ROOT / "job_quality_calibration"
BATCH_ROOT = CALIBRATION_ROOT / "response_span_batch2"
SELECTION_PATH = BATCH_ROOT / "response_span_selection.json"
QUEUE_STATE_PATH = BATCH_ROOT / "measurement_queue_state.json"
QUEUE_LEDGER_PATH = BATCH_ROOT / "measurement_queue_ledger.jsonl"
COUNTER_PATH = OUTER_ROOT / "replay_counter.txt"
RUNNER = LANE_ROOT / "launch_outer/run_job_formal_point.sh"
EXPECTED_SELECTION_ID = "pg5_job_quality_response_span_batch2_v1"
EXPECTED_START_COUNTER = 9
MAX_BATCH_ATTEMPTS = 12
REQUIRED_VALID_POINTS = 4
EXPECTED_MEMORY_BYTES = 3124756480
EXPECTED_CPU_CORES = 3.0
EXPECTED_IO_BPS = 134217728
EXPECTED_TERMINALS = 2
ACTIVE_PARAMS = (
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_index_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
)
RESOURCES = ("cpu", "io", "memory")
PROFILE_BANDS = {
    "cpu": (0.30, 0.50),
    "memory": (0.85, 0.95),
    "io": (0.30, 0.50),
}


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


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def current_counter() -> int:
    return int(COUNTER_PATH.read_text(encoding="utf-8").strip())


def exact_params_equal(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return all(float(left[name]).hex() == float(right[name]).hex() for name in ACTIVE_PARAMS)


def response_roles(
    candidate: Mapping[str, Any],
    low_ids: Mapping[str, set[str]],
    high_ids: Mapping[str, set[str]],
) -> List[str]:
    subset_id = str(candidate["subset_id"])
    return [
        f"{resource}_{end}"
        for resource in RESOURCES
        for end, ids in (("low", low_ids[resource]), ("high", high_ids[resource]))
        if subset_id in ids
    ]


def set_is_legal(
    candidates: Sequence[Mapping[str, Any]],
    low_ids: Mapping[str, set[str]],
    high_ids: Mapping[str, set[str]],
) -> bool:
    if len(candidates) != REQUIRED_VALID_POINTS:
        return False
    ids = {str(item["subset_id"]) for item in candidates}
    return (
        len(ids) == REQUIRED_VALID_POINTS
        and len({str(item["structural_fingerprint"]) for item in candidates})
        == REQUIRED_VALID_POINTS
        and all(ids & low_ids[resource] and ids & high_ids[resource] for resource in RESOURCES)
        and len(set().union(*(set(item["changed_params"]) for item in candidates)))
        == len(ACTIVE_PARAMS)
    )


def response_span_score(candidates: Sequence[Mapping[str, Any]]) -> Tuple[float, float, float]:
    spans = []
    for resource in RESOURCES:
        values = [float(item["predicted_formula_resource"][resource]) for item in candidates]
        spans.append(max(values) - min(values))
    transformed = [item["transformed_vector"] for item in candidates]
    distances = [
        math.sqrt(sum((a - b) ** 2 for a, b in zip(transformed[left], transformed[right])))
        for left, right in itertools.combinations(range(len(transformed)), 2)
    ]
    return min(spans), sum(spans), min(distances)


def choose_replacement(
    *,
    valid: Sequence[Mapping[str, Any]],
    pending: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    attempted_ids: set[str],
    low_ids: Mapping[str, set[str]],
    high_ids: Mapping[str, set[str]],
) -> Dict[str, Any]:
    best: Dict[str, Any] | None = None
    best_score: Tuple[float, float, float] | None = None
    for candidate in candidates:
        if str(candidate["subset_id"]) in attempted_ids:
            continue
        proposed = [*valid, *pending, candidate]
        if not set_is_legal(proposed, low_ids, high_ids):
            continue
        score = response_span_score(proposed)
        if best_score is None or score > best_score:
            best = dict(candidate)
            best_score = score
    if best is None:
        raise RuntimeError("cannot reconstruct a legal four-point response-span set")
    best["response_roles"] = response_roles(best, low_ids, high_ids)
    best["replacement_for_failed_point"] = True
    return best


def validate_result(result: Mapping[str, Any], candidate: Mapping[str, Any]) -> List[str]:
    reasons: List[str] = []
    measurement = result.get("measurement") or {}
    final_means = measurement.get("resource_mean_utilization") or {}
    r0_gate = measurement.get("r0_mean_profile_gate") or {}
    gate_means = r0_gate.get("means") or {}
    denominators = measurement.get("resource_denominators") or {}
    requested = (result.get("readback") or {}).get("requested_resource_profile") or {}
    checks = {
        "status_complete": result.get("status") == "complete",
        "cost_parameters_exact": exact_params_equal(
            result.get("cost_parameters") or {}, candidate.get("params") or {}
        ),
        "cpu_cap": float(requested.get("cpu_quota_cores") or 0.0) == EXPECTED_CPU_CORES,
        "memory_cap": int(requested.get("memory_bytes") or 0) == EXPECTED_MEMORY_BYTES,
        "memory_denominator": int(denominators.get("memory_bytes") or 0)
        == EXPECTED_MEMORY_BYTES,
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
        "r0_profile_gate_present": r0_gate.get("enabled") is True,
        "r0_profile_gate_180s": int(r0_gate.get("gate_seconds") or 0) == 180,
        "r0_profile_gate_passed": r0_gate.get("passed") is True,
    }
    for resource, (lower, upper) in PROFILE_BANDS.items():
        checks[f"r0_{resource}_profile"] = lower <= float(gate_means.get(resource) or 0.0) <= upper
        checks[f"final_{resource}_profile"] = lower <= float(final_means.get(resource) or 0.0) <= upper
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
    selection_sha256: str,
) -> Dict[str, Any]:
    result = read_json(result_path) if result_path.is_file() else {}
    measurement = result.get("measurement") or {}
    return {
        "schema_version": 1,
        "point_id": f"experience_table_outer_{ordinal:04d}",
        "replay_ordinal": ordinal,
        "status": status,
        "formal_baseline_eligible_for_original_run": False,
        "reuse_as_restart_baseline": False,
        "eligible_for_benchmark_scoped_training": status == "valid_quality_calibration_point",
        "independent_parameter_vector_sample": status == "valid_quality_calibration_point",
        "selection_id": EXPECTED_SELECTION_ID,
        "selection_sha256": selection_sha256,
        "selection_subset_id": candidate.get("subset_id"),
        "selection_rank": candidate.get("rank"),
        "selection_structural_fingerprint": candidate.get("structural_fingerprint"),
        "selection_response_roles": candidate.get("response_roles"),
        "cost_parameters": candidate.get("params"),
        "predicted_formula_resource": candidate.get("predicted_formula_resource"),
        "runner_exit_code": runner_exit_code,
        "failure_reasons": failure_reasons,
        "measurement_completed": bool(result_path.is_file()),
        "measurement_stability_passed": (
            (measurement.get("resource_stability") or {}).get("passed") is True
        ),
        "r0_mean_profile_gate": measurement.get("r0_mean_profile_gate"),
        "resource_mean_utilization": measurement.get("resource_mean_utilization"),
        "qps": measurement.get("qps"),
        "point_result_sha256": sha256(result_path) if result_path.is_file() else None,
        "budget_disposition": (
            "second_quality_calibration_extension_consumed_by_shared_replay_counter; "
            "formal_127_search_remains_forbidden"
        ),
        "marked_at_utc": now(),
    }


def main() -> int:
    selection = read_json(SELECTION_PATH)
    selection_sha = sha256(SELECTION_PATH)
    if selection.get("selection_id") != EXPECTED_SELECTION_ID:
        raise RuntimeError("unexpected response-span selection id")
    if selection.get("historical_success_coordinates_used") is not False:
        raise RuntimeError("selection is not independent of historical success coordinates")
    if selection.get("real_measurement_outcomes_used") is not False:
        raise RuntimeError("selection used real measurement outcomes")
    low_ids, high_ids = extreme_ids(selection)
    candidates = [dict(item) for item in selection.get("all_predicted_candidates") or []]
    candidate_by_id = {str(item["subset_id"]): item for item in candidates}
    if QUEUE_STATE_PATH.is_file():
        previous = read_json(QUEUE_STATE_PATH)
        previous_valid = list(previous.get("valid_points") or [])
        if (
            previous.get("status") == "running"
            and previous.get("current") is None
            and len(previous_valid) == REQUIRED_VALID_POINTS
            and previous.get("selection_sha256") == selection_sha
        ):
            for item in previous_valid:
                source = candidate_by_id[str(item["subset_id"])]
                item.setdefault("changed_params", source["changed_params"])
                item.setdefault("transformed_vector", source["transformed_vector"])
            if not set_is_legal(previous_valid, low_ids, high_ids):
                raise RuntimeError("completed measured set is not a legal response-span set")
            previous["valid_points"] = previous_valid
            previous["status"] = "complete"
            previous["valid_set_constraints_passed"] = True
            previous["completed_at_utc"] = now()
            previous["recovered_finalization_after_bookkeeping_error"] = True
            write_json(QUEUE_STATE_PATH, previous)
            append_ledger(
                {
                    "event": "response_span_queue_recovered_complete",
                    "selection_sha256": selection_sha,
                    "valid_ordinals": [item["ordinal"] for item in previous_valid],
                    "counter": current_counter(),
                    "at": now(),
                }
            )
            print(
                "RESPONSE_SPAN_QUEUE_RECOVERED_COMPLETE="
                + json.dumps(
                    {
                        "valid_count": len(previous_valid),
                        "valid_ordinals": [item["ordinal"] for item in previous_valid],
                        "counter": current_counter(),
                        "selection_sha256": selection_sha,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            return 0
    if current_counter() != EXPECTED_START_COUNTER:
        raise RuntimeError(
            f"expected replay counter {EXPECTED_START_COUNTER}, got {current_counter()}"
        )
    selected = [dict(item) for item in selection.get("selected") or []]
    if not set_is_legal(selected, *extreme_ids(selection)):
        raise RuntimeError("primary selection does not satisfy the four-point constraints")
    pending = list(selected)
    valid: List[Dict[str, Any]] = []
    attempted_ids: set[str] = set()
    state: Dict[str, Any] = {
        "schema_version": 1,
        "status": "running",
        "started_at_utc": now(),
        "selection_id": EXPECTED_SELECTION_ID,
        "selection_sha256": selection_sha,
        "required_valid_points": REQUIRED_VALID_POINTS,
        "max_batch_attempts": MAX_BATCH_ATTEMPTS,
        "formal_127_search_allowed": False,
        "valid_points": valid,
        "attempts": [],
    }
    write_json(QUEUE_STATE_PATH, state)

    while pending and len(valid) < REQUIRED_VALID_POINTS and len(attempted_ids) < MAX_BATCH_ATTEMPTS:
        candidate = pending.pop(0)
        subset_id = str(candidate["subset_id"])
        if subset_id in attempted_ids:
            continue
        attempted_ids.add(subset_id)
        counter_before = current_counter()
        ordinal = counter_before + 1
        point_dir = OUTER_ROOT / "formal_points" / f"point_{ordinal:04d}"
        if point_dir.exists() or point_dir.is_symlink():
            raise RuntimeError(f"next point output already exists: {point_dir}")
        log_path = LANE_ROOT / "logs" / f"job_quality_response_span_point_{ordinal:04d}.log"
        env = os.environ.copy()
        env.update(
            {
                "RA_PG_MEMORY_BYTES": str(EXPECTED_MEMORY_BYTES),
                "RA_PG_RUN_ATTEMPT_ID": f"job_quality_response_span_point_{ordinal:04d}",
                "RA_PG_QUALITY_CALIBRATION_ROLE": (
                    "response_span_batch2_model_quality_calibration"
                ),
                "RA_PG_R0_MEAN_PROFILE_GATE": "1",
                "RA_PG_CALIBRATION_SELECTION_ID": EXPECTED_SELECTION_ID,
                "RA_PG_CALIBRATION_SUBSET_ID": subset_id,
                "RA_PG_EXPLICIT_COST_PARAMETERS_JSON": json.dumps(
                    candidate["params"], sort_keys=True, separators=(",", ":")
                ),
            }
        )
        attempt = {
            "ordinal": ordinal,
            "subset_id": subset_id,
            "rank": candidate.get("rank"),
            "structural_fingerprint": candidate.get("structural_fingerprint"),
            "response_roles": candidate.get("response_roles"),
            "changed_params": candidate.get("changed_params"),
            "transformed_vector": candidate.get("transformed_vector"),
            "params": candidate.get("params"),
            "predicted_formula_resource": candidate.get("predicted_formula_resource"),
            "status": "running",
            "started_at_utc": now(),
            "log_path": str(log_path),
        }
        state["current"] = attempt
        state["attempts"].append(attempt)
        write_json(QUEUE_STATE_PATH, state)
        append_ledger({"event": "response_span_point_start", **attempt})
        print("RESPONSE_SPAN_POINT_START=" + json.dumps(attempt, sort_keys=True), flush=True)

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
        result_path = point_dir / "point_result.json"
        failure_reasons: List[str] = []
        if current_counter() != ordinal:
            failure_reasons.append(
                f"counter_mismatch_expected_{ordinal}_actual_{current_counter()}"
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
            else "sealed_invalid_quality_calibration_point"
        )
        marker = marker_for(
            ordinal=ordinal,
            candidate=candidate,
            status=status,
            result_path=result_path,
            failure_reasons=failure_reasons,
            runner_exit_code=completed.returncode,
            selection_sha256=selection_sha,
        )
        write_json(point_dir / "formal_replay_validity.json", marker)
        attempt.update(
            {
                "status": status,
                "completed_at_utc": now(),
                "failure_reasons": failure_reasons,
                "point_result_path": str(result_path) if result_path.is_file() else None,
                "point_result_sha256": marker.get("point_result_sha256"),
                "r0_mean_profile_gate": marker.get("r0_mean_profile_gate"),
                "resource_mean_utilization": marker.get("resource_mean_utilization"),
                "qps": marker.get("qps"),
            }
        )
        if not failure_reasons:
            valid.append(dict(attempt))
        else:
            replacement = choose_replacement(
                valid=valid,
                pending=pending,
                candidates=candidates,
                attempted_ids=attempted_ids | {str(item["subset_id"]) for item in pending},
                low_ids=low_ids,
                high_ids=high_ids,
            )
            pending.append(replacement)
            attempt["replacement_subset_id"] = replacement["subset_id"]
            append_ledger(
                {
                    "event": "response_span_point_reconstructed",
                    "failed_subset_id": subset_id,
                    "replacement_subset_id": replacement["subset_id"],
                    "replacement_response_roles": replacement["response_roles"],
                    "at": now(),
                }
            )
        state["valid_points"] = valid
        state["pending_subset_ids"] = [item["subset_id"] for item in pending]
        state["current"] = None
        write_json(QUEUE_STATE_PATH, state)
        append_ledger({"event": "response_span_point_complete", **attempt})
        print("RESPONSE_SPAN_POINT_COMPLETE=" + json.dumps(attempt, sort_keys=True), flush=True)

    state["completed_at_utc"] = now()
    state["current"] = None
    if len(valid) == REQUIRED_VALID_POINTS and set_is_legal(valid, low_ids, high_ids):
        state["status"] = "complete"
        state["valid_set_constraints_passed"] = True
        write_json(QUEUE_STATE_PATH, state)
        print(
            "RESPONSE_SPAN_QUEUE_COMPLETE="
            + json.dumps(
                {
                    "valid_count": len(valid),
                    "valid_ordinals": [item["ordinal"] for item in valid],
                    "valid_subset_ids": [item["subset_id"] for item in valid],
                    "counter": current_counter(),
                    "selection_sha256": selection_sha,
                },
                sort_keys=True,
            ),
            flush=True,
        )
        return 0
    state["status"] = "failed_insufficient_legal_points"
    state["valid_set_constraints_passed"] = False
    write_json(QUEUE_STATE_PATH, state)
    raise RuntimeError(
        f"only {len(valid)} valid calibration points after {len(attempted_ids)} attempts"
    )


def extreme_ids(
    selection: Mapping[str, Any],
) -> Tuple[Dict[str, set[str]], Dict[str, set[str]]]:
    count = int(selection["extreme_count_per_resource_end"])
    candidates = list(selection.get("all_predicted_candidates") or [])
    low: Dict[str, set[str]] = {}
    high: Dict[str, set[str]] = {}
    for resource in RESOURCES:
        ordered = sorted(
            candidates,
            key=lambda item: (
                float(item["predicted_formula_resource"][resource]),
                int(item["rank"]),
            ),
        )
        low[resource] = {str(item["subset_id"]) for item in ordered[:count]}
        high[resource] = {str(item["subset_id"]) for item in ordered[-count:]}
    return low, high


if __name__ == "__main__":
    raise SystemExit(main())
