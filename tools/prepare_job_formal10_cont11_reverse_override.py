#!/usr/bin/env python3
"""Prepare JOB continuation 11 with SHA-pinned reverse replay calibration."""

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
LANE = BASE.parents[3]
PREVIOUS = BASE / "formal10_after_round10_gate_cont10_reuse_r0_v1_20260729T1904"
OUTPUT = BASE / "formal10_after_round10_gate_cont11_reverse_override_v1_20260729T1921"
HISTORY = PREVIOUS / "optimizer_history_2026-07-29_190026.jsonl"
OPTIMIZER = LANE / "workspace_attempt7/parameter_optimizer.py"
ACCEPTED = BASE / "formal10_accepted_counter.txt"
REPLAY = BASE / "formal_search_replay_counter.txt"
GLOBAL = BASE / "replay_counter.txt"
POINT214 = BASE / "formal_points/point_0214/point_result.json"
POINT215 = BASE / "formal_points/point_0215/point_result.json"
EXPECTED = {
    PREVIOUS / "config.json":
        "250a029306b9bf29242f77d8a6471340866fd977e3a1779c15c14780e964b2cb",
    HISTORY:
        "a8502fdfcd6429d41b005756d65520dbb272cd96d020f9868588f3e978cf4d6b",
    PREVIOUS / "rejected_baseline_restore_readback.txt":
        "a62d0e5450185391d221362a79eee35223e8700f9cb7438b13ba2b51c738e532",
    POINT214:
        "8e98ee2e7340511a113ff0f72b31fcafa9c957667ad1879b0066bdd55e330d57",
    POINT215:
        "efbe78a8d6c3fe56a7031303c268fc081f261c1fd0509516f89d30db895cbf0a",
    OPTIMIZER:
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
POINT215_EXACT_PARAMS = {
    "cpu_index_tuple_cost": 0.0005,
    "cpu_operator_cost": 0.002625,
    "cpu_tuple_cost": 0.01043750437818371,
    "parallel_setup_cost": 7252.315440520575,
    "parallel_tuple_cost": 0.001,
    "random_page_cost": 3.9827032194733225,
    "seq_page_cost": 1.0,
}
POINT215_FINGERPRINT = (
    "three-level-sha256:41f2a85edf7d6a2fcb03ee0b286c224d"
    "f2d4e1680fb16cbfd61fd291f13156a8"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


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
    result = {str(name): float(item) for name, item in value.items()}
    if set(result) != set(BASELINE_PARAMS) or not all(
        math.isfinite(item) for item in result.values()
    ):
        raise RuntimeError("invalid cost parameter signature")
    return result


def main() -> int:
    if OUTPUT.exists():
        raise FileExistsError(f"output already exists: {OUTPUT}")
    for path, expected in EXPECTED.items():
        actual = sha256(path)
        if actual != expected:
            raise RuntimeError(f"SHA mismatch: {path}: {actual} != {expected}")
    if (PREVIOUS / "driver_exit_code.txt").read_text(
        encoding="utf-8"
    ).strip() != "0":
        raise RuntimeError("JOB cont10 did not exit cleanly")
    counters = {
        "accepted": int(ACCEPTED.read_text(encoding="utf-8")),
        "formal_replay": int(REPLAY.read_text(encoding="utf-8")),
        "global_ordinal": int(GLOBAL.read_text(encoding="utf-8")),
    }
    if counters != {"accepted": 0, "formal_replay": 156, "global_ordinal": 215}:
        raise RuntimeError(f"counter mismatch: {counters}")

    point214 = load_json(POINT214)
    point215 = load_json(POINT215)
    for point, expected_id in (
        (point214, "job_experience_table_outer_attempt1_0214"),
        (point215, "job_experience_table_outer_attempt1_0215"),
    ):
        measurement = point.get("measurement") or {}
        if (
            point.get("status") != "complete"
            or point.get("point_id") != expected_id
            or measurement.get("correctness_passed") is not True
            or (measurement.get("resource_stability") or {}).get("passed") is not True
        ):
            raise RuntimeError(f"invalid stable evidence: {expected_id}")
    if normalized_params(point214["cost_parameters"]) != BASELINE_PARAMS:
        raise RuntimeError("point214 is no longer the exact baseline")

    config = load_json(PREVIOUS / "config.json")
    protocol = config["formal_outer_protocol"]
    old_params = [
        normalized_params(item)
        for item in protocol.get("excluded_prior_parameter_signatures", [])
    ]
    point215_readback = normalized_params(point215["cost_parameters"])
    unique_params = {
        json.dumps(item, sort_keys=True, separators=(",", ":")): item
        for item in old_params + [POINT215_EXACT_PARAMS, point215_readback]
    }
    old_fingerprints = list(
        protocol.get("excluded_prior_three_level_fingerprints", [])
    )
    protocol.update({
        "formal_search_accepted_count_start": 0,
        "formal_search_replay_count_start": 156,
        "initial_real_replay_count": 156,
        "global_replay_ordinal_at_binding": 215,
        "formal_startup_baseline_count_start": 1,
        "formal_startup_baseline_counter_path": str(
            OUTPUT / "state/formal_startup_baseline_counter.txt"
        ),
        "formal_search_attempt_safety_cap": 161,
        "formal_search_maximum_replay_count": 161,
        "maximum_cumulative_real_replay_count": 161,
        "maximum_non_default_real_candidates": 5,
        "maximum_resource_qualified_real_replays_per_batch": 5,
        "global_real_replay_top_k_hard_cap": 5,
        "excluded_prior_parameter_signatures": list(unique_params.values()),
        "excluded_prior_three_level_fingerprints": list(
            dict.fromkeys(old_fingerprints + [POINT215_FINGERPRINT])
        ),
    })

    OUTPUT.mkdir(parents=True)
    atomic_text(OUTPUT / "state/formal_startup_baseline_counter.txt", "1\n")
    calibration = {
        "artifact_type": "pg_real_replay_direction_training_dataset_v1",
        "schema_version": 1,
        "qps_used_for_inner_scoring": False,
        "same_incumbent_scope_decisions": [{
            "scope_id": "job-cont11-reverse-after-p209-p215",
            "target_resource": "memory",
            "evidence_points": [209, 210, 211, 212, 213, 215],
            "decision": {
                "runtime_policy": {
                    "action": "reverse_probe",
                    "probe_only": True,
                    "acceptance_proven": False,
                    "outer_acceptance_unchanged": True,
                    "outer_shrink_forbidden_until_real_acceptance": True,
                    "direction_override_log_vector": {
                        "cpu_tuple_cost": -1.0,
                        "cpu_operator_cost": -1.0,
                        "parallel_setup_cost": -1.0,
                        "random_page_cost": 0.2,
                    },
                    "eta_multiplier": 0.25,
                }
            },
        }],
    }
    calibration_path = OUTPUT / "reverse_direction_calibration.json"
    atomic_json(calibration_path, calibration)
    gradient = config["optimizer"]["gradient"]
    gradient["replay_direction_calibration"] = {
        "enabled": True,
        "artifact_path": str(calibration_path),
        "artifact_sha256": sha256(calibration_path),
        "scope_id": "job-cont11-reverse-after-p209-p215",
    }
    gradient["real_evidence_exploration_anchor"] = {"enabled": False}
    config["metrics"]["plan_estimator"]["ratio_scale"]["boundary_scan"].update({
        "enabled": True,
        "max_distinct_clusters": 5,
        "min_target_improvement_pct": 0.5,
    })
    config["optimizer"]["gradient"]["subset_log_dir"] = str(
        OUTPUT / "subset_logs"
    )
    config["output"]["directory"] = str(OUTPUT)
    config["logging"]["history_path"] = str(OUTPUT / "optimizer_history.jsonl")
    baseline_evidence = {
        "schema_version": 1,
        "event": "job_cont11_r0_zero_call_reclassification",
        "point_id": point214["point_id"],
        "classification": "formal_startup_baseline",
        "point_result_path": str(POINT214),
        "point_result_sha256": EXPECTED[POINT214],
        "physical_call_already_counted": True,
        "formal_search_replay_counter_preserved": 156,
        "global_ordinal_preserved": 215,
        "new_workload_calls": 0,
    }
    baseline_evidence_path = OUTPUT / "startup_baseline_reclassification_evidence.json"
    atomic_json(baseline_evidence_path, baseline_evidence)
    config["startup_baseline_reuse"].update({
        "enabled": True,
        "point_result_path": str(POINT214),
        "point_result_sha256": EXPECTED[POINT214],
        "reclassification_evidence_path": str(baseline_evidence_path),
        "reclassification_evidence_sha256": sha256(baseline_evidence_path),
        "startup_baseline_count_before_restart": 1,
    })
    config["_job_formal_continuation"] = {
        "schema_version": "job_formal10_cont11_reverse_override_v1",
        "previous_formal_dir": str(PREVIOUS),
        "accepted_count_start": 0,
        "formal_replay_count_start": 156,
        "global_replay_ordinal_at_binding": 215,
        "absolute_replay_cap": 161,
        "batch_physical_cap": 5,
        "qps_used_for_inner_scoring": False,
        "boundary_threshold_pct": 0.5,
        "sealed_memory_error_pct_of_incumbent": 0.01938662871364574,
        "reverse_probe_only_until_actual_acceptance": True,
    }
    audit = {
        "schema_version": 1,
        "event": "job_point215_rejected_quarantine",
        "point_result_path": str(POINT215),
        "point_result_sha256": EXPECTED[POINT215],
        "accepted": False,
        "accepted_update_applied": False,
        "incumbent_update_applied": False,
        "range_update_applied": False,
        "gradient_update_applied": False,
        "physical_calls_already_counted": 1,
        "formal_replay_counter_after": 156,
        "global_ordinal_after": 215,
        "new_workload_calls": 0,
        "reason": "actual target memory direction failed",
    }
    atomic_json(OUTPUT / "point215_rejected_quarantine_audit.json", audit)
    config_path = OUTPUT / "config.json"
    atomic_json(config_path, config)
    manifest = {
        "schema_version": "job_formal10_cont11_reverse_override_manifest_v1",
        "output_config": str(config_path),
        "output_config_sha256": sha256(config_path),
        "parameter_optimizer_sha256": EXPECTED[OPTIMIZER],
        "accepted_count_start": 0,
        "formal_replay_count_start": 156,
        "global_replay_ordinal_at_binding": 215,
        "absolute_replay_cap": 161,
        "maximum_physical_calls_this_batch": 5,
        "direction_calibration_sha256": sha256(calibration_path),
        "point215_quarantine_sha256": sha256(
            OUTPUT / "point215_rejected_quarantine_audit.json"
        ),
        "qps_used_for_inner_scoring": False,
        "startup_baseline_reused_point": 214,
    }
    atomic_json(OUTPUT / "continuation_manifest.json", manifest)
    print(json.dumps(manifest, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
