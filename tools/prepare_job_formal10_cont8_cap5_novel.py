#!/usr/bin/env python3
"""Prepare JOB continuation 8 with a real cap and initial novelty gate."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping


BASE = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "job_node02/history/job/outer/attempt_7_cap5_contract_20260726"
)
LANE = Path(
    "/mnt/nvme2n1/anonymous/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "job_node02"
)
PREVIOUS = BASE / "formal10_after_round10_gate_cont7_feedback_v1_20260729T1645"
OUTPUT = BASE / "formal10_after_round10_gate_cont8_cap5_novel_v1_20260729T1738"
HISTORY = PREVIOUS / "optimizer_history_2026-07-29_164828.jsonl"
WORKSPACE_OPTIMIZER = LANE / "workspace_attempt7/parameter_optimizer.py"
ACCEPTED = BASE / "formal10_accepted_counter.txt"
REPLAY = BASE / "formal_search_replay_counter.txt"
GLOBAL = BASE / "replay_counter.txt"

EXPECTED = {
    PREVIOUS / "config.json":
        "a306c0d0badb4fcf94728473bfdc1ccf86a41cfff49eea1af12489b4de1ad449",
    HISTORY:
        "499c9b90e730d181c9062fef494a9ef046ce0514f3cfc57fb231e90714b4b576",
    PREVIOUS / "rejected_baseline_restore_readback.txt":
        "a62d0e5450185391d221362a79eee35223e8700f9cb7438b13ba2b51c738e532",
    BASE / "formal_points/point_0204/point_result.json":
        "f63188f93e0ca3287a8ae5e4e727945a8990abbfe82d4b269e412a258c48f6b8",
    BASE / "formal_points/point_0205/point_result.json":
        "865b72df53ffd684afa236d4809d1975d4e903abdfa9a6693cd8f04c46fcc43a",
    BASE / "formal_points/point_0206/point_result.json":
        "cdab9995eb4ad9a6d624a5ea78454c6daaf321d166e5099425d8fec8b3b4cd62",
    BASE / "formal_points/point_0207/point_result.json":
        "9835ae21f7a083c80b1c49e28139b71224b868c9799ed87783cd8edab26447fc",
    BASE / "formal_points/point_0208/point_result.json":
        "c1d291d274d4ad3daa59dea8a09f1a3af309640443e2a94cad4d452ec9a70585",
    WORKSPACE_OPTIMIZER:
        "c54b9dba238bb03c5333c0dbbb780299f0ecaf09de2d8de3c590ba693c1c0ed2",
}
BASELINE_PARAMS = {
    "cpu_index_tuple_cost": 0.0005,
    "cpu_operator_cost": 0.0025,
    "cpu_tuple_cost": 0.01,
    "parallel_setup_cost": 7000.0,
    "parallel_tuple_cost": 0.001,
    "random_page_cost": 4.0,
    "seq_page_cost": 1.0,
}
REPLAYED_FINGERPRINTS = [
    "three-level-sha256:cf341a2b1ead0cc6fee278017ac4e479e6db8dd334fae42eb379faafe45a9ea2",
    "three-level-sha256:cacc58228b090d8ea5eeab0f8c693f01a6088084ecdd88ec4d3c15c829bcf622",
    "three-level-sha256:db17f405d4967a1a9f9b0182e94f1f9bb5a10b0d27c3da35031980a8828467a8",
    "three-level-sha256:81f220c14d73a694fd734d56e88db77003d1ead13a76c4cf41e8fa5fb722222d",
    "three-level-sha256:2c7310b05c85f054fd7acc39113a146b41a0c1ff9b7eda2bc4c38a86b338a9fd",
    "three-level-sha256:cf47bde09ff52bce832269ad0b18a678ff99d1ab9bd0db8adcdbc5f0aac0e460",
    "three-level-sha256:3c34c171ee15adf8d42a82556243efbc31e29e4930c6e9b9048357ead30fde89",
    "three-level-sha256:858c812c94dfa996209f2218f6a275ef483dfcd75906a38411aed4125c92c206",
]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def read_int(path: Path) -> int:
    return int(path.read_text(encoding="utf-8").strip())


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=str(path.parent), text=True
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    atomic_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def normalized_params(value: Mapping[str, Any]) -> dict[str, float]:
    return {str(name): float(item) for name, item in value.items()}


def main() -> int:
    if OUTPUT.exists():
        raise FileExistsError(f"continuation output already exists: {OUTPUT}")
    for path, expected_sha in EXPECTED.items():
        actual_sha = sha256(path)
        if actual_sha != expected_sha:
            raise RuntimeError(f"SHA mismatch: {path}: {actual_sha} != {expected_sha}")
    if (PREVIOUS / "driver_exit_code.txt").read_text(
        encoding="utf-8"
    ).strip() != "143":
        raise RuntimeError("cont7 did not end through the sealed cap watchdog")
    counters = {
        "accepted": read_int(ACCEPTED),
        "formal_replay": read_int(REPLAY),
        "global_ordinal": read_int(GLOBAL),
    }
    expected_counters = {
        "accepted": 0,
        "formal_replay": 149,
        "global_ordinal": 208,
    }
    if counters != expected_counters:
        raise RuntimeError(f"counter mismatch: {counters} != {expected_counters}")

    points: list[dict[str, Any]] = []
    replayed_params: list[dict[str, float]] = []
    for ordinal in range(204, 209):
        path = BASE / f"formal_points/point_{ordinal:04d}/point_result.json"
        point = load_json(path)
        measurement = point.get("measurement") or {}
        stability = measurement.get("resource_stability") or {}
        params = normalized_params(point.get("cost_parameters") or {})
        if (
            point.get("status") != "complete"
            or measurement.get("correctness_passed") is not True
            or stability.get("passed") is not True
            or set(params) != set(BASELINE_PARAMS)
        ):
            raise RuntimeError(f"point_{ordinal:04d} is not valid stable evidence")
        replayed_params.append(params)
        points.append({
            "ordinal": ordinal,
            "path": str(path),
            "sha256": EXPECTED[path],
            "params": params,
            "db_qps": float(measurement["db_qps"]),
            "stable": True,
            "correctness_passed": True,
        })
    baseline_qps = points[0]["db_qps"]
    point_208 = points[-1]
    if not point_208["db_qps"] < baseline_qps:
        raise RuntimeError("point0208 no longer satisfies rejected DB-QPS evidence")
    if normalized_params(points[0]["params"]) != BASELINE_PARAMS:
        raise RuntimeError("point0204 baseline parameters changed")

    config = load_json(PREVIOUS / "config.json")
    if config["initial_params"] != BASELINE_PARAMS:
        raise RuntimeError("cont7 initial baseline changed")
    protocol = config["formal_outer_protocol"]
    protocol.update({
        "formal_search_accepted_count_start": 0,
        "formal_search_accepted_counter_path": str(ACCEPTED),
        "formal_search_replay_count_start": 149,
        "initial_real_replay_count": 149,
        "formal_search_replay_counter_path": str(REPLAY),
        "global_replay_ordinal_at_binding": 208,
        "global_replay_counter_path": str(GLOBAL),
        "formal_startup_baseline_count_start": 0,
        "formal_startup_baseline_counter_path": str(
            OUTPUT / "state/formal_startup_baseline_counter.txt"
        ),
        "formal_search_attempt_safety_cap": 154,
        "formal_search_maximum_replay_count": 154,
        "maximum_cumulative_real_replay_count": 154,
        "maximum_non_default_real_candidates": 5,
        "maximum_resource_qualified_real_replays_per_batch": 5,
        "global_real_replay_top_k_hard_cap": 5,
        "excluded_prior_three_level_fingerprints": REPLAYED_FINGERPRINTS,
        "excluded_prior_parameter_signatures": replayed_params,
    })
    feedback = config["final_evaluation"]["feedback_loop"]
    if feedback.get("enabled") is not True:
        raise RuntimeError("formal feedback state machine is disabled")
    feedback["max_rounds"] = 1
    feedback["stop_after_primary_acceptance"] = True
    feedback["continue_after_primary_acceptance_rounds"] = 0
    feedback["select_best_primary_by_db_qps"] = False
    feedback["resource_iteration_goal"]["strong_constraints"]["enabled"] = False
    if config["final_evaluation"].get("run_validation") is not True:
        raise RuntimeError("formal validation state machine is disabled")
    config["optimizer"]["gradient"]["subset_log_dir"] = str(
        OUTPUT / "subset_logs"
    )
    config["output"]["directory"] = str(OUTPUT)
    config["logging"]["history_path"] = str(OUTPUT / "optimizer_history.jsonl")
    config["_job_formal_continuation"] = {
        "schema_version": "job_formal10_cont8_cap5_novel_v1",
        "previous_formal_dir": str(PREVIOUS),
        "previous_config_sha256": EXPECTED[PREVIOUS / "config.json"],
        "previous_history_sha256": EXPECTED[HISTORY],
        "accepted_count_start": 0,
        "formal_replay_count_start": 149,
        "global_replay_ordinal_at_binding": 208,
        "absolute_replay_cap": 154,
        "batch_physical_cap": 5,
        "initial_candidate_novelty_gate_enabled": True,
        "replayed_point_params": replayed_params,
        "excluded_prior_three_level_fingerprints": REPLAYED_FINGERPRINTS,
        "qps_used_for_inner_scoring": False,
        "rejected_requires_baseline_restore": True,
    }

    OUTPUT.mkdir(parents=True)
    atomic_text(OUTPUT / "state/formal_startup_baseline_counter.txt", "0\n")
    quarantine = {
        "schema_version": 1,
        "event": "job_point0208_zero_call_reclassification",
        "source_point": point_208,
        "baseline_point": points[0],
        "classification": "formal_rejected_completed_before_controller_cap_stop",
        "accepted": False,
        "accepted_update_applied": False,
        "incumbent_update_applied": False,
        "range_update_applied": False,
        "gradient_update_applied": False,
        "physical_call_already_counted": True,
        "formal_replay_counter_after": 149,
        "global_ordinal_after": 208,
        "new_workload_calls": 0,
        "reason": "db_qps_not_strictly_higher_than_baseline",
    }
    atomic_json(OUTPUT / "point0208_zero_call_quarantine_audit.json", quarantine)
    config_path = OUTPUT / "config.json"
    atomic_json(config_path, config)
    manifest = {
        "schema_version": "job_formal10_cont8_cap5_novel_manifest_v1",
        "output_config": str(config_path),
        "output_config_sha256": sha256(config_path),
        "parameter_optimizer_sha256": EXPECTED[WORKSPACE_OPTIMIZER],
        "accepted_count_start": 0,
        "formal_replay_count_start": 149,
        "global_replay_ordinal_at_binding": 208,
        "absolute_replay_cap": 154,
        "maximum_physical_calls_this_batch": 5,
        "initial_candidate_novelty_gate_enabled": True,
        "excluded_fingerprint_count": len(REPLAYED_FINGERPRINTS),
        "excluded_parameter_signature_count": len(replayed_params),
        "point0208_quarantine_sha256": sha256(
            OUTPUT / "point0208_zero_call_quarantine_audit.json"
        ),
        "qps_used_for_inner_scoring": False,
    }
    atomic_json(OUTPUT / "continuation_manifest.json", manifest)
    print(json.dumps(manifest, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
