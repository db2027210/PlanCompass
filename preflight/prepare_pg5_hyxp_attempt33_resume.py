#!/usr/bin/env python3
"""Build the corrected HyBench XP rank-2 resume with all 31 baseline SQL."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile


PROJECT = Path("/home/anonymous/program/resource_adaptive")
LANE = Path(
    "/disk1/pg5_common_outer_bgallowed_20260722_v1/lanes/"
    "hybench_xp_compute00_59630/experience_sgd"
)
A32_NAME = "attempt32_tsgd_v5_core043b_io0040_v1"
A33_NAME = "attempt36_tsgd_v5_resume_rank2_target5_fullsql31_callback_v1"
A32 = LANE / A32_NAME
A33 = LANE / A33_NAME
A32_CONFIG = A32 / "configs/experience_table_all_subset_sgd.tsgd.json"
A32_HISTORY = A32 / "output/hybench_xp_optimizer_history_2026-07-23_040115.jsonl"
A32_BASELINE = A32 / "baseline_reuse/r0_default_point_result.json"
A32_RECLASS = A32 / "baseline_reuse/r0_default_reclassification_evidence.json"
A32_RANK1_SUMMARY = (
    A32 / "candidate_measurements/20260723T041716+0800_440213/summary.json"
)
A32_RANK1_WINDOWS = (
    A32
    / "candidate_measurements/20260723T041716+0800_440213/measurement_windows.jsonl"
)
A32_RANK1_SAMPLES = (
    A32
    / "candidate_measurements/20260723T041716+0800_440213/formal_resource_samples.jsonl"
)
A32_SNAPSHOT = A32 / "runtime_snapshot_043b_v5_unique5"
SNAPSHOT_NAME = "runtime_snapshot_8fef_resume_fullsql31_callback_v1"
EXPECTED_MAIN_SHA = "8fefbe8a8d0bc1e466c6f158fd8ad8fc36be103b2db0a59e8395d33b2bda91e8"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def history_events() -> list[dict]:
    return [
        json.loads(line)
        for line in A32_HISTORY.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def exactly_one(events: list[dict], event_name: str) -> dict:
    matches = [item for item in events if item.get("event") == event_name]
    if len(matches) != 1:
        raise RuntimeError(f"expected one {event_name}, found {len(matches)}")
    return matches[0]


def replace_tree_strings(value, old: str, new: str):
    if isinstance(value, str):
        return value.replace(old, new)
    if isinstance(value, list):
        return [replace_tree_strings(item, old, new) for item in value]
    if isinstance(value, dict):
        return {
            key: replace_tree_strings(item, old, new)
            for key, item in value.items()
        }
    return value


def normalize_sql(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().rstrip(";").lower())


def main() -> int:
    required = [
        A32_CONFIG,
        A32_HISTORY,
        A32_BASELINE,
        A32_RECLASS,
        A32_RANK1_SUMMARY,
        A32_RANK1_WINDOWS,
        A32_RANK1_SAMPLES,
        A32_SNAPSHOT,
        PROJECT / "parameter_optimizer.py",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise RuntimeError(f"missing A33 source artifacts: {missing}")
    if sha256(PROJECT / "parameter_optimizer.py") != EXPECTED_MAIN_SHA:
        raise RuntimeError("shared parameter_optimizer.py SHA changed before A33 build")
    if A33.exists():
        raise RuntimeError(f"refuse to overwrite existing A33 root: {A33}")

    events = history_events()
    ranking = exactly_one(events, "experience_table_all_subset_ranking")
    baseline_resource = exactly_one(events, "baseline_runtime_resource_summary")
    baseline_top = exactly_one(events, "baseline_top_sql")
    rank1_final = exactly_one(events, "final_top_sql")
    exception = exactly_one(events, "optimizer_process_exception")
    if (
        ranking.get("round_index") != 0
        or ranking.get("subset_count") != 63
        or ranking.get("successful_subset_count") != 63
        or ranking.get("failed_subset_count") != 0
    ):
        raise RuntimeError("A32 ranking is not a complete 63/63 round-0 ranking")
    rankings = ranking.get("candidate_rankings") or []
    ranking_by_rank = {int(item["rank"]): item for item in rankings}
    if not all(rank in ranking_by_rank for rank in range(1, 11)):
        raise RuntimeError("A32 ranking does not contain ranks 1..10")
    rank1_params = ranking_by_rank[1].get("optimized_params") or {}
    final_validation = json.loads(
        json.dumps(rank1_final.get("final_validation") or {})
    )
    rank1_protocol = (
        final_validation.get("candidate_measurement_protocol")
        or (final_validation.get("resource_stats") or {}).get(
            "candidate_measurement_protocol"
        )
        or {}
    )
    if rank1_protocol.get("stable") is not True:
        raise RuntimeError("A32 rank1 lacks a sealed stable measurement protocol")
    final_validation["candidate_measurement_protocol"] = rank1_protocol
    rank1_qps = float(final_validation["db_qps"])
    rank1_io = float(final_validation["utilization_pct"]["io"])

    source_config = read_json(A32_CONFIG)
    incumbent_params = source_config.get("initial_params") or {}
    baseline_point = read_json(A32_BASELINE)
    baseline_qps = float(baseline_point["measurement"]["db_qps"])
    baseline_io = float(baseline_resource["utilization_pct"]["io"])
    if rank1_qps >= baseline_qps or rank1_io >= baseline_io:
        raise RuntimeError(
            "A32 rank1 is not the approved DB-QPS-down/IO-down rejected point"
        )

    temporary_parent = Path(tempfile.mkdtemp(prefix=f".{A33_NAME}.", dir=str(LANE)))
    build_root = temporary_parent / A33_NAME
    try:
        for relative in (
            "configs",
            "counters",
            "output",
            "candidate_measurements",
            "replicate_ledgers",
            "resource_models",
            "subset_logs",
            "sealed",
            "wrapper",
        ):
            (build_root / relative).mkdir(parents=True, exist_ok=True)

        shutil.copytree(A32_SNAPSHOT, build_root / SNAPSHOT_NAME)
        snapshot = build_root / SNAPSHOT_NAME
        for old_module in A32_SNAPSHOT.glob("*.py"):
            current_module = PROJECT / old_module.name
            if current_module.is_file():
                shutil.copy2(current_module, snapshot / old_module.name)
        if sha256(snapshot / "parameter_optimizer.py") != EXPECTED_MAIN_SHA:
            raise RuntimeError("A33 runtime snapshot did not bind the approved main SHA")

        shutil.copy2(
            A32 / "wrapper/candidate_stable_measurement_formal_v5.py",
            build_root / "wrapper/candidate_stable_measurement_formal_v5.py",
        )
        shutil.copy2(
            A32 / "wrapper/validate_seed_registry_boundary.py",
            build_root / "wrapper/validate_seed_registry_boundary.py",
        )
        shutil.copy2(
            A32 / "configs/hybench_measurement_180_600s.props",
            build_root / "configs/hybench_measurement_180_600s.props",
        )
        shutil.copy2(
            A32 / "configs/hybench_warmup_60s.props",
            build_root / "configs/hybench_warmup_60s.props",
        )
        shutil.copytree(A32 / "baseline_reuse", build_root / "baseline_reuse")
        shutil.copy2(
            A32 / "sealed_startup_baseline_sql_xp31_v1.json",
            build_root / "sealed_startup_baseline_sql_xp31_v1.json",
        )

        rank1_evidence = {
            "schema_version": 1,
            "kind": "sealed_partial_initial_replay_rejection_v1",
            "campaign_id": "pg5_common_outer_bgallowed_20260722_v1",
            "benchmark": "hybench_xp",
            "target_resource": "io",
            "round_index": 0,
            "estimated_rank": 1,
            "candidate_params": rank1_params,
            "incumbent": {
                "params": incumbent_params,
                "db_qps": baseline_qps,
                "io_utilization_pct": baseline_io,
                "point_result_path": str(A32_BASELINE),
                "point_result_sha256": sha256(A32_BASELINE),
            },
            "candidate": {
                "db_qps": rank1_qps,
                "io_utilization_pct": rank1_io,
                "stable": True,
                "correctness_passed": True,
                "summary_path": str(A32_RANK1_SUMMARY),
                "summary_sha256": sha256(A32_RANK1_SUMMARY),
                "measurement_windows_path": str(A32_RANK1_WINDOWS),
                "measurement_windows_sha256": sha256(A32_RANK1_WINDOWS),
                "formal_resource_samples_path": str(A32_RANK1_SAMPLES),
                "formal_resource_samples_sha256": sha256(A32_RANK1_SAMPLES),
            },
            "strict_acceptance": {
                "db_qps_strictly_higher": rank1_qps > baseline_qps,
                "target_io_strictly_lower": rank1_io < baseline_io,
                "accepted": False,
                "reason": "db_qps_not_strictly_higher",
            },
            "source_history_path": str(A32_HISTORY),
            "source_history_sha256": sha256(A32_HISTORY),
            "source_failure": {
                "exception_type": exception.get("exception_type"),
                "exception_message": exception.get("exception_message"),
                "failure_after_rank1_evidence": True,
            },
        }
        rank1_evidence_path = build_root / "sealed/rank1_rejection_evidence.json"
        final_rank1_evidence_path = A33 / "sealed/rank1_rejection_evidence.json"
        write_json(rank1_evidence_path, rank1_evidence)

        baseline_validation = {
            "resource_raw": baseline_resource["resource_raw"],
            "resource_stats": baseline_resource.get("resource_stats") or {},
            "resource_normalized": baseline_resource["resource_normalized"],
            "utilization_pct": baseline_resource["utilization_pct"],
            "candidate_measurement_protocol": {
                "point_id": baseline_point["point_id"],
                "correctness_passed": True,
                "stable": True,
                "source": "sealed_A32_startup_baseline",
            },
            "correctness_passed": True,
            "db_qps": baseline_qps,
            "formal_qps": baseline_qps,
            "db_qps_source": "formal_benchmark_db_qps",
            "formal_qps_source": "formal_benchmark_db_qps",
        }
        rank1_evaluation = {
            "candidate_index": 1,
            "estimated_rank": 1,
            "estimated_score": ranking_by_rank[1].get("final_score"),
            "candidate_actual_score": None,
            "accepted_by_actual_score": False,
            "actual_accept_reason": "db_qps_not_strictly_higher",
            "common_outer_acceptance": {
                "accepted": False,
                "db_qps_before": baseline_qps,
                "db_qps_after": rank1_qps,
                "target_resource": "io",
                "target_before_pct": baseline_io,
                "target_after_pct": rank1_io,
            },
            "performance_guard_ok": True,
            "candidate_optimized_params": rank1_params,
            "candidate_estimated_resource": ranking_by_rank[1].get("final_resource") or {},
            "candidate_final_validation": final_validation,
            "candidate_top_sql": rank1_final.get("top") or [],
            "candidate_plan_samples": [],
            "candidate_plan_changes": [],
            "candidate_degradation_summary": {},
        }
        range_state = {}
        active_params = set(source_config["optimizer"]["gradient"]["active_params"])
        for name, record in source_config["param_ranges"].items():
            range_state[name] = {
                "min_value": float(record["min_value"]),
                "max_value": float(record["max_value"]),
                "transform": (
                    "log"
                    if name in {
                        "seq_page_cost",
                        "random_page_cost",
                        "cpu_tuple_cost",
                        "cpu_index_tuple_cost",
                        "cpu_operator_cost",
                        "parallel_tuple_cost",
                    }
                    else "linear"
                ),
            }
            if name in active_params and range_state[name]["min_value"] >= range_state[name]["max_value"]:
                raise RuntimeError(f"active A33 parameter has a non-positive range: {name}")

        sealed_sql = read_json(A32 / "sealed_startup_baseline_sql_xp31_v1.json")
        sealed_queries = sealed_sql.get("queries") or []
        if len(sealed_queries) != 31:
            raise RuntimeError("A32 sealed workload SQL does not contain exactly 31 queries")
        sealed_query_texts = {normalize_sql(item.get("query")) for item in sealed_queries}
        if len(sealed_query_texts) != 31 or "" in sealed_query_texts:
            raise RuntimeError("A32 sealed workload SQL is empty or duplicated")
        plan_capture_path = Path(str(baseline_top.get("plan_capture_file") or ""))
        plan_capture = read_json(plan_capture_path)
        baseline_plan_samples = [
            item
            for item in (plan_capture.get("items") or [])
            if normalize_sql(item.get("query")) in sealed_query_texts
        ]
        if len(baseline_plan_samples) != 31:
            raise RuntimeError(
                "A32 complete plan capture did not resolve exactly 31 sealed workload plans"
            )
        baseline_workload_profile = dict(
            baseline_top.get("plan_estimator_workload_profile") or {}
        )
        baseline_workload_profile["sql_count"] = 31
        baseline_workload_profile["sealed_workload_query_count"] = 31
        baseline_workload_profile["sealed_workload_source"] = str(
            A32 / "sealed_startup_baseline_sql_xp31_v1.json"
        )
        checkpoint = {
            "schema_version": 1,
            "kind": "gradient_partial_initial_round_resume_checkpoint_v1",
            "source_history_path": str(A32_HISTORY),
            "source_history_sha256": sha256(A32_HISTORY),
            "source_config_path": str(A32_CONFIG),
            "source_config_sha256": sha256(A32_CONFIG),
            "incumbent_evidence_path": str(A32_BASELINE),
            "incumbent_evidence_sha256": sha256(A32_BASELINE),
            "baseline_evidence_path": str(A32_BASELINE),
            "baseline_evidence_sha256": sha256(A32_BASELINE),
            "accepted_params": incumbent_params,
            "accepted_validation": baseline_validation,
            "baseline_validation": baseline_validation,
            "accepted_estimated_resource": baseline_resource["resource_normalized"],
            "replayed_scope_incumbent_params": incumbent_params,
            "replayed_candidate_params": [rank1_params],
            "replayed_candidate_ranks": [1],
            "replayed_candidate_evaluations": [rank1_evaluation],
            "replayed_candidate_evidence": [
                {
                    "estimated_rank": 1,
                    "point_result_path": str(final_rank1_evidence_path),
                    "point_result_sha256": sha256(rank1_evidence_path),
                }
            ],
            "round0_ranking_event": ranking,
            "round0_replay_plan_event": {
                "event": "experience_table_gradient_real_replay_plan",
                "round_index": 0,
                "attempt_index": 0,
                "initial_batch_size": 5,
                "max_cumulative_batch_size": 10,
                "selected_ranks": list(range(1, 11)),
                "source": "approved_A33_explicit_resume_plan_from_sealed_A32_ranking",
            },
            "resume_next_estimated_rank": 2,
            "resume_same_ranked_scope": True,
            "next_feedback_round": 0,
            "expected_range_state_after_accept": range_state,
            "online_sensitivity_table": source_config["sensitivity_table"],
            "baseline_top_sql": sealed_queries,
            "baseline_plan_samples": baseline_plan_samples,
            "baseline_workload_profile": baseline_workload_profile,
            "counters": {
                "accepted_shrink_count": 0,
                "completed_outer_explorations": 0,
                "round0_replayed_candidate_count": 1,
            },
            "resume_contract": {
                "do_not_rerun_r0": True,
                "do_not_rerun_round0": True,
                "do_not_rerun_round0_subsets": True,
                "do_not_rerun_replayed_ranks": True,
                "technical_failures_excluded_from_composite": True,
                "first_unstable_then_total_valid_replicates": 3,
                "next_real_replay_rank": 2,
                "target_resource": "io",
                "accepted_shrink_target": 5,
                "normalized_width_target": 1.0 / 32.0,
            },
        }
        checkpoint_path = build_root / "sealed/partial_initial_round_resume_checkpoint.json"
        final_checkpoint_path = A33 / "sealed/partial_initial_round_resume_checkpoint.json"
        write_json(checkpoint_path, checkpoint)

        config = replace_tree_strings(
            source_config,
            str(A32),
            str(A33),
        )
        config = replace_tree_strings(config, A32_NAME, A33_NAME)
        protocol = config["formal_outer_protocol"]
        protocol.update(
            {
                "formal_search_accepted_target": 5,
                "formal_search_replay_count_start": 1,
                "formal_search_accepted_count_start": 0,
                "global_replay_count_start": 1,
                "global_replay_ordinal_at_binding": 1,
                "formal_search_resume_checkpoint_path": str(final_checkpoint_path),
                "formal_search_resume_checkpoint_sha256": sha256(checkpoint_path),
            }
        )
        config["startup_baseline_reuse"]["startup_baseline_count_before_restart"] = 1
        config["optimizer"]["gradient"]["subset_log_dir"] = str(A33 / "subset_logs")
        config["_attempt36_resume_contract"] = {
            "source_attempt": A32_NAME,
            "source_rank1_disposition": "sealed_rejected_db_qps_down_io_down",
            "resume_rank": 2,
            "do_not_rerun_r0": True,
            "do_not_rerun_round0_subsets": True,
            "do_not_rerun_rank1": True,
            "first_batch_cumulative_ranks": [1, 2, 3, 4, 5],
            "second_batch_cumulative_ranks": list(range(1, 11)),
            "accepted_shrink_target": 5,
            "normalized_width_target": 1.0 / 32.0,
            "sealed_baseline_workload_query_count": 31,
            "sealed_baseline_plan_sample_count": 31,
            "rank1_protocol_promoted_from_resource_stats_without_value_changes": True,
            "parameter_optimizer_sha256": EXPECTED_MAIN_SHA,
        }
        config_path = build_root / "configs/experience_table_all_subset_sgd.resume.json"
        final_config_path = A33 / "configs/experience_table_all_subset_sgd.resume.json"
        write_json(config_path, config)

        counters = {
            "global_replay.count": 1,
            "formal_search_replay.count": 1,
            "formal_search_accepted.count": 0,
            "formal_startup_baseline.count": 1,
        }
        for filename, value in counters.items():
            (build_root / "counters" / filename).write_text(
                f"{value}\n", encoding="utf-8"
            )

        manifest = {
            "schema_version": 1,
            "campaign_id": "pg5_common_outer_bgallowed_20260722_v1",
            "lane_id": "hybench_xp_compute00_59630",
            "attempt": A33_NAME,
            "status": "prepared",
            "resume": {
                "source_attempt": A32_NAME,
                "source_round": 0,
                "sealed_baseline_workload_query_count": 31,
                "sealed_baseline_plan_sample_count": 31,
                "sealed_subset_ranking": "63/63 successful",
                "sealed_rejected_ranks": [1],
                "resume_next_rank": 2,
                "accepted_shrink_count_start": 0,
                "accepted_shrink_target": 5,
                "normalized_width_target": 1.0 / 32.0,
                "replay_tiers": [5, 10],
            },
            "measurement": {
                "target_stability_resource": "io_utilization",
                "reported_window_numbering": "one_based",
                "decision_checkpoints_seconds": list(range(180, 601, 30)),
                "unstable_action_before_maximum": "continue_exactly_30_seconds",
            },
            "artifacts": {
                "config_path": str(final_config_path),
                "config_sha256": sha256(config_path),
                "checkpoint_path": str(final_checkpoint_path),
                "checkpoint_sha256": sha256(checkpoint_path),
                "rank1_rejection_evidence_path": str(final_rank1_evidence_path),
                "rank1_rejection_evidence_sha256": sha256(rank1_evidence_path),
                "parameter_optimizer_path": str(A33 / SNAPSHOT_NAME / "parameter_optimizer.py"),
                "parameter_optimizer_sha256": sha256(snapshot / "parameter_optimizer.py"),
                "wrapper_sha256": sha256(
                    build_root / "wrapper/candidate_stable_measurement_formal_v5.py"
                ),
                "source_history_sha256": sha256(A32_HISTORY),
                "source_config_sha256": sha256(A32_CONFIG),
            },
        }
        write_json(build_root / "formal_launch_manifest.json", manifest)
        build_root.rename(A33)
        temporary_parent.rmdir()
    except Exception:
        shutil.rmtree(temporary_parent, ignore_errors=True)
        raise

    print(json.dumps({"attempt_root": str(A33)}, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
