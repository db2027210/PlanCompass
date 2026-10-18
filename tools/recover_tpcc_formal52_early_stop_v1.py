#!/usr/bin/env python3
"""Recover TPC-C formal52 after the infeasible-batch early stop.

The rank-4 point process outlived the sealed parent.  This tool binds its
completed summary to the sealed candidate with zero new workload calls,
restores the TPC-C baseline, and retires the partial formal batch without
changing accepted/incumbent/range state.
"""

from __future__ import annotations

import copy
import fcntl
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Mapping, Tuple


PROJECT = Path("/home/anonymous/program/resource_adaptive")
CAMPAIGN = PROJECT / "history/tpcc_final_algorithm_fresh10_v29_20260731T2005"
FORMAL_STATE = CAMPAIGN / "formal_state.json"
CONTROLLER_STATE = CAMPAIGN / "autonomous_controller_v32/controller_state.json"
RUN_ROOT = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/"
    "formal10_final_algorithm_fresh10_v29_20260731T2005/autonomous_runs_v32/"
    "autonomous_formal_52"
)
REPLAY = RUN_ROOT / "top5_real_replay_round14_formal"
SELECTION = RUN_ROOT / "top5.json"
LEDGER = RUN_ROOT / "strict_top5_real_replay_round14_ledger_v1.json"
EVENTS = REPLAY / "events.jsonl"
LOCK = RUN_ROOT / "strict_top5_real_replay.lock"
POINT_PARENT = REPLAY / "rank_04/attempt_01"
POINT_ROOTS = sorted(POINT_PARENT.glob("point_*"))
if len(POINT_ROOTS) != 1:
    raise RuntimeError(
        f"expected exactly one formal52 rank4 point root: {POINT_ROOTS}"
    )
POINT_ROOT = POINT_ROOTS[0]
SUMMARY = POINT_ROOT / "summary.json"
POINT_RESULT = POINT_PARENT / "point_result.json"
RUNNER_PATH = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/"
    "gradient_debug_round14_robust_regime_interval_v2_20260729T1824/"
    "run_tpcc_round11_calibrated_top5_strict_serial.py"
)

EXPECTED_SHA256 = {
    "selection": "2e27c1e280061c2f98846c93d8393c524122be264d943fe06aadbdb1e44118dc",
    "ledger": "9daff9375ea4fb286c6bdb9562eacd08adda30c3eb19824418354a5bd8bd5173",
    "events": "08485e5346b43e53c420aa870f1650bed98b16a1b3eebdc9de98433845b9788e",
    "summary": "83cf42d46a8733aca05533c13eb69f6f1b02d88ea5738245865f0529e1f6a6cc",
    "controller_state": "a605699d41d7f67418da306a1c4347a3fe4bdde562bb212d423236816e1b0c6e",
    "formal_state": "8575cb37b666afda71ab0bf4ffeb85cf007ca919ca2143709d014fac34b539ca",
    "runner": "df1f9f9b43656214cdccace0e3614fada7d2efc7c7679942da6478ac0f042d68",
}


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def maximum_median_after_one_observation(scores: List[float]) -> float:
    ordered = sorted(float(value) for value in scores)
    if not ordered:
        raise RuntimeError("formal feasibility history is empty")
    midpoint = len(ordered) // 2
    if len(ordered) % 2 == 0:
        return ordered[midpoint]
    return (ordered[midpoint] + ordered[midpoint + 1]) / 2.0


