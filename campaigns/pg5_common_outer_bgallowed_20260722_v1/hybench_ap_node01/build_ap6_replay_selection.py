#!/usr/bin/env python3
"""Module."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Dict, List


LANE_DIR = Path(__file__).resolve().parent
BASE_ROOT = LANE_DIR.parents[2]
WORKSPACE_CORE = BASE_ROOT / "workspace/pg"
REPO_ROOT = (
    WORKSPACE_CORE
    if (WORKSPACE_CORE / "common_outer_loop.py").is_file()
    else BASE_ROOT
)

import sys

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from common_outer_loop import (  # noqa: E402
    prune_ranked_candidates_for_replay,
    select_plan_diverse_representatives,
)


ACTIVE_PARAMETERS = [
    "seq_page_cost",
    "random_page_cost",
    "cpu_tuple_cost",
    "cpu_operator_cost",
    "parallel_setup_cost",
    "parallel_tuple_cost",
]
CAPABILITY = {
    "cpu": 5.5,
    "memory": 223_155_000.0,
    "io": 103_809_024.0,
}
EXPECTED_CANDIDATE_COUNT = 63
REQUESTED_CUMULATIVE_STAGE_LIMITS = [5, 10]
ERROR_BOX_UNAVAILABLE_REASON = (
    "no_current_scope_calibrated_AP6_resource_error_box_artifact"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"JSON : {path}")
    return payload


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


def _build_stages(representatives: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    stages: List[Dict[str, Any]] = []
    previous_effective_limit = 0
    for stage_index, requested_limit in enumerate(
        REQUESTED_CUMULATIVE_STAGE_LIMITS,
        start=1,
    ):
        effective_limit = min(requested_limit, len(representatives))
        cumulative = representatives[:effective_limit]
        newly_added = representatives[previous_effective_limit:effective_limit]
        stages.append(
            {
                "stage_index": stage_index,
                "requested_cumulative_limit": requested_limit,
                "effective_cumulative_limit": effective_limit,
                "cumulative_representative_ranks": [
                    candidate.get("rank") for candidate in cumulative
                ],
                "new_representative_ranks": [
                    candidate.get("rank") for candidate in newly_added
                ],
                "quota_fully_available": effective_limit == requested_limit,
                "exhausted_exact_plan_representatives": (
                    effective_limit == len(representatives)
                ),
            }
        )
        previous_effective_limit = effective_limit
    return stages


def _parameter_vector(candidate: Dict[str, Any]) -> List[float]:
    params = candidate.get("optimized_params") or {}
    return [
        math.log(max(float(params[name]), 1e-300))
        for name in ACTIVE_PARAMETERS
    ]


def _supplement_parameter_diverse_representatives(
    kept: List[Dict[str, Any]],
    plan_representatives: List[Dict[str, Any]],
    limit: int,
) -> List[Dict[str, Any]]:
    """
    Keep one representative per exact plan first, then fill the replay budget
    with parameter-diverse points.  An exact plan match is not permission to
    collapse a requested 5/10-point recall batch to one runtime observation.
    """
    unique: List[Dict[str, Any]] = []
    signatures = set()
    for candidate in sorted(kept, key=lambda item: int(item["rank"])):
        signature = str(
            candidate.get("applied_parameter_signature_sha256")
            or json.dumps(
                candidate.get("optimized_params") or {},
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        if signature in signatures:
            continue
        signatures.add(signature)
        unique.append(candidate)

    selected: List[Dict[str, Any]] = []
    selected_ranks = set()
    for candidate in sorted(
        plan_representatives,
        key=lambda item: int(item["rank"]),
    ):
        rank = int(candidate["rank"])
        if rank not in selected_ranks:
            selected.append(candidate)
            selected_ranks.add(rank)
        if len(selected) >= limit:
            return selected

    vectors = {int(candidate["rank"]): _parameter_vector(candidate) for candidate in unique}
    dimensions = list(zip(*vectors.values()))
    lower = [min(values) for values in dimensions]
    upper = [max(values) for values in dimensions]

    def normalized(rank: int) -> List[float]:
        values = vectors[rank]
        return [
            0.0 if hi <= lo else (value - lo) / (hi - lo)
            for value, lo, hi in zip(values, lower, upper)
        ]

    def distance(left_rank: int, right_rank: int) -> float:
        left = normalized(left_rank)
        right = normalized(right_rank)
        return math.sqrt(
            sum((left_value - right_value) ** 2 for left_value, right_value in zip(left, right))
        )

    while len(selected) < min(limit, len(unique)):
        remaining = [
            candidate
            for candidate in unique
            if int(candidate["rank"]) not in selected_ranks
        ]
        if not remaining:
            break
        if not selected:
            chosen = remaining[0]
        else:
            chosen = max(
                remaining,
                key=lambda candidate: (
                    min(
                        distance(
                            int(candidate["rank"]),
                            int(existing["rank"]),
                        )
                        for existing in selected
                    ),
                    -int(candidate["rank"]),
                ),
            )
        selected.append(chosen)
        selected_ranks.add(int(chosen["rank"]))
    return selected


def build(
    ranking_path: Path,
    baseline_path: Path,
    output_dir: Path,
    expected_ranking_sha256: str,
) -> None:
    ranking_sha256 = _sha256(ranking_path)
    if ranking_sha256 != expected_ranking_sha256:
        raise ValueError(
            " SHA256 : "
            f"{ranking_sha256} != {expected_ranking_sha256}"
        )

    ranking = _load_json(ranking_path)
    baseline = _load_json(baseline_path)
    candidates = copy.deepcopy(ranking.get("candidate_rankings"))
    if not isinstance(candidates, list) or len(candidates) != EXPECTED_CANDIDATE_COUNT:
        raise ValueError(" 63  AP ")
    if [candidate.get("rank") for candidate in candidates] != list(
        range(1, EXPECTED_CANDIDATE_COUNT + 1)
    ):
        raise ValueError(" rank  1..63 ")

    for candidate in candidates:
        if candidate.get("plan_similarity_cluster_id") != candidate.get(
            "workload_plan_fingerprint"
        ):
            raise ValueError(
                f"rank={candidate.get('rank')}  ID"
            )
        if not str(candidate.get("workload_plan_fingerprint", "")).startswith(
            "ap13-exact-sha256:"
        ):
            raise ValueError(f"rank={candidate.get('rank')}  AP13 ")
        candidate["optimistic_replay_pruning_input"] = {
            "schema_version": 1,
            "calibration_ready": False,
            "calibration_scope_matches": True,
            "simultaneous_coverage_guaranteed": False,
            "unavailable_reason": ERROR_BOX_UNAVAILABLE_REASON,
            "predicted_resource": copy.deepcopy(candidate.get("final_resource")),
            "predicted_score": candidate.get("final_score"),
            "resource_error_bounds": None,
            "capability": copy.deepcopy(CAPABILITY),
        }

    incumbent_db_qps = baseline.get("db_qps")
    incumbent_utilization = baseline.get("resource_mean_utilization") or {}
    incumbent_target_utilization = incumbent_utilization.get("memory")
    kept, pruned, pruning_checks = prune_ranked_candidates_for_replay(
        candidates,
        incumbent_db_qps=incumbent_db_qps,
        incumbent_target_utilization=incumbent_target_utilization,
        capability=CAPABILITY,
    )
    pruning_reasons = sorted(
        {check["decision"]["reason"] for check in pruning_checks}
    )
    if pruned or len(kept) != EXPECTED_CANDIDATE_COUNT:
        raise ValueError(" fail-open")
    if pruning_reasons != ["calibration_not_ready_keep_for_real_replay"]:
        raise ValueError(f" fail-open : {pruning_reasons}")

    plan_representatives, _, plan_audit = select_plan_diverse_representatives(kept)
    exact_plan_cluster_count = int(plan_audit.get("plan_cluster_count") or 0)
    if exact_plan_cluster_count <= 0:
        raise ValueError("")
    if len(plan_representatives) != len(kept):
        raise ValueError("")
    representatives = list(kept[: REQUESTED_CUMULATIVE_STAGE_LIMITS[-1]])
    if len(representatives) < min(REQUESTED_CUMULATIVE_STAGE_LIMITS[0], len(kept)):
        raise ValueError(" 5 ")
    representative_ranks = {int(candidate["rank"]) for candidate in representatives}
    suppressed = [
        candidate for candidate in kept
        if int(candidate["rank"]) not in representative_ranks
    ]
    plan_audit["plan_only_representative_count"] = len(plan_representatives)
    plan_audit["parameter_diverse_supplement_count"] = 0
    plan_audit["final_representative_count"] = len(representatives)
    plan_audit["selection_strategy"] = (
        "global_estimated_rank_top10_without_plan_shape_suppression"
    )

    error_box_status = {
        "schema_version": 1,
        "workload": "HyBench AP",
        "active_parameters": ACTIVE_PARAMETERS,
        "candidate_count": EXPECTED_CANDIDATE_COUNT,
        "scope": {
            "parameter_set_exact_match": True,
            "workload_exact_match": True,
            "resource_capability_exact_match": True,
        },
        "capability": CAPABILITY,
        "calibration_ready": False,
        "simultaneous_coverage_guaranteed": False,
        "resource_error_bounds": None,
        "reason": ERROR_BOX_UNAVAILABLE_REASON,
        "formal_consequence": (
            "fail_open_keep_all_candidates_before_exact_plan_cluster_selection"
        ),
        "not_permitted": [
            "derive_zero_width_bounds_from_predictions",
            "reuse_the_invalid_seven_parameter_configuration_as_formal_calibration",
            "prune_any_candidate_without_current_scope_simultaneous_coverage",
        ],
    }
    error_box_path = output_dir / "ap6_model_error_box_status.json"
    _write_json(error_box_path, error_box_status)

    stages = _build_stages(representatives)
    selection_plan = {
        "schema_version": 1,
        "status": "sealed_selection_only_real_replay_not_started",
        "workload": "HyBench AP",
        "formal_active_parameters": ACTIVE_PARAMETERS,
        "formal_nonempty_subset_count": EXPECTED_CANDIDATE_COUNT,
        "source_artifacts": {
            "annotated_ranking_path": str(ranking_path),
            "annotated_ranking_sha256": ranking_sha256,
            "source_subset_ranking_sha256": ranking.get("source_ranking_sha256"),
            "baseline_measurement_path": str(baseline_path),
            "baseline_measurement_sha256": _sha256(baseline_path),
            "model_error_box_status_path": str(error_box_path),
            "model_error_box_status_sha256": _sha256(error_box_path),
            "common_outer_loop_path": str(REPO_ROOT / "common_outer_loop.py"),
            "common_outer_loop_sha256": _sha256(REPO_ROOT / "common_outer_loop.py"),
        },
        "incumbent": {
            "db_qps": incumbent_db_qps,
            "target_bottleneck": "memory",
            "target_bottleneck_utilization": incumbent_target_utilization,
        },
        "optimistic_pruning": {
            "order": "before_exact_plan_cluster_selection",
            "input_count": len(candidates),
            "kept_count": len(kept),
            "pruned_count": len(pruned),
            "decision_reasons": pruning_reasons,
            "calibration_ready": False,
            "fail_open": True,
            "checks": pruning_checks,
        },
        "exact_plan_diversity": plan_audit,
        "representative_order": [
            {
                "replay_order": replay_order,
                "rank": candidate.get("rank"),
                "subset_id": candidate.get("subset_id"),
                "active_subset": candidate.get("active_subset"),
                "optimized_params": candidate.get("optimized_params"),
                "predicted_resource": candidate.get("final_resource"),
                "predicted_score": candidate.get("final_score"),
                "exact_plan_cluster_id": candidate.get(
                    "plan_similarity_cluster_id"
                ),
                "replay_plan_cluster_id": candidate.get(
                    "replay_plan_cluster_id"
                ),
            }
            for replay_order, candidate in enumerate(representatives, start=1)
        ],
        "suppressed_candidates": [
            {
                "rank": candidate.get("rank"),
                "representative_rank": candidate.get(
                    "replay_plan_cluster_representative_rank"
                ),
                "exact_plan_cluster_id": candidate.get(
                    "plan_similarity_cluster_id"
                ),
                "reason": (
                    candidate.get("real_replay_suppressed_reason")
                    or "outside_max_real_replay_top10"
                ),
            }
            for candidate in sorted(suppressed, key=lambda item: item.get("rank"))
        ],
        "staged_recall": {
            "requested_cumulative_limits": REQUESTED_CUMULATIVE_STAGE_LIMITS,
            "available_exact_plan_representatives": len(representatives),
            "maximum_real_replay_candidates": 10,
            "advance_rule": (
                "replay_first_five_completely_then_choose_best_acceptable; "
                "only_if_none_acceptable_expand_cumulatively_to_10"
            ),
            "availability_rule": (
                "keep global estimated order; plan cluster is audit-only and "
                "does not suppress real replay"
            ),
            "stages": stages,
        },
        "acceptance_contract": {
            "hard_gate_required": True,
            "db_qps": "candidate_strictly_greater_than_current_incumbent",
            "target_bottleneck_utilization": (
                "candidate_strictly_lower_than_current_incumbent"
            ),
            "accept_if": (
                "hard_gate_passes_and_db_qps_rises_and_target_bottleneck_falls"
            ),
            "rejection_does_not_shrink": True,
            "acceptance_triggers_binary_interval_shrink": True,
        },
        "candidate_records_after_fail_open_pruning": kept,
    }
    selection_path = output_dir / "ap6_replay_selection_plan.json"
    _write_json(selection_path, selection_plan)

    print(
        json.dumps(
            {
                "error_box_status_path": str(error_box_path),
                "error_box_status_sha256": _sha256(error_box_path),
                "selection_plan_path": str(selection_path),
                "selection_plan_sha256": _sha256(selection_path),
                "kept_count": len(kept),
                "pruned_count": len(pruned),
                "plan_cluster_count": plan_audit["plan_cluster_count"],
                "representative_ranks": [
                    candidate.get("rank") for candidate in representatives
                ],
                "suppressed_ranks": sorted(
                    candidate.get("rank") for candidate in suppressed
                ),
                "stages": stages,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ),
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-ranking-sha256", required=True)
    parser.add_argument(
        "--ranking",
        type=Path,
        default=(
            LANE_DIR
            / "results/experience_outer/attempt_2_plan_fingerprints_ap6_v3"
            / "subset_ranking_with_plan_fingerprints.json"
        ),
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=(
            LANE_DIR
            / "results/shared_baseline/P0_repeat_1_attempt_34_retry3"
            / "measurement_summary.json"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=(
            LANE_DIR
            / "results/experience_outer/attempt_2_plan_fingerprints_ap6_v3"
        ),
    )
    args = parser.parse_args()
    build(
        args.ranking.resolve(),
        args.baseline.resolve(),
        args.output_dir.resolve(),
        args.expected_ranking_sha256,
    )


if __name__ == "__main__":
    main()
