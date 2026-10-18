#!/usr/bin/env python3
"""Zero-call recovery of TPC-C validation51's completed orphan rank3."""

from __future__ import annotations

import copy
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Mapping, Tuple


PROJECT = Path("/home/anonymous/program/resource_adaptive")
CAMPAIGN = PROJECT / "history/tpcc_final_algorithm_fresh10_v29_20260731T2005"
FORMAL_STATE = CAMPAIGN / "formal_state.json"
RUN_ROOT = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/"
    "formal10_final_algorithm_fresh10_v29_20260731T2005/autonomous_runs_v32/"
    "autonomous_diagnostic_validation_51"
)
REPLAY = RUN_ROOT / "top5_real_replay_round14_diagnostic_v1"
SELECTION = RUN_ROOT / "top5.json"
LEDGER = RUN_ROOT / "strict_top5_real_replay_round14_ledger_v1.json"
EVENTS = REPLAY / "events.jsonl"
LOCK = RUN_ROOT / "strict_top5_real_replay.lock"
POINT_ROOT = (
    REPLAY
    / "rank_03/attempt_01/point_20260801_134653_1914962"
)
SUMMARY = POINT_ROOT / "summary.json"
POINT_RESULT = POINT_ROOT.parent / "point_result.json"
RUNNER_PATH = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt12_plan_cluster_fallback_20260726/"
    "gradient_debug_round14_robust_regime_interval_v2_20260729T1824/"
    "run_tpcc_round11_calibrated_top5_strict_serial.py"
)

EXPECTED_SHA256 = {
    "selection": "3989da61ee53ded02d1627ae3a55ff361bf057c9927bf8b747fe2c039c27881c",
    "ledger": "d1a9c007f2334a727158564155b24c76dd30f16fe144a20ede48ea891a63540b",
    "events": "c6599f5613f675063dfbf1109a6d08c60ab9c54cbaccb4a0d380b2e30fc43501",
    "summary": "dc03772d9bfaa121ed2fb64953f4b2ef1fb2caad69eb2280d4d7a71885cf23c7",
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


def atomic_bytes(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with temporary.open("xb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def import_runner() -> Any:
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
        "formal_state": sha256(FORMAL_STATE),
        "runner": sha256(RUNNER_PATH),
    }
    if observed != EXPECTED_SHA256:
        raise RuntimeError(f"TPC-C orphan recovery SHA mismatch: {observed}")
    if POINT_RESULT.exists():
        raise RuntimeError("rank3 point_result already exists")
    if (POINT_ROOT.parent / "workload.log").exists():
        raise RuntimeError("rank3 unexpectedly has a parent runner workload log")

    lock_handle = LOCK.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        runner = import_runner()
        ledger_original = LEDGER.read_bytes()
        events_original = EVENTS.read_bytes()
        ledger = load_json(LEDGER)
        events = load_events(events_original)
        selection = load_json(SELECTION)
        candidates = {
            int(item["rank"]): item for item in selection.get("top5") or []
        }
        starts = start_bindings(events)
        start = starts.get((3, 1)) or {}
        candidate = candidates.get(3) or {}
        candidate_params = candidate.get("candidate_params") or candidate.get(
            "optimized_params"
        )
        if (
            set(candidates) != {1, 2, 3, 4, 5}
            or int(ledger.get("real_workload_invocation_count", -1)) != 3
            or [int(item["rank"]) for item in ledger.get("successful") or []]
            != [1, 2]
            or ledger.get("technical_failures") not in ([], None)
            or runner.count_real_workload_invocations(EVENTS) != 3
            or runner.load_invoked_ranks(EVENTS) != [1, 2, 3]
            or (start.get("candidate") or {}).get("candidate_params")
            != candidate_params
            or (start.get("candidate") or {}).get("three_level_fingerprint")
            != candidate.get("three_level_fingerprint")
        ):
            raise RuntimeError("TPC-C orphan rank3 binding/accounting mismatch")

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
            raise RuntimeError("orphan rank3 is not a valid stable replay")
        measured_score = runner.actual_score(utilization)
        result = {
            "rank": 3,
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
        ledger["real_workload_invocation_count"] = 3
        ledger["orphan_rank3_recovery"] = {
            "contract": "tpcc_validation51_orphan_rank3_zero_call_v1",
            "new_workload_calls": 0,
            "rank": 3,
            "attempt": 1,
            "summary_path": str(SUMMARY),
            "summary_sha256": sha256(SUMMARY),
            "accepted_update_applied": False,
            "incumbent_update_applied": False,
            "range_update_applied": False,
            "gradient_update_applied": False,
        }
        event = {
            "event": "tpcc_validation51_orphan_rank3_zero_call_recovered",
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

        timestamp = time.strftime("%Y%m%dT%H%M%S")
        ledger_backup = LEDGER.with_name(f"{LEDGER.name}.before_orphan_{timestamp}.bak")
        events_backup = EVENTS.with_name(f"{EVENTS.name}.before_orphan_{timestamp}.bak")
        ledger_backup.write_bytes(ledger_original)
        events_backup.write_bytes(events_original)
        try:
            atomic_bytes(POINT_RESULT, point_result_new)
            atomic_bytes(LEDGER, ledger_new)
            atomic_bytes(EVENTS, events_new)
        except BaseException:
            atomic_bytes(LEDGER, ledger_original)
            atomic_bytes(EVENTS, events_original)
            if POINT_RESULT.exists():
                POINT_RESULT.unlink()
            raise

        if (
            sha256(FORMAL_STATE) != EXPECTED_SHA256["formal_state"]
            or runner.count_real_workload_invocations(EVENTS) != 3
            or runner.load_invoked_ranks(EVENTS) != [1, 2, 3]
            or [
                int(item["rank"])
                for item in load_json(LEDGER).get("successful") or []
            ]
            != [1, 2, 3]
        ):
            raise RuntimeError("TPC-C orphan recovery post-write verification failed")
        audit = {
            "status": "applied_and_verified",
            "contract": "tpcc_validation51_orphan_rank3_zero_call_v1",
            "new_workload_calls": 0,
            "physical_calls": 3,
            "valid_replays": 3,
            "technical_failures": 0,
            "remaining_physical_budget": 2,
            "formal_state_sha256": sha256(FORMAL_STATE),
            "formal_state_mutated": False,
            "ledger_sha256_before": EXPECTED_SHA256["ledger"],
            "ledger_sha256_after": sha256(LEDGER),
            "events_sha256_before": EXPECTED_SHA256["events"],
            "events_sha256_after": sha256(EVENTS),
            "point_result_sha256": sha256(POINT_RESULT),
            "ledger_backup": str(ledger_backup),
            "events_backup": str(events_backup),
        }
        atomic_bytes(RUN_ROOT / "orphan_rank3_recovery_audit.json", json_bytes(audit))
        print(json.dumps(audit, indent=2, sort_keys=True), flush=True)
        return 0
    finally:
        lock_handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