def atomic_bytes(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with temporary.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def import_runner() -> Any:
    sys.path.insert(0, str(PROJECT))
    sys.path.insert(0, str(RUNNER_PATH.parent))
    specification = importlib.util.spec_from_file_location(
        "tpcc_validation51_runner", RUNNER_PATH
    )
    if specification is None or specification.loader is None:
        raise RuntimeError("cannot import sealed TPC-C runner")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def load_events(original: bytes) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for number, line in enumerate(original.decode("utf-8").splitlines(), 1):
        value = json.loads(line)
        if not isinstance(value, dict):
            raise RuntimeError(f"event line {number} is not an object")
        rows.append(value)
    return rows


def start_bindings(events: List[Dict[str, Any]]) -> Dict[Tuple[int, int], Dict[str, Any]]:
    return {
        (int(event["rank"]), int(event["attempt"])): event
        for event in events
        if event.get("event") == "tpcc_gradient_real_replay_started"
    }


def reconstruct_payload(summary: Mapping[str, Any]) -> Dict[str, Any]:
    utilization = summary.get("resource_utilization") or {}
    point_id = POINT_ROOT.name
    latency = float(summary["sensitive_latency_ms"])
    protocol = {
        "algorithmic_gate_rejection": bool(
            (summary.get("correctness_gate") or {}).get(
                "algorithmic_gate_rejection"
            )
        ),
        "complete_window_count": int(
            (summary.get("window_mean_stability") or {}).get(
                "consecutive_stable_windows"
            )
        ),
        "correctness_gate": copy.deepcopy(summary["correctness_gate"]),
        "correctness_passed": bool(summary["correctness_passed"]),
        "effective_measurement_seconds": float(
            summary["effective_measurement_seconds"]
        ),
        "initial_probe_seconds": 180,
        "maximum_measurement_seconds": 600,
        "minimum_measurement_seconds": 180,
        "point_id": point_id,
        "qps_used_for_inner_scoring": False,
        "runner_sha256": "bf03a7b4e299798739f4d92393b01db2c89c7cd266b58d12dad6737d5b0643ee",
        "stability_contract_sha256": (
            "4b90d0126806ac16031fafbfe905d3aa3d693ddf28b64717eb348fb0911d4692"
        ),
        "stability_target_resource": summary["stability_target_resource"],
        "stable": bool((summary.get("window_mean_stability") or {}).get("stable")),
        "stop_reason": summary["stop_reason"],
        "summary_path": str(SUMMARY),
        "summary_schema": "tpcc_sensitive_summary_v1",
        "summary_sha256": sha256(SUMMARY),
        "technical_failure": bool(summary["technical_failure"]),
        "throughput_stability_required": False,
        "workload_contract": "tpcc_sensitive_stable_summary_v2",
    }
    return {
        "benchmark": {
            "db_qps": float(summary["db_qps"]),
            "native_tps": float(summary["native_tps"]),
            "sensitive_tps": float(summary["sensitive_tps"]),
            "tp_tps": float(summary["combined_tps"]),
            "xp_qps": float(summary["combined_tps"]),
            "xp_tps": float(summary["combined_tps"]),
        },
        "benchmark_latency": {
            "all_p90_avg": latency,
            "all_p90_max": latency,
            "all_p95_avg": latency,
            "all_p95_max": latency,
            "all_p99_avg": latency,
            "all_p99_max": latency,
        },
        "candidate_measurement_protocol": protocol,
        "formal_pg_resource": {
            "aggregate": {
                "cpu_utilization": float(utilization["cpu"]),
                "memory_utilization": float(utilization["memory"]),
                "io_utilization": float(utilization["io"]),
            },
            "samples_path": str(POINT_ROOT / "resource_samples.jsonl"),
        },
    }


def main() -> int:
    observed = {
        "selection": sha256(SELECTION),
        "ledger": sha256(LEDGER),
        "events": sha256(EVENTS),
        "summary": sha256(SUMMARY),
        "controller_state": sha256(CONTROLLER_STATE),
        "formal_state": sha256(FORMAL_STATE),
        "runner": sha256(RUNNER_PATH),
    }
    if observed != EXPECTED_SHA256:
        raise RuntimeError(f"TPC-C formal52 recovery SHA mismatch: {observed}")
    if POINT_RESULT.exists():
        raise RuntimeError("rank4 point_result already exists")
    if (POINT_ROOT.parent / "workload.log").exists():
        raise RuntimeError("rank4 unexpectedly has a parent runner workload log")

    lock_handle = LOCK.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        runner = import_runner()
        ledger_original = LEDGER.read_bytes()
        events_original = EVENTS.read_bytes()
        controller_original = CONTROLLER_STATE.read_bytes()
        ledger = load_json(LEDGER)
        events = load_events(events_original)
        selection = load_json(SELECTION)
        formal_state = load_json(FORMAL_STATE)
        controller_state = load_json(CONTROLLER_STATE)
        candidates = {
            int(item["rank"]): item for item in selection.get("top5") or []
        }
        starts = start_bindings(events)
        start = starts.get((4, 1)) or {}
        candidate = candidates.get(4) or {}
        candidate_params = candidate.get("candidate_params") or candidate.get(
            "optimized_params"
        )
        if (
            set(candidates) != {1, 2, 3, 4, 5}
            or int(ledger.get("real_workload_invocation_count", -1)) != 4
            or [int(item["rank"]) for item in ledger.get("successful") or []]
            != [1, 2, 3]
            or ledger.get("technical_failures") not in ([], None)
            or runner.count_real_workload_invocations(EVENTS) != 4
            or runner.load_invoked_ranks(EVENTS) != [1, 2, 3, 4]
            or (start.get("candidate") or {}).get("candidate_params")
            != candidate_params
            or (start.get("candidate") or {}).get("three_level_fingerprint")
            != candidate.get("three_level_fingerprint")
        ):
            raise RuntimeError("TPC-C orphan rank4 binding/accounting mismatch")

        current_run = controller_state.get("current_run") or {}
        if (
            int(formal_state.get("accepted_count", -1)) != 2
            or not math.isclose(
                float(formal_state.get("incumbent_score", math.nan)),
                4.708709193584568,
                rel_tol=0.0,
                abs_tol=1e-12,
            )
            or int(controller_state.get("next_ordinal", -1)) != 53
            or current_run.get("mode") != "formal"
            or Path(str(current_run.get("root") or "")).resolve() != RUN_ROOT
            or controller_state.get("status") != "launching_sealed_run"
        ):
            raise RuntimeError("TPC-C formal/controller state handoff mismatch")

        incumbent_score = float(formal_state["incumbent_score"])
        feasibility_rows = [
            {
                "rank": int(item["rank"]),
                "upper_bound": maximum_median_after_one_observation(
                    [float(value) for value in item["independent_actual_scores"]]
                ),
            }
            for item in selection["top5"]
        ]
        if any(
            row["upper_bound"] > incumbent_score + 1e-12
            for row in feasibility_rows
        ):
            raise RuntimeError("formal52 is not mathematically infeasible")

        summary = load_json(SUMMARY)
        payload = reconstruct_payload(summary)
        utilization, protocol, measurement_summary = (
            runner.normalize_measurement_payload(payload, POINT_ROOT.parent)
        )
        if (
            protocol.get("stable") is not True
            or protocol.get("correctness_passed") is not True
            or protocol.get("technical_failure") is not False
            or summary.get("status") != "complete"
        ):
            raise RuntimeError("orphan rank4 is not a valid stable replay")
        measured_score = runner.actual_score(utilization)
        result = {
            "rank": 4,
            "attempt": 1,
            "selection_role": candidate.get("selection_role", "legacy"),
            "trusted": candidate.get("trusted"),
            "diagnostic_only": True,
            "eligible_for_incumbent_or_accepted": False,
            "candidate_params": candidate_params,
            "predicted_score": candidate["candidate_score"],
            "predicted_score_delta": candidate.get("candidate_score_delta"),
            "feature_ratio": candidate["feature_ratio"],
            "predicted_resource": candidate["predicted_resource"],
            "workload_plan_fingerprint": candidate["workload_plan_fingerprint"],
            "three_level_fingerprint": candidate["three_level_fingerprint"],
            "actual_utilization": utilization,
            "actual_resource": {
                name: float(utilization[name]) * runner.CAPABILITY[name]
                for name in runner.CAPABILITY
            },
            "actual_score": measured_score,
            "actual_score_delta_vs_baseline": None,
            "workload_returncode": 0,
            "algorithmic_gate_rejection": bool(
                protocol.get("algorithmic_gate_rejection")
            ),
            "offline_plateau": bool(candidate["boundary_plateau"]),
            "incumbent_update_eligible": False,
            "consumes_successful_replay_budget": True,
            "consumes_technical_failure_budget": False,
            "benchmark": payload["benchmark"],
            "benchmark_latency": payload["benchmark_latency"],
            "measurement_protocol": protocol,
            "measurement_summary": measurement_summary,
            "payload": payload,
            "zero_call_reconstructed_after_parent_exit": True,
        }
        successful = [*(ledger.get("successful") or []), result]
        ledger["successful"] = successful
        ledger["technical_failures"] = []
        ledger["real_workload_invocation_count"] = 4
        ledger["formal52_early_stop_recovery"] = {
            "contract": "tpcc_formal52_infeasible_early_stop_zero_call_v1",
            "new_workload_calls": 0,
            "rank": 4,
            "attempt": 1,
            "summary_path": str(SUMMARY),
            "summary_sha256": sha256(SUMMARY),
            "accepted_update_applied": False,
            "incumbent_update_applied": False,
            "range_update_applied": False,
            "gradient_update_applied": False,
        }
        event = {
            "event": "tpcc_formal52_orphan_rank4_zero_call_recovered",
            **result,
            "new_workload_calls": 0,
            "timestamp_epoch": time.time(),
        }
        events_new = events_original
        if events_new and not events_new.endswith(b"\n"):
            events_new += b"\n"
        events_new += (json.dumps(event, sort_keys=True) + "\n").encode("utf-8")
        ledger_new = json_bytes(ledger)
        point_result_new = json_bytes(result)

        if "--check-only" in sys.argv[1:]:
            print(
                json.dumps(
                    {
                        "status": "preflight_passed",
                        "new_workload_calls": 0,
                        "physical_calls": 4,
                        "valid_replays": 4,
                        "technical_failures": 0,
                        "rank4_actual_score": measured_score,
                        "incumbent_score": incumbent_score,
                        "feasibility_rows": feasibility_rows,
                        "ledger_sha256_after": sha256_bytes(ledger_new),
                        "point_result_sha256": sha256_bytes(point_result_new),
                    },
                    indent=2,
                    sort_keys=True,
                ),
                flush=True,
            )
            return 0

        snapshot_restore_output = runner.restore_database()
        baseline_param_readback = runner.apply_params(runner.BASELINE_PARAMS)
        if any(
            not math.isclose(
                float(baseline_param_readback[name]),
                float(value),
                # PostgreSQL renders cost GUCs with roughly six significant
                # digits; apply_params has already enforced this tolerance.
                rel_tol=1e-5,
                abs_tol=1e-12,
            )
            for name, value in runner.BASELINE_PARAMS.items()
        ):
            raise RuntimeError("TPC-C baseline parameter restore mismatch")

        physical_fingerprints = [
            candidates[int(item["rank"])]["three_level_fingerprint"]["id"]
            for item in successful
        ]
        audit_record = {
            "mode": "formal",
            "root": str(RUN_ROOT),
            "clean": False,
            "algorithmic_early_stop": True,
            "algorithmic_early_stop_reason": (
                "all_sealed_candidates_have_median_acceptance_upper_bound_"
                "at_or_below_incumbent"
            ),
            "physical_calls": 4,
            "valid_replays": 4,
            "technical_failures": 0,
            "baseline_restored": True,
            "accepted_before": 2,
            "accepted_after": 2,
            "batch_added_accepted": 0,
            "formal_prediction_miss": True,
            "incumbent_score": incumbent_score,
            "formal_acceptance_feasibility_gate": {
                "contract": "one_additional_observation_median_upper_bound_v1",
                "candidate_bounds": feasibility_rows,
                "feasible_ranks": [],
                "passed": False,
            },
            "fingerprint_ids": [
                item["three_level_fingerprint"]["id"]
                for item in selection["top5"]
            ],
            "physical_fingerprint_ids": physical_fingerprints,
            "selection_path": str(SELECTION),
            "selection_sha256": EXPECTED_SHA256["selection"],
            "ledger_path": str(LEDGER),
            "ledger_sha256": sha256_bytes(ledger_new),
            "new_workload_calls_during_recovery": 0,
        }
        controller_new = copy.deepcopy(controller_state)
        controller_new.setdefault("audits", []).append(audit_record)
        controller_new.setdefault("acquisition_transitions", []).append(
            {
                "event": "formal52_infeasible_early_stop_recovered",
                "ordinal": 52,
                "workload_calls_added_by_recovery": 0,
                "physical_calls_preserved": 4,
                "valid_replays_preserved": 4,
                "accepted_count": 2,
                "formal_state_sha256": EXPECTED_SHA256["formal_state"],
                "feasibility_gate": copy.deepcopy(
                    audit_record["formal_acceptance_feasibility_gate"]
                ),
            }
        )
        controller_new["current_run"] = None
        controller_new["status"] = "formal52_infeasible_early_stop_recovered"
        controller_new["next_mode"] = "diagnostic"
        controller_new["formal_state_sha256"] = EXPECTED_SHA256["formal_state"]
        controller_new.pop("last_launcher_returncode", None)
        controller_new_bytes = json_bytes(controller_new)

        timestamp = time.strftime("%Y%m%dT%H%M%S")
        ledger_backup = LEDGER.with_name(f"{LEDGER.name}.before_orphan_{timestamp}.bak")
        events_backup = EVENTS.with_name(f"{EVENTS.name}.before_orphan_{timestamp}.bak")
        controller_backup = CONTROLLER_STATE.with_name(
            f"{CONTROLLER_STATE.name}.before_formal52_recovery_{timestamp}.bak"
        )
        ledger_backup.write_bytes(ledger_original)
        events_backup.write_bytes(events_original)
        controller_backup.write_bytes(controller_original)
        try:
            atomic_bytes(POINT_RESULT, point_result_new)
            atomic_bytes(LEDGER, ledger_new)
            atomic_bytes(EVENTS, events_new)
            atomic_bytes(CONTROLLER_STATE, controller_new_bytes)
        except BaseException:
            atomic_bytes(LEDGER, ledger_original)
            atomic_bytes(EVENTS, events_original)
            atomic_bytes(CONTROLLER_STATE, controller_original)
            if POINT_RESULT.exists():
                POINT_RESULT.unlink()
            raise

        if (
            sha256(FORMAL_STATE) != EXPECTED_SHA256["formal_state"]
            or runner.count_real_workload_invocations(EVENTS) != 4
            or runner.load_invoked_ranks(EVENTS) != [1, 2, 3, 4]
            or [
                int(item["rank"])
                for item in load_json(LEDGER).get("successful") or []
            ]
            != [1, 2, 3, 4]
            or load_json(CONTROLLER_STATE).get("current_run") is not None
            or load_json(CONTROLLER_STATE).get("next_mode") != "diagnostic"
        ):
            raise RuntimeError("TPC-C formal52 recovery post-write verification failed")
        audit = {
            "status": "applied_and_verified",
            "contract": "tpcc_formal52_infeasible_early_stop_zero_call_v1",
            "new_workload_calls": 0,
            "physical_calls": 4,
            "valid_replays": 4,
            "technical_failures": 0,
            "remaining_physical_budget": 1,
            "formal_state_sha256": sha256(FORMAL_STATE),
            "formal_state_mutated": False,
            "ledger_sha256_before": EXPECTED_SHA256["ledger"],
            "ledger_sha256_after": sha256(LEDGER),
            "events_sha256_before": EXPECTED_SHA256["events"],
            "events_sha256_after": sha256(EVENTS),
            "point_result_sha256": sha256(POINT_RESULT),
            "ledger_backup": str(ledger_backup),
            "events_backup": str(events_backup),
            "controller_state_sha256_before": EXPECTED_SHA256[
                "controller_state"
            ],
            "controller_state_sha256_after": sha256(CONTROLLER_STATE),
            "controller_state_backup": str(controller_backup),
            "snapshot_restore_output": snapshot_restore_output,
            "baseline_param_readback": baseline_param_readback,
        }
        atomic_bytes(RUN_ROOT / "early_stop_recovery_audit.json", json_bytes(audit))
        print(json.dumps(audit, indent=2, sort_keys=True), flush=True)
        return 0
    finally:
        lock_handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
