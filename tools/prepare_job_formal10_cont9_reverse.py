#!/usr/bin/env python3
"""Prepare JOB continuation 10 with a reused baseline and reverse probes."""

from __future__ import annotations

import hashlib
import json
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
PREVIOUS = BASE / "formal10_after_round10_gate_cont8_cap5_novel_v1_20260729T1738"
OUTPUT = BASE / "formal10_after_round10_gate_cont10_reuse_r0_v1_20260729T1904"
HISTORY = PREVIOUS / "optimizer_history_2026-07-29_174025.jsonl"
WORKSPACE_OPTIMIZER = LANE / "workspace_attempt7/parameter_optimizer.py"
ACCEPTED = BASE / "formal10_accepted_counter.txt"
REPLAY = BASE / "formal_search_replay_counter.txt"
GLOBAL = BASE / "replay_counter.txt"

EXPECTED = {
    PREVIOUS / "config.json":
        "3d6693b9fd3acc305953d7095da814f27e0776746cb5df20362cc4dad0dbdd0e",
    HISTORY:
        "b27cd75e3f0e2f729e62db58d9dae27a6793c9d993f40c4696642a0ddb4aee79",
    PREVIOUS / "rejected_baseline_restore_readback.txt":
        "a62d0e5450185391d221362a79eee35223e8700f9cb7438b13ba2b51c738e532",
    BASE / "formal_points/point_0209/point_result.json":
        "067c061717d6e348972da3928f12304e27aebae643b4f297bf26698517aa9cbc",
    BASE / "formal_points/point_0210/point_result.json":
        "ca1e0e3290fa6fd8c08549469bdc37807cce62e4c2f0ab0db3e09c5d376ce402",
    BASE / "formal_points/point_0211/point_result.json":
        "880e476dd5a8f0a9e698aa75a489d5e7c8ea35b0295d0a42c2299d52194942be",
    BASE / "formal_points/point_0212/point_result.json":
        "704f0536fbe478c3df01e47e745f08d750a6d9a96419074ef82004af35bdcfb4",
    BASE / "formal_points/point_0213/point_result.json":
        "f331a21e29a727c462d833f284a257e8574963136129af48ac698d26d557a4bd",
    BASE / "formal_points/point_0214/point_result.json":
        "8e98ee2e7340511a113ff0f72b31fcafa9c957667ad1879b0066bdd55e330d57",
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
NEW_FINGERPRINTS = [
    "three-level-sha256:23fe49fe7fb97e3ca4f37982bba3c3ff97665d461a87737dd62f528b7f79dca4",
    "three-level-sha256:ecc216603e6a8c4d96e79c804bae8c05b50dd9ebf4dd98e0c7af0eff41d6da6c",
    "three-level-sha256:ff5c160ff7c2727c753ca79873a6d6089e4d0ccd54dfcca424e3a5dd34806c1b",
    "three-level-sha256:6154005af94f92f3947cf869a8337b34f99abf190e99488f1a9382c63e53471b",
]
REVERSE_ANCHORS = [
    {
        "name": "reverse_tuple_operator_setup_5pct",
        "params": {
            **BASELINE_PARAMS,
            "cpu_operator_cost": 0.002375,
            "cpu_tuple_cost": 0.0095,
            "parallel_setup_cost": 6650.0,
        },
    },
    {
        "name": "reverse_tuple_5pct_random_up",
        "params": {
            **BASELINE_PARAMS,
            "cpu_tuple_cost": 0.0095,
            "random_page_cost": 4.02,
        },
    },
    {
        "name": "reverse_tuple_setup_10pct",
        "params": {
            **BASELINE_PARAMS,
            "cpu_tuple_cost": 0.009,
            "parallel_setup_cost": 6300.0,
        },
    },
    {
        "name": "reverse_setup_10pct",
        "params": {**BASELINE_PARAMS, "parallel_setup_cost": 6300.0},
    },
    {
        "name": "reverse_operator_5pct",
        "params": {**BASELINE_PARAMS, "cpu_operator_cost": 0.002375},
    },
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


def normalize_params(value: Mapping[str, Any]) -> dict[str, float]:
    return {str(name): float(item) for name, item in value.items()}


def main() -> int:
    if OUTPUT.exists():
        raise FileExistsError(f"output already exists: {OUTPUT}")
    for path, expected_sha in EXPECTED.items():
        actual_sha = sha256(path)
        if actual_sha != expected_sha:
            raise RuntimeError(f"SHA mismatch: {path}: {actual_sha} != {expected_sha}")
    if (PREVIOUS / "driver_exit_code.txt").read_text(
        encoding="utf-8"
    ).strip() != "0":
        raise RuntimeError("cont8 did not exit cleanly")
    counters = {
        "accepted": read_int(ACCEPTED),
        "formal_replay": read_int(REPLAY),
        "global_ordinal": read_int(GLOBAL),
    }
    expected_counters = {
        "accepted": 0,
        "formal_replay": 155,
        "global_ordinal": 214,
    }
    if counters != expected_counters:
        raise RuntimeError(f"counter mismatch: {counters} != {expected_counters}")

    points = []
    replayed_params = []
    for ordinal in range(209, 214):
        path = BASE / f"formal_points/point_{ordinal:04d}/point_result.json"
        point = load_json(path)
        measurement = point.get("measurement") or {}
        params = normalize_params(point.get("cost_parameters") or {})
        if (
            point.get("status") != "complete"
            or measurement.get("correctness_passed") is not True
            or (measurement.get("resource_stability") or {}).get("passed") is not True
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
            "correctness_passed": True,
            "stable": True,
        })
    baseline_qps = points[0]["db_qps"]
    if any(point["db_qps"] >= baseline_qps for point in points[1:]):
        raise RuntimeError("cont8 rejection evidence changed")

    config = load_json(PREVIOUS / "config.json")
    if normalize_params(config["initial_params"]) != BASELINE_PARAMS:
        raise RuntimeError("cont8 baseline parameters changed")
    protocol = config["formal_outer_protocol"]
    old_params = [
        normalize_params(item)
        for item in protocol.get("excluded_prior_parameter_signatures", [])
    ]
    unique_params = {
        json.dumps(item, sort_keys=True, separators=(",", ":")): item
        for item in old_params + replayed_params
    }
    old_fingerprints = list(
        protocol.get("excluded_prior_three_level_fingerprints", [])
    )
    protocol.update({
        "formal_search_accepted_count_start": 0,
        "formal_search_accepted_counter_path": str(ACCEPTED),
        "formal_search_replay_count_start": 155,
        "initial_real_replay_count": 155,
        "formal_search_replay_counter_path": str(REPLAY),
        "global_replay_ordinal_at_binding": 214,
        "global_replay_counter_path": str(GLOBAL),
        "formal_startup_baseline_count_start": 1,
        "formal_startup_baseline_counter_path": str(
            OUTPUT / "state/formal_startup_baseline_counter.txt"
        ),
        "formal_search_attempt_safety_cap": 160,
        "formal_search_maximum_replay_count": 160,
        "maximum_cumulative_real_replay_count": 160,
        "maximum_non_default_real_candidates": 5,
        "maximum_resource_qualified_real_replays_per_batch": 5,
        "global_real_replay_top_k_hard_cap": 5,
        "excluded_prior_parameter_signatures": list(unique_params.values()),
        "excluded_prior_three_level_fingerprints": list(
            dict.fromkeys(old_fingerprints + NEW_FINGERPRINTS)
        ),
    })

    # The frozen experience table is SHA-bound. Keep it byte-semantically
    # identical and express the corrected direction only through explicit
    # exploration anchors.
    gradient = config["optimizer"]["gradient"]
    gradient["real_evidence_exploration_anchor"] = {
        "enabled": True,
        "optimized_params": REVERSE_ANCHORS[0]["params"],
        "provenance": {
            "source": "cont8_rejected_direction_outer_audit",
            "reverse_only": True,
            "qps_used_for_inner_scoring": False,
            "points": [point["ordinal"] for point in points],
        },
    }
    pair_probe = config["optimizer"]["bruteforce"]["candidate_pair_probe"]
    anchor_probe = pair_probe["anchor_probe"]
    anchor_probe.update({
        "enabled": True,
        "anchors": REVERSE_ANCHORS,
        "interpolation_steps": [1.0],
        "max_candidates": 5,
        "min_topk": 3,
    })
    pair_probe["source_quota"]["candidate_anchor_probe"] = 5
    plateau_quota = config["optimizer"]["bruteforce"]["candidate_gate"][
        "plateau_control"
    ]["source_quota"]
    plateau_quota["candidate_anchor_probe"] = 5
    config["optimizer"]["gradient"]["subset_log_dir"] = str(
        OUTPUT / "subset_logs"
    )
    config["output"]["directory"] = str(OUTPUT)
    config["logging"]["history_path"] = str(OUTPUT / "optimizer_history.jsonl")
    config["_job_formal_continuation"] = {
        "schema_version": "job_formal10_cont10_reuse_r0_v1",
        "previous_formal_dir": str(PREVIOUS),
        "accepted_count_start": 0,
        "formal_replay_count_start": 155,
        "global_replay_ordinal_at_binding": 214,
        "absolute_replay_cap": 160,
        "batch_physical_cap": 5,
        "reverse_anchor_count": len(REVERSE_ANCHORS),
        "qps_used_for_inner_scoring": False,
        "rejected_requires_baseline_restore": True,
    }

    OUTPUT.mkdir(parents=True)
    atomic_text(OUTPUT / "state/formal_startup_baseline_counter.txt", "1\n")
    baseline_point_path = BASE / "formal_points/point_0214/point_result.json"
    baseline_point = load_json(baseline_point_path)
    baseline_measurement = baseline_point.get("measurement") or {}
    if (
        baseline_point.get("status") != "complete"
        or baseline_point.get("point_id")
        != "job_experience_table_outer_attempt1_0214"
        or normalize_params(baseline_point.get("cost_parameters") or {})
        != BASELINE_PARAMS
        or baseline_measurement.get("correctness_passed") is not True
        or (baseline_measurement.get("resource_stability") or {}).get("passed")
        is not True
    ):
        raise RuntimeError("point_0214 is not reusable exact-baseline evidence")
    baseline_evidence_path = OUTPUT / "startup_baseline_reclassification_evidence.json"
    atomic_json(
        baseline_evidence_path,
        {
            "schema_version": 1,
            "event": "job_cont9_r0_zero_call_reclassification",
            "point_id": baseline_point["point_id"],
            "classification": "formal_startup_baseline",
            "point_result_path": str(baseline_point_path),
            "point_result_sha256": EXPECTED[baseline_point_path],
            "candidate_replay_budget_after": 0,
            "accepted_budget_after": 0,
            "startup_baseline_count_after": 1,
            "physical_call_already_counted": True,
            "formal_search_replay_counter_preserved": 155,
            "global_ordinal_preserved": 214,
            "new_workload_calls": 0,
        },
    )
    config["startup_baseline_reuse"] = {
        "enabled": True,
        "point_result_path": str(baseline_point_path),
        "point_result_sha256": EXPECTED[baseline_point_path],
        "reclassification_evidence_path": str(baseline_evidence_path),
        "reclassification_evidence_sha256": sha256(baseline_evidence_path),
        "startup_baseline_count_before_restart": 1,
        "db_qps_source": "formal_benchmark_db_qps",
        "baseline_sql_snapshot_path": (
            BASE / "sealed_startup_baseline_sql_job8_literal_v2.json"
        ).as_posix(),
        "baseline_sql_snapshot_sha256": (
            "842413fda8fa783bc8750f6b438a6b6d2aa41d7adef629cf7becbba9f38ab7fb"
        ),
        "baseline_sql_expected_query_count": 8,
    }
    audit = {
        "schema_version": 1,
        "event": "job_cont8_rejected_batch_quarantine",
        "source_points": points,
        "classification": "formal_rejected_valid_stable_batch",
        "accepted": False,
        "accepted_update_applied": False,
        "incumbent_update_applied": False,
        "range_update_applied": False,
        "gradient_update_applied": False,
        "physical_calls_already_counted": 5,
        "formal_replay_counter_after": 154,
        "global_ordinal_after": 213,
        "new_workload_calls": 0,
        "direction_change": (
            "reverse tuple/operator/setup resource sensitivity; "
            "QPS remains outer audit only"
        ),
    }
    atomic_json(OUTPUT / "cont8_rejected_batch_quarantine_audit.json", audit)
    config_path = OUTPUT / "config.json"
    atomic_json(config_path, config)
    manifest = {
        "schema_version": "job_formal10_cont10_reuse_r0_manifest_v1",
        "output_config": str(config_path),
        "output_config_sha256": sha256(config_path),
        "parameter_optimizer_sha256": EXPECTED[WORKSPACE_OPTIMIZER],
        "accepted_count_start": 0,
        "formal_replay_count_start": 155,
        "global_replay_ordinal_at_binding": 214,
        "absolute_replay_cap": 160,
        "maximum_physical_calls_this_batch": 5,
        "excluded_fingerprint_count": len(
            protocol["excluded_prior_three_level_fingerprints"]
        ),
        "excluded_parameter_signature_count": len(
            protocol["excluded_prior_parameter_signatures"]
        ),
        "reverse_anchors": REVERSE_ANCHORS,
        "qps_used_for_inner_scoring": False,
        "startup_baseline_reused_point": 214,
        "startup_baseline_reclassification_sha256": sha256(
            baseline_evidence_path
        ),
        "cont8_audit_sha256": sha256(
            OUTPUT / "cont8_rejected_batch_quarantine_audit.json"
        ),
    }
    atomic_json(OUTPUT / "continuation_manifest.json", manifest)
    print(json.dumps(manifest, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
