#!/usr/bin/env python3
"""Prepare the XP formal continuation from the canonical accepted=4 state."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any


ATTEMPT = Path(
    "/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/"
    "hybench_xp_compute00_59630/experience_sgd/"
    "attempt39_fresh_from_accepted3_20260726"
)
SOURCE = ATTEMPT / "formal10_after_v3g_entry_gate_reuse_20260729T1425"
CANONICAL = ATTEMPT / "formal10_canonical_after_unreserved_call_20260729T1448"
SUMMARY = ATTEMPT / "candidate_measurements/20260729T143213+0800_2725057/summary.json"
OUTPUT = ATTEMPT / "formal10_cont2_accepted4_cap5_feedback_v1_20260729T1745"

EXPECTED = {
    SOURCE / "config.json":
        "a4489ddf47bf630700e75df8a5adb6de3333c6fc7bdce5118c632998d76ae16c",
    SOURCE / "sealed_startup_baseline_sql.json":
        "ec986fed69e87439b5f84e52ec23960693be04863a76ce1c0be2bb9b50c518a9",
    CANONICAL / "canonical_state.json":
        "3ac05c8febe66ebab6d92abc4fcc7b9ea8a4707a287daa1160c7e034ed1006c3",
    SUMMARY:
        "a7865a46bbc8d2c4ad6d31c9226ad58890a62e0b07289f2adecca1b92307f027",
    SUMMARY.parent / "formal_resource_samples.jsonl":
        "a48755327111ba3c7ced14e1d0b71ec04054dc10820527f9fab608f3cdfd98bf",
}
PRIOR_FINGERPRINT = (
    "three-level-sha256:"
    "fc40eb419f48160230f0932a2d94cf3411a5fdb5c49ef72fd9653139f747dfec"
)
ACCEPTED_DB_QPS = 88.99209427445054


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    atomic_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def shrink_log_range(record: dict[str, Any], anchor: float) -> dict[str, Any]:
    result = dict(record)
    low = float(record["min_value"])
    high = float(record["max_value"])
    if high == low:
        return result
    midpoint = math.sqrt(low * high)
    if anchor <= midpoint:
        result["max_value"] = midpoint
    else:
        result["min_value"] = midpoint
    return result


def main() -> int:
    if OUTPUT.exists():
        raise FileExistsError(f"output already exists: {OUTPUT}")
    for path, expected_sha in EXPECTED.items():
        actual_sha = sha256(path)
        if actual_sha != expected_sha:
            raise RuntimeError(f"SHA mismatch: {path}: {actual_sha} != {expected_sha}")

    canonical = load_json(CANONICAL / "canonical_state.json")
    expected_state = {
        "accepted_total": 4,
        "formal_replay_count": 1,
        "global_replay_ordinal_delta": 1,
        "inherited_accepted": 3,
        "new_accepted": 1,
    }
    for name, expected_value in expected_state.items():
        if canonical.get(name) != expected_value:
            raise RuntimeError(
                f"canonical state mismatch: {name}="
                f"{canonical.get(name)!r} != {expected_value!r}"
            )
    incumbent = {
        name: float(value)
        for name, value in canonical["incumbent_params"].items()
    }
    summary = load_json(SUMMARY)
    effective = {
        name: float(value)
        for name, value in (summary.get("effective_parameters") or {}).items()
    }
    effective_matches = (
        set(effective) == set(incumbent)
        and all(
            math.isclose(effective[name], incumbent[name], rel_tol=0.0, abs_tol=1e-12)
            for name in incumbent
        )
    )
    if (
        summary.get("status") != "ok"
        or summary.get("stable") is not True
        or summary.get("correctness_passed") is not True
        or summary.get("technical_failure") is not False
        or not effective_matches
    ):
        raise RuntimeError("canonical accepted summary is not reusable")

    config = load_json(SOURCE / "config.json")
    if config.get("initial_params") != {
        "cpu_index_tuple_cost": 0.005,
        "cpu_operator_cost": 0.0025,
        "cpu_tuple_cost": 0.01,
        "parallel_setup_cost": 1000,
        "parallel_tuple_cost": 0.1,
        "random_page_cost": 4,
        "seq_page_cost": 1,
    }:
        raise RuntimeError("source initial parameter state changed")
    config["initial_params"] = incumbent
    active = set(config["optimizer"]["gradient"]["active_params"])
    ranges = config["param_ranges"]
    range_updates: dict[str, Any] = {}
    for name, record in list(ranges.items()):
        if name not in active:
            continue
        updated = shrink_log_range(record, incumbent[name])
        range_updates[name] = {
            "old_min": record["min_value"],
            "old_max": record["max_value"],
            "new_min": updated["min_value"],
            "new_max": updated["max_value"],
            "anchor": incumbent[name],
            "transform": "log",
        }
        ranges[name] = updated

    state_dir = OUTPUT / "state"
    protocol = config["formal_outer_protocol"]
    counter_paths = {
        "formal_search_accepted_counter_path":
            state_dir / "formal_search_accepted_counter.txt",
        "formal_search_replay_counter_path":
            state_dir / "formal_search_replay_counter.txt",
        "formal_startup_baseline_counter_path":
            state_dir / "formal_startup_baseline_counter.txt",
        "global_replay_counter_path":
            state_dir / "global_replay_counter.txt",
    }
    for name, path in counter_paths.items():
        protocol[name] = str(path)
    protocol.update({
        "formal_search_accepted_count_start": 4,
        "formal_search_replay_count_start": 1,
        "initial_real_replay_count": 1,
        "global_replay_count_start": 1,
        "global_replay_ordinal_at_binding": 1,
        "formal_startup_baseline_count_start": 0,
        "formal_search_accepted_target": 10,
        "accepted_shrink_target": 10,
        "candidate_replay_top_k": 5,
        "formal_search_initial_replay_top_k": 5,
        "formal_search_max_replay_top_k": 5,
        "formal_search_replay_cumulative_tiers": [4],
        "formal_search_attempt_safety_cap": 4,
        "formal_search_maximum_replay_count": 4,
        "maximum_cumulative_real_replay_count": 4,
        "maximum_non_default_real_candidates": 3,
        "maximum_resource_qualified_real_replays_per_batch": 3,
        "global_real_replay_top_k_hard_cap": 5,
        "second_replay_batch_enabled": False,
        "excluded_prior_three_level_fingerprints": [PRIOR_FINGERPRINT],
    })

    feedback = config["final_evaluation"]["feedback_loop"]
    feedback.update({
        "enabled": True,
        "max_rounds": 3,
        "select_best_actual": True,
        "select_best_primary_by_db_qps": False,
    })
    goal = feedback["resource_iteration_goal"]
    goal.update({
        "enabled": True,
        "stop_after_primary_acceptance": True,
        "continue_after_primary_acceptance_rounds": 0,
        "select_best_primary_by_db_qps": False,
    })
    goal["strong_constraints"]["enabled"] = False
    config["final_evaluation"]["run_validation"] = True
    config["_xp_next_validation_round"].update({
        "accepted_start": 4,
        "accepted_shrink_target": 10,
        "maximum_valid_real_replays": 3,
        "qps_used_for_inner_scoring": False,
        "rejected_candidates_update_incumbent_range_or_accepted": False,
        "technical_failures_count_as_replay": True,
        "excluded_prior_three_level_fingerprints": [PRIOR_FINGERPRINT],
    })
    config["optimizer"]["gradient"]["subset_log_dir"] = str(OUTPUT / "subset_logs")
    config["logging"]["history_path"] = str(OUTPUT / "optimizer_history.jsonl")
    config["output"]["directory"] = str(OUTPUT)

    samples_path = SUMMARY.parent / "formal_resource_samples.jsonl"
    aggregate = summary["formal_pg_resource"]["aggregate"]
    caps = summary["formal_pg_resource"]["caps"]
    point_result = {
        "status": "complete",
        "point_id": SUMMARY.parent.name,
        "cost_parameters": incumbent,
        "oom_killed": False,
        "database_restart_count": 0,
        "measurement": {
            "correctness_passed": True,
            "resource_stability": {"passed": True},
            "derived_resource_samples_path": str(samples_path),
            "derived_resource_samples_sha256": EXPECTED[samples_path],
            "resource_denominators": {
                "cpu_quota_cores": float(caps["cpu_quota_cores"]),
                "memory_bytes": float(caps["memory_bytes"]),
                "io_read_bps": float(caps["read_cap_bps"]),
                "io_write_bps": float(caps["write_cap_bps"]),
            },
            "resource_utilization": {
                "cpu": float(aggregate["cpu_utilization"]),
                "memory": float(aggregate["memory_utilization"]),
                "io": float(aggregate["io_utilization"]),
            },
            "db_qps": ACCEPTED_DB_QPS,
            "qps": float(summary["benchmark"]["xp_qps"]),
            "query_count": 0,
            "latency_ms": {"p90": None, "p95": None, "p99": None},
            "effective_measurement_seconds":
                float(summary["effective_measurement_seconds"]),
            "error_rate": 0.0,
        },
    }

    OUTPUT.mkdir(parents=True)
    state_dir.mkdir()
    atomic_text(counter_paths["formal_search_accepted_counter_path"], "4\n")
    atomic_text(counter_paths["formal_search_replay_counter_path"], "1\n")
    atomic_text(counter_paths["formal_startup_baseline_counter_path"], "0\n")
    atomic_text(counter_paths["global_replay_counter_path"], "1\n")
    point_path = OUTPUT / "startup_baseline_point_result.json"
    atomic_json(point_path, point_result)
    snapshot_path = SOURCE / "sealed_startup_baseline_sql.json"
    evidence_path = OUTPUT / "startup_baseline_reclassification.json"
    evidence = {
        "contract": "xp_accepted_incumbent_startup_baseline_reuse_v1",
        "classification": "formal_startup_baseline",
        "point_id": SUMMARY.parent.name,
        "point_result_sha256": sha256(point_path),
        "source_summary_path": str(SUMMARY),
        "source_summary_sha256": EXPECTED[SUMMARY],
        "candidate_replay_budget_after": 0,
        "accepted_budget_after": 0,
        "startup_baseline_count_after": 1,
        "new_workload_calls": 0,
        "qps_role": "outer_audit_only",
    }
    atomic_json(evidence_path, evidence)
    config["startup_baseline_reuse"] = {
        "enabled": True,
        "point_result_path": str(point_path),
        "point_result_sha256": sha256(point_path),
        "reclassification_evidence_path": str(evidence_path),
        "reclassification_evidence_sha256": sha256(evidence_path),
        "baseline_sql_snapshot_path": str(snapshot_path),
        "baseline_sql_snapshot_sha256": EXPECTED[snapshot_path],
        "baseline_sql_expected_query_count": len(
            load_json(snapshot_path)["queries"]
        ),
        "startup_baseline_count_before_restart": 0,
        "db_qps_source": "formal_benchmark_db_qps",
    }
    config["_xp_formal_continuation"] = {
        "contract": "xp_formal10_accepted4_continuation_v1",
        "canonical_state": str(CANONICAL / "canonical_state.json"),
        "canonical_state_sha256": EXPECTED[CANONICAL / "canonical_state.json"],
        "accepted_start": 4,
        "formal_replay_start": 1,
        "global_replay_start": 1,
        "batch_physical_cap": 5,
        "prior_technical_warmup_calls": 1,
        "planned_startup_warmup_calls": 1,
        "maximum_candidate_calls_remaining": 3,
        "range_updates": range_updates,
        "prior_fingerprint_blacklist": [PRIOR_FINGERPRINT],
        "qps_used_for_inner_scoring": False,
    }
    config_path = OUTPUT / "config.json"
    atomic_json(config_path, config)
    manifest = {
        "contract": "xp_formal10_accepted4_continuation_v1",
        "output_config": str(config_path),
        "output_config_sha256": sha256(config_path),
        "canonical_state_sha256": EXPECTED[CANONICAL / "canonical_state.json"],
        "accepted_count_start": 4,
        "inherited_accepted": 3,
        "new_accepted_before_batch": 1,
        "formal_replay_count_start": 1,
        "global_replay_ordinal_at_binding": 1,
        "per_batch_physical_replay_cap": 5,
        "prior_technical_warmup_calls": 1,
        "planned_startup_warmup_calls": 1,
        "maximum_candidate_calls_remaining": 3,
        "accepted_target": 10,
        "qps_role": "outer_audit_only",
        "cpu_affinity": "32-51,288-307",
        "mysql_exclusion": "do_not_touch_cpu_184-199,440-455_or_mysql_state",
        "range_updates": range_updates,
    }
    atomic_json(OUTPUT / "formal_manifest.json", manifest)
    print(json.dumps(manifest, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
