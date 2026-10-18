#!/usr/bin/env python3
"""Seal TPC-C attempt8 rank-1 rejection and prepare target-5 attempt9."""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt8_tsgd_v1"
)
ATTEMPT_ROOT = Path(
    "/disk2/resource_adaptive/pg5_common_outer_bgallowed_20260722_v1/"
    "tpcc_experience_sgd/attempt9_partial_initial_target5_v1"
)
SOURCE_CONFIG = SOURCE_ROOT / "config/tpcc_experience_table_all_subset_sgd.json"
SOURCE_HISTORY = (
    SOURCE_ROOT / "history/tpcc_optimizer_history_2026-07-23_034034.jsonl"
)
BASELINE_POINT = (
    SOURCE_ROOT / "candidate_measurements/point_20260723_034037_3646917/summary.json"
)
RANK1_POINT = (
    SOURCE_ROOT / "candidate_measurements/point_20260723_042203_516439/summary.json"
)
CHECKPOINT = ATTEMPT_ROOT / "checkpoint/partial_initial_round0_rank1.json"
OUTPUT_CONFIG = ATTEMPT_ROOT / "config/tpcc_experience_table_all_subset_sgd.json"
AUDIT = ATTEMPT_ROOT / "checkpoint/partial_initial_round0_rank1_audit.json"
EXPECTED_PARAMETER_OPTIMIZER_SHA = (
    "7ecdcd7c43af82beed9781e315ae6385385fd6be8479abbeb0a46f13fcf3c281"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    if path.exists() or path.is_symlink():
        raise RuntimeError(f"refusing to overwrite sealed output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def replace_root(value: Any, old: str, new: str) -> Any:
    if isinstance(value, dict):
        return {key: replace_root(item, old, new) for key, item in value.items()}
    if isinstance(value, list):
        return [replace_root(item, old, new) for item in value]
    if isinstance(value, str):
        return value.replace(old, new)
    return value


def events(path: Path) -> list[Dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def exact_plan_samples(event: Dict[str, Any]) -> list[Dict[str, Any]]:
    capture = Path(str(event.get("plan_capture_file") or ""))
    if capture.is_file():
        payload = json.loads(capture.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            return list(
                payload.get("items")
                or payload.get("plans")
                or payload.get("samples")
                or []
            )
    return list(event.get("plan_hashes") or [])


def main() -> int:
    required = [
        SOURCE_CONFIG,
        SOURCE_HISTORY,
        BASELINE_POINT,
        RANK1_POINT,
        ROOT / "parameter_optimizer.py",
    ]
    for path in required:
        if not path.is_file():
            raise RuntimeError(f"required source is missing: {path}")
    if sha256(ROOT / "parameter_optimizer.py") != EXPECTED_PARAMETER_OPTIMIZER_SHA:
        raise RuntimeError("parameter_optimizer.py changed after the resume design was sealed")

    config = json.loads(SOURCE_CONFIG.read_text(encoding="utf-8"))
    history = events(SOURCE_HISTORY)
    baseline_top = next(
        item for item in history if item.get("event") == "baseline_top_sql"
    )
    baseline_resource = next(
        item
        for item in history
        if item.get("event") == "baseline_runtime_resource_summary"
    )
    ranking = copy.deepcopy(
        next(
            item
            for item in history
            if item.get("event") == "experience_table_all_subset_ranking"
            and int(item.get("round_index", -1)) == 0
        )
    )
    final_top = next(item for item in history if item.get("event") == "final_top_sql")
    if (
        int(ranking.get("subset_count", -1)) != 127
        or int(ranking.get("successful_subset_count", -1)) != 127
        or int(ranking.get("failed_subset_count", -1)) != 0
        or int(ranking.get("unique_candidate_count", -1)) != 49
    ):
        raise RuntimeError("attempt8 round0 ranking is not the complete 127/127 result")
    all_rankings = list(ranking.get("candidate_rankings") or [])
    ranking_by_rank = {int(item.get("rank") or 0): item for item in all_rankings}
    selected_ranks = list(range(1, 11))
    if any(rank not in ranking_by_rank for rank in selected_ranks):
        raise RuntimeError("attempt8 ranking does not contain the sealed top-10 prefix")
    ranking["candidate_rankings"] = [
        copy.deepcopy(ranking_by_rank[rank]) for rank in selected_ranks
    ]
    ranking["all_candidate_rankings"] = copy.deepcopy(ranking["candidate_rankings"])
    ranking["sealed_original_unique_candidate_count"] = 49
    ranking["sealed_scenario_max_replay_top_k"] = 10

    baseline_summary = json.loads(BASELINE_POINT.read_text(encoding="utf-8"))
    rank1_summary = json.loads(RANK1_POINT.read_text(encoding="utf-8"))
    if (
        baseline_summary.get("status") != "complete"
        or (baseline_summary.get("measurement_assessment") or {}).get("stable")
        is not True
        or rank1_summary.get("status") != "complete"
        or (rank1_summary.get("measurement_assessment") or {}).get("stable")
        is not True
        or int(rank1_summary.get("native_sql_exception_occurrences", 0) or 0) != 0
    ):
        raise RuntimeError("baseline or rank1 runtime evidence is invalid")

    baseline_protocol = copy.deepcopy(
        (baseline_resource.get("resource_stats") or {}).get(
            "candidate_measurement_protocol"
        )
        or {}
    )
    baseline_protocol.update({"stable": True, "correctness_passed": True})
    baseline_benchmark = copy.deepcopy(baseline_top.get("baseline_benchmark") or {})
    baseline_qps = float(baseline_summary["db_qps"])
    if abs(float(baseline_benchmark.get("db_qps")) - baseline_qps) > 1e-9:
        raise RuntimeError("baseline DB-QPS differs between history and point evidence")
    baseline_validation = {
        "resource_raw": copy.deepcopy(baseline_resource.get("resource_raw") or {}),
        "resource_stats": copy.deepcopy(baseline_resource.get("resource_stats") or {}),
        "resource_normalized": copy.deepcopy(
            baseline_resource.get("resource_normalized") or {}
        ),
        "utilization_pct": copy.deepcopy(
            baseline_resource.get("utilization_pct") or {}
        ),
        "benchmark": baseline_benchmark,
        "benchmark_latency": copy.deepcopy(
            baseline_top.get("baseline_benchmark_latency") or {}
        ),
        "db_qps": baseline_qps,
        "formal_qps": baseline_qps,
        "correctness_passed": True,
        "candidate_measurement_protocol": baseline_protocol,
    }
    baseline_estimator_sql = exact_plan_samples(baseline_top)
    if len(baseline_estimator_sql) != 37:
        raise RuntimeError(
            "sealed baseline plan capture must contain exactly 37 plannable SQLs"
        )
    rank1_validation = copy.deepcopy(final_top.get("final_validation") or {})
    rank1_protocol = copy.deepcopy(
        rank1_validation.get("candidate_measurement_protocol") or {}
    )
    rank1_protocol.update({"stable": True, "correctness_passed": True})
    rank1_validation["candidate_measurement_protocol"] = rank1_protocol
    rank1_qps = float(rank1_validation.get("db_qps"))
    rank1_cpu = float((rank1_validation.get("utilization_pct") or {}).get("cpu"))
    baseline_cpu = float((baseline_validation.get("utilization_pct") or {}).get("cpu"))
    if (
        abs(rank1_qps - float(rank1_summary["db_qps"])) > 1e-9
        or abs(
            rank1_cpu
            - float((rank1_summary.get("resource_utilization") or {})["cpu"])
            * 100.0
        )
        > 1e-6
        or not rank1_cpu < baseline_cpu
        or not rank1_qps < baseline_qps
    ):
        raise RuntimeError("rank1 is not the sealed QPS-down/CPU-down rejection")

    rank1_candidate = ranking_by_rank[1]
    rank1_evaluation = {
        "candidate_index": 1,
        "estimated_rank": 1,
        "estimated_score": rank1_candidate.get("final_score"),
        "candidate_actual_score": None,
        "candidate_actual_score_detail": {},
        "accepted_by_actual_score": False,
        "actual_accept_reason": "db_qps_not_strictly_higher",
        "common_outer_acceptance": {
            "accepted": False,
            "db_qps_before": baseline_qps,
            "db_qps_after": rank1_qps,
            "target_resource": "cpu",
            "target_utilization_before": baseline_cpu,
            "target_utilization_after": rank1_cpu,
            "required": "db_qps_strictly_up_and_target_utilization_strictly_down",
        },
        "performance_guard_ok": True,
        "performance_guard_reason": "performance_guard_disabled",
        "performance_guard_details": {},
        "candidate_optimized_params": copy.deepcopy(
            rank1_candidate["optimized_params"]
        ),
        "candidate_estimated_resource": copy.deepcopy(
            rank1_candidate.get("final_resource") or {}
        ),
        "candidate_final_validation": rank1_validation,
        "candidate_top_sql": copy.deepcopy(final_top.get("top") or []),
        "candidate_plan_samples": exact_plan_samples(final_top),
        "candidate_plan_changes": copy.deepcopy(
            (rank1_validation.get("plan_change_summary") or {}).get(
                "changed_plan_keys"
            )
            or []
        ),
        "candidate_degradation_summary": {},
        "replay_evidence": {
            "new_evidence": True,
            "scope_replayed_evidence_count": 1,
        },
    }
    replay_plan = {
        "event": "experience_table_gradient_real_replay_plan",
        "round_index": 0,
        "ranking_count": 49,
        "selected_count": 10,
        "selected_ranks": selected_ranks,
        "initial_batch_size": 5,
        "second_cumulative_batch_size": 10,
        "max_cumulative_batch_size": 10,
        "optimistic_pruning_framework": (
            "multidimensional_resource_error_box_utility_upper_before_plan_cluster_recall"
        ),
        "optimistically_pruned_count": 0,
        "optimistically_pruned_ranks": [],
        "plan_cluster_basis": "exact_physical_bundle_only",
        "plan_fingerprint_missing_policy": "fail_open_unique_cluster",
        "plan_cluster_count": 10,
        "similar_plan_suppressed_count": 0,
        "similar_plan_suppressed_ranks": [],
        "candidate_space_exhausted": False,
        "transition_completed_replay_ranks": [1],
    }
    experience_table_path = Path(
        str((config.get("experience_table_contract") or {}).get("artifact_path"))
    )
    experience_table = json.loads(experience_table_path.read_text(encoding="utf-8"))[
        "table"
    ]
    expected_range_state = {
        name: {
            "min_value": float(record["min_value"]),
            "max_value": float(record["max_value"]),
            "transform": "linear" if float(record["min_value"]) <= 0.0 else "log",
        }
        for name, record in (config.get("param_ranges") or {}).items()
    }
    counters = {
        "global_replay_ordinal": 1,
        "formal_search_replay_count": 1,
        "formal_startup_baseline_count": 1,
        "accepted_shrink_count": 0,
        "completed_outer_explorations": 0,
        "round0_replayed_candidate_count": 1,
    }
    checkpoint = {
        "schema_version": 1,
        "kind": "gradient_partial_initial_round_resume_checkpoint_v1",
        "source_history_path": str(SOURCE_HISTORY),
        "source_history_sha256": sha256(SOURCE_HISTORY),
        "source_config_path": str(SOURCE_CONFIG),
        "source_config_sha256": sha256(SOURCE_CONFIG),
        "incumbent_evidence_path": str(BASELINE_POINT),
        "incumbent_evidence_sha256": sha256(BASELINE_POINT),
        "baseline_evidence_path": str(BASELINE_POINT),
        "baseline_evidence_sha256": sha256(BASELINE_POINT),
        "accepted_params": copy.deepcopy(config["initial_params"]),
        "accepted_validation": baseline_validation,
        "accepted_estimated_resource": copy.deepcopy(
            baseline_validation["resource_normalized"]
        ),
        "accepted_final_top_sql": copy.deepcopy(baseline_top.get("top") or []),
        "accepted_final_plan_samples": exact_plan_samples(baseline_top),
        "accepted_final_plan_changes": [],
        "accepted_degradation_summary": {},
        "baseline_validation": baseline_validation,
        "baseline_top_sql": baseline_estimator_sql,
        "baseline_plan_samples": exact_plan_samples(baseline_top),
        "baseline_workload_profile": copy.deepcopy(
            baseline_top.get("plan_estimator_workload_profile") or {}
        ),
        "online_sensitivity_table": experience_table,
        "expected_range_state_after_accept": expected_range_state,
        "replayed_scope_incumbent_params": copy.deepcopy(config["initial_params"]),
        "replayed_candidate_params": [
            copy.deepcopy(rank1_candidate["optimized_params"])
        ],
        "replayed_candidate_ranks": [1],
        "replayed_candidate_evaluations": [rank1_evaluation],
        "replayed_candidate_evidence": [
            {
                "rank": 1,
                "point_result_path": str(RANK1_POINT),
                "point_result_sha256": sha256(RANK1_POINT),
                "db_qps": rank1_qps,
                "cpu_utilization_pct": rank1_cpu,
                "correctness_passed": True,
                "stable": True,
            }
        ],
        "round0_ranking_event": ranking,
        "round0_replay_plan_event": replay_plan,
        "resume_next_estimated_rank": 2,
        "resume_same_ranked_scope": True,
        "counters": counters,
        "resume_contract": {
            "do_not_rerun_r0": True,
            "do_not_rerun_round0": True,
            "do_not_rerun_round0_subsets": True,
            "do_not_rerun_replayed_ranks": True,
            "next_real_replay_rank": 2,
            "target_resource": "cpu",
            "technical_failures_excluded_from_composite": True,
            "first_unstable_then_total_valid_replicates": 3,
            "first_stable_replicate_used_directly": True,
            "later_stable_replicate_short_circuit": False,
        },
        "parameter_optimizer_sha256": EXPECTED_PARAMETER_OPTIMIZER_SHA,
        "common_outer_loop_sha256": sha256(ROOT / "common_outer_loop.py"),
        "resource_utility_sha256": sha256(ROOT / "resource_utility.py"),
    }
    write_json(CHECKPOINT, checkpoint)

    resumed_config = replace_root(
        copy.deepcopy(config), str(SOURCE_ROOT), str(ATTEMPT_ROOT)
    )
    resumed_config["_campaign"]["attempt_tag"] = (
        "attempt9_partial_initial_target5_v1"
    )
    resumed_config["_campaign"]["built_at"] = "2026-07-23T04:45:00+08:00"
    protocol = resumed_config.setdefault("formal_outer_protocol", {})
    protocol.update(
        {
            "formal_search_initial_replay_top_k": 5,
            "formal_search_second_replay_top_k": 10,
            "formal_search_max_replay_top_k": 10,
            "formal_search_accepted_target": 5,
            "formal_search_accepted_count_start": 0,
            "formal_search_replay_count_start": 1,
            "formal_search_resume_checkpoint_path": str(CHECKPOINT),
            "formal_search_resume_checkpoint_sha256": sha256(CHECKPOINT),
            "formal_search_accepted_counter_path": str(
                ATTEMPT_ROOT / "formal_search_accepted_counter.txt"
            ),
            "resume_next_initial_rank": 2,
            "resume_replays_are_evidence_only_no_rerun": True,
            "normalized_width_stop": 1.0 / 32.0,
            "plan_cluster_basis": "exact_physical_bundle_only",
            "plan_fingerprint_missing_policy": "fail_open_unique_cluster",
        }
    )
    resumed_config.setdefault("_tpcc_attempt9_partial_initial_contract", {}).update(
        {
            "parameter_optimizer_sha256": EXPECTED_PARAMETER_OPTIMIZER_SHA,
            "source_attempt": str(SOURCE_ROOT),
            "source_rank1_db_qps": rank1_qps,
            "source_rank1_cpu_utilization_pct": rank1_cpu,
            "source_rank1_accepted": False,
            "resume_next_rank": 2,
            "accepted_target": 5,
            "normalized_width_stop": 1.0 / 32.0,
        }
    )
    write_json(OUTPUT_CONFIG, resumed_config)
    accepted_counter = Path(protocol["formal_search_accepted_counter_path"])
    accepted_counter.parent.mkdir(parents=True, exist_ok=True)
    with accepted_counter.open("x", encoding="utf-8") as handle:
        handle.write("0\n")
        handle.flush()
        os.fsync(handle.fileno())

    audit = {
        "schema_version": 1,
        "status": "ready",
        "parameter_optimizer_sha256": EXPECTED_PARAMETER_OPTIMIZER_SHA,
        "source_history_sha256": sha256(SOURCE_HISTORY),
        "source_config_sha256": sha256(SOURCE_CONFIG),
        "baseline_point_sha256": sha256(BASELINE_POINT),
        "rank1_point_sha256": sha256(RANK1_POINT),
        "checkpoint_path": str(CHECKPOINT),
        "checkpoint_sha256": sha256(CHECKPOINT),
        "config_path": str(OUTPUT_CONFIG),
        "config_sha256": sha256(OUTPUT_CONFIG),
        "baseline": {"db_qps": baseline_qps, "cpu_utilization_pct": baseline_cpu},
        "rank1": {
            "db_qps": rank1_qps,
            "cpu_utilization_pct": rank1_cpu,
            "db_qps_delta_pct": (rank1_qps / baseline_qps - 1.0) * 100.0,
            "cpu_delta_pp": rank1_cpu - baseline_cpu,
            "accepted": False,
        },
        "resume": {
            "skip_r0": True,
            "skip_round0_subsets": True,
            "replayed_ranks": [1],
            "next_rank": 2,
            "initial_top_k": 5,
            "max_top_k": 10,
            "accepted_target": 5,
            "normalized_width_stop": 1.0 / 32.0,
        },
    }
    write_json(AUDIT, audit)
    print(json.dumps(audit, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
