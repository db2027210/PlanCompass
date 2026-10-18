#!/usr/bin/env python3
"""Seal JOB p42 partial resume checkpoint and its 5/10/16 config."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Mapping

from common_outer_loop import (
    prune_ranked_candidates_for_replay,
    select_plan_diverse_representatives,
)
from parameter_optimizer import _build_empirical_resource_box_pruning_input
from resource_utility import ResourceUtilityCalculator


EXPECTED_REPRESENTATIVE_RANKS = [1, 2, 3, 4, 12]


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


def history_events(path: Path) -> list[Dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def exact_params_equal(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return set(left) == set(right) and all(
        abs(float(left[name]) - float(right[name]))
        <= max(5e-7, abs(float(right[name])) * 1e-5)
        for name in left
    )


def build_calculator(config: Mapping[str, Any]) -> ResourceUtilityCalculator:
    scoring = config.get("scoring") or {}
    return ResourceUtilityCalculator(
        reward_type="sigmoid_linear",
        penalty_type="sigmoid_linear",
        overload_threshold=float(scoring.get("overload_threshold", 0.8)),
        score_mode=str(scoring.get("score_mode") or "band_target"),
        resource_weights=scoring.get("resource_weights"),
        resource_reward_weights=scoring.get("resource_reward_weights"),
        resource_penalty_weights=scoring.get("resource_penalty_weights"),
        resource_target_ranges=scoring.get("resource_target_ranges"),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt-root", type=Path, required=True)
    parser.add_argument("--source-config", type=Path, required=True)
    parser.add_argument("--source-history", type=Path, required=True)
    parser.add_argument("--enriched-ranking", type=Path, required=True)
    parser.add_argument("--p40-checkpoint", type=Path, required=True)
    parser.add_argument("--p42-result", type=Path, required=True)
    parser.add_argument("--checkpoint-output", type=Path, required=True)
    parser.add_argument("--config-output", type=Path, required=True)
    parser.add_argument("--audit-output", type=Path, required=True)
    args = parser.parse_args()
    attempt = args.attempt_root.resolve()
    source_config_path = args.source_config.resolve()
    source_history_path = args.source_history.resolve()
    enriched_path = args.enriched_ranking.resolve()
    p40_checkpoint_path = args.p40_checkpoint.resolve()
    p42_path = args.p42_result.resolve()
    checkpoint_output = args.checkpoint_output.resolve()
    config_output = args.config_output.resolve()
    audit_output = args.audit_output.resolve()
    for output in (checkpoint_output, config_output, audit_output):
        if output.exists() or output.is_symlink():
            raise RuntimeError(f"sealed output already exists: {output}")

    config = json.loads(source_config_path.read_text(encoding="utf-8"))
    prior = json.loads(p40_checkpoint_path.read_text(encoding="utf-8"))
    enriched = json.loads(enriched_path.read_text(encoding="utf-8"))
    p42 = json.loads(p42_path.read_text(encoding="utf-8"))
    events = history_events(source_history_path)
    baseline_top = next(item for item in events if item.get("event") == "baseline_top_sql")
    baseline_resource = next(
        item for item in events if item.get("event") == "baseline_runtime_resource_summary"
    )
    ranking_event = copy.deepcopy(enriched)
    ranking_event["event"] = "experience_table_all_subset_ranking"
    ranking_event["round_index"] = 1

    candidates = list(ranking_event.get("candidate_rankings") or [])
    candidate_by_rank = {int(item["rank"]): item for item in candidates}
    if sorted(candidate_by_rank) != list(range(1, 13)):
        raise RuntimeError("enriched ranking must contain exact ranks 1..12")
    accepted_validation = prior.get("accepted_validation") or {}
    capability = config.get("capability") or {}
    calculator = build_calculator(config)
    incumbent_resource = accepted_validation.get("resource_normalized") or {}
    best_score = calculator.calculate_utility_score(
        {name: float(incumbent_resource[name]) for name in ("cpu", "memory", "io")},
        {name: float(capability[name]) for name in ("cpu", "memory", "io")},
    )
    enriched_for_recall = []
    for raw_candidate in candidates:
        candidate = copy.deepcopy(raw_candidate)
        candidate["optimistic_replay_pruning_input"] = (
            _build_empirical_resource_box_pruning_input(candidate)
        )
        enriched_for_recall.append(candidate)
    kept, pruned, pruning_checks = prune_ranked_candidates_for_replay(
        enriched_for_recall,
        incumbent_db_qps=accepted_validation.get("db_qps"),
        incumbent_target_utilization=(
            (accepted_validation.get("utilization_pct") or {}).get("memory")
        ),
        capability=capability,
        utility_calculator=calculator,
        best_score=best_score,
    )
    representatives, suppressed, plan_audit = select_plan_diverse_representatives(kept)
    representative_ranks = [int(item["rank"]) for item in representatives]
    if pruned or representative_ranks != EXPECTED_REPRESENTATIVE_RANKS:
        raise RuntimeError(
            f"unexpected recall result: pruned={[item['rank'] for item in pruned]} "
            f"representatives={representative_ranks}"
        )
    if not all(check["decision"].get("bounds_usable") is True for check in pruning_checks):
        raise RuntimeError("one or more JOB empirical error boxes are unusable")

    p42_measurement = p42.get("measurement") or {}
    p42_stability = p42_measurement.get("resource_stability") or {}
    p42_params = p42.get("cost_parameters") or {}
    if (
        p42.get("status") != "complete"
        or p42_measurement.get("correctness_passed") is not True
        or p42_stability.get("stable") is not True
        or p42_stability.get("passed") is not True
        or p42.get("oom_killed")
        or int(p42.get("database_restart_count") or 0) != 0
        or not exact_params_equal(p42_params, candidate_by_rank[1]["optimized_params"])
    ):
        raise RuntimeError("p42 is not valid sealed rank-1 replay evidence")

    baseline_protocol = copy.deepcopy(
        (baseline_resource.get("resource_stats") or {}).get(
            "candidate_measurement_protocol"
        )
        or {}
    )
    if baseline_protocol.get("correctness_passed") is not True:
        raise RuntimeError("sealed baseline failed correctness")
    baseline_protocol["stable"] = bool(
        baseline_protocol.get("stable")
        or (baseline_protocol.get("resource_stability") or {}).get("stable")
    )
    if baseline_protocol.get("stable") is not True:
        raise RuntimeError("sealed baseline is not stable")
    baseline_benchmark = copy.deepcopy(baseline_top.get("baseline_benchmark") or {})
    baseline_qps = float(
        baseline_benchmark.get("db_qps")
        or baseline_benchmark.get("formal_qps")
    )
    baseline_validation = {
        "resource_raw": copy.deepcopy(baseline_resource.get("resource_raw") or {}),
        "resource_stats": copy.deepcopy(baseline_resource.get("resource_stats") or {}),
        "resource_normalized": copy.deepcopy(
            baseline_resource.get("resource_normalized") or {}
        ),
        "utilization_pct": copy.deepcopy(baseline_resource.get("utilization_pct") or {}),
        "benchmark": baseline_benchmark,
        "benchmark_latency": copy.deepcopy(
            baseline_top.get("baseline_benchmark_latency") or {}
        ),
        "db_qps": baseline_qps,
        "formal_qps": baseline_qps,
        "correctness_passed": True,
        "candidate_measurement_protocol": baseline_protocol,
    }
    baseline_path = Path(str(prior.get("baseline_point_result_path") or "")).resolve()
    if not baseline_path.is_file():
        raise RuntimeError("prior checkpoint baseline point is missing")
    plan_capture_path = Path(str(baseline_top.get("plan_capture_file") or ""))
    baseline_plan_samples = (
        json.loads(plan_capture_path.read_text(encoding="utf-8"))
        if plan_capture_path.is_file()
        else copy.deepcopy(baseline_top.get("plan_hashes") or [])
    )
    if isinstance(baseline_plan_samples, dict):
        baseline_plan_samples = (
            baseline_plan_samples.get("plans")
            or baseline_plan_samples.get("samples")
            or []
        )

    replay_plan = {
        "event": "experience_table_gradient_real_replay_plan",
        "round_index": 1,
        "ranking_count": len(candidates),
        "selected_count": len(representative_ranks),
        "selected_ranks": representative_ranks,
        "initial_batch_size": 5,
        "second_cumulative_batch_size": 10,
        "max_cumulative_batch_size": 16,
        "optimistic_pruning_framework": (
            "calibrated_multidimensional_utility_box_before_exact_plan_cluster_recall"
        ),
        "optimistically_pruned_count": len(pruned),
        "optimistically_pruned_ranks": [int(item["rank"]) for item in pruned],
        "optimistic_pruning_checks": pruning_checks,
        "plan_diversity_audit": plan_audit,
        "similar_plan_suppressed_count": len(suppressed),
        "similar_plan_suppressed_ranks": [int(item["rank"]) for item in suppressed],
        "plan_cluster_basis": "exact_physical_bundle_only",
        "approximate_plan_features_role": "audit_only",
        "candidate_space_exhausted": False,
        "transition_completed_replay_ranks": [1],
    }
    counters = {
        "global_replay_ordinal": int((attempt / "replay_counter.txt").read_text().strip()),
        "formal_search_replay_count": int(
            (attempt / "formal_search_replay_counter.txt").read_text().strip()
        ),
        "formal_startup_baseline_count": int(
            (attempt / "formal_startup_baseline_counter.txt").read_text().strip()
        ),
        "accepted_shrink_count": int(
            (attempt / "formal_search_accepted_counter.txt").read_text().strip()
        ),
        "completed_outer_explorations": 1,
        "round1_replayed_candidate_count": 1,
    }
    if counters != {
        "global_replay_ordinal": 42,
        "formal_search_replay_count": 11,
        "formal_startup_baseline_count": 1,
        "accepted_shrink_count": 1,
        "completed_outer_explorations": 1,
        "round1_replayed_candidate_count": 1,
    }:
        raise RuntimeError(f"unexpected JOB transition counters: {counters}")
    checkpoint = {
        "schema_version": 1,
        "kind": "gradient_partial_round_resume_checkpoint_v1",
        "source_history_path": str(enriched_path),
        "source_history_sha256": sha256(enriched_path),
        "source_config_path": str(source_config_path),
        "source_config_sha256": sha256(source_config_path),
        "accepted_evidence_path": str(Path(prior["accepted_point_result_path"]).resolve()),
        "accepted_evidence_sha256": sha256(Path(prior["accepted_point_result_path"])),
        "baseline_evidence_path": str(baseline_path),
        "baseline_evidence_sha256": sha256(baseline_path),
        "accepted_params": copy.deepcopy(prior["accepted_params"]),
        "accepted_validation": copy.deepcopy(accepted_validation),
        "accepted_estimated_resource": copy.deepcopy(
            prior.get("accepted_estimated_resource") or {}
        ),
        "accepted_final_top_sql": copy.deepcopy(prior.get("accepted_final_top_sql") or []),
        "accepted_final_plan_samples": copy.deepcopy(
            prior.get("accepted_final_plan_samples") or []
        ),
        "accepted_final_plan_changes": copy.deepcopy(
            prior.get("accepted_final_plan_changes") or []
        ),
        "accepted_degradation_summary": copy.deepcopy(
            prior.get("accepted_degradation_summary") or {}
        ),
        "baseline_validation": baseline_validation,
        "baseline_top_sql": copy.deepcopy(baseline_top.get("top") or []),
        "baseline_plan_samples": baseline_plan_samples,
        "baseline_workload_profile": copy.deepcopy(
            baseline_top.get("plan_estimator_workload_profile") or {}
        ),
        "online_sensitivity_table": copy.deepcopy(prior["online_sensitivity_table"]),
        "expected_range_state_after_accept": copy.deepcopy(
            prior["expected_range_state_after_accept"]
        ),
        "replayed_scope_incumbent_params": copy.deepcopy(
            prior["replayed_scope_incumbent_params"]
        ),
        "replayed_candidate_params": [
            copy.deepcopy(candidate_by_rank[1]["optimized_params"])
        ],
        "replayed_candidate_ranks": [1],
        "replayed_candidate_evidence": [
            {
                "rank": 1,
                "point_result_path": str(p42_path),
                "point_result_sha256": sha256(p42_path),
                "db_qps": p42_measurement.get("db_qps"),
                "correctness_passed": True,
                "stable": True,
            }
        ],
        "round1_ranking_event": ranking_event,
        "round1_replay_plan_event": replay_plan,
        "resume_next_estimated_rank": 2,
        "resume_same_ranked_scope": True,
        "next_feedback_round": 1,
        "counters": counters,
        "resume_contract": {
            "do_not_rerun_r0": True,
            "do_not_rerun_round0": True,
            "do_not_rerun_round1_subsets": True,
            "do_not_rerun_replayed_ranks": True,
            "next_real_replay_rank": 2,
            "technical_failures_excluded_from_composite": True,
            "first_unstable_then_total_valid_replicates": 3,
            "first_stable_replicate_used_directly": True,
            "later_stable_replicate_short_circuit": False,
        },
        "plan_fingerprint_artifact_path": str(enriched_path),
        "plan_fingerprint_artifact_sha256": sha256(enriched_path),
        "parameter_optimizer_sha256": sha256(Path(__import__("parameter_optimizer").__file__)),
        "common_outer_loop_sha256": sha256(Path(__import__("common_outer_loop").__file__)),
        "resource_utility_sha256": sha256(Path(__import__("resource_utility").__file__)),
    }
    write_json(checkpoint_output, checkpoint)
    checkpoint_sha = sha256(checkpoint_output)
    resumed_config = copy.deepcopy(config)
    protocol = resumed_config.setdefault("formal_outer_protocol", {})
    protocol.update(
        {
            "global_replay_ordinal_at_binding": 42,
            "formal_search_initial_replay_top_k": 5,
            "formal_search_second_replay_top_k": 10,
            "formal_search_max_replay_top_k": 16,
            "formal_search_accepted_target": 10,
            "formal_search_resume_checkpoint_path": str(checkpoint_output),
            "formal_search_resume_checkpoint_sha256": checkpoint_sha,
            "formal_search_replay_count_start": 11,
            "formal_search_accepted_count_start": 1,
            "resume_next_feedback_round": 1,
            "resume_replays_are_evidence_only_no_rerun": True,
            "plan_cluster_basis": "exact_physical_bundle_only",
            "plan_fingerprint_artifact_path": str(enriched_path),
            "plan_fingerprint_artifact_sha256": sha256(enriched_path),
        }
    )
    write_json(config_output, resumed_config)
    audit = {
        "schema_version": 1,
        "status": "sealed",
        "representative_ranks": representative_ranks,
        "next_rank": 2,
        "bounds_usable_count": sum(
            check["decision"].get("bounds_usable") is True for check in pruning_checks
        ),
        "pruned_ranks": [],
        "suppressed_ranks": replay_plan["similar_plan_suppressed_ranks"],
        "checkpoint_path": str(checkpoint_output),
        "checkpoint_sha256": checkpoint_sha,
        "config_path": str(config_output),
        "config_sha256": sha256(config_output),
        "p42_result_sha256": sha256(p42_path),
        "source_enriched_ranking_sha256": sha256(enriched_path),
        "deployed_core_sha256": {
            "parameter_optimizer.py": checkpoint["parameter_optimizer_sha256"],
            "common_outer_loop.py": checkpoint["common_outer_loop_sha256"],
            "resource_utility.py": checkpoint["resource_utility_sha256"],
        },
    }
    write_json(audit_output, audit)
    print(json.dumps(audit, ensure_ascii=False, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
