#!/usr/bin/env python3
"""Seal the final fresh JOB T-SGD ranking and staged replay representatives."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, List


EXPECTED_CORE_SHA256 = {
    "gradient_descent_optimizer.py": "687006473e87974d6b91a7b26308539c33554fafc715265699a2e61140f42914",
    "experience_table_subset_optimizer.py": "0df42da5cccd55f38d864114285e6b10122bb09c5955db8ebffb55b05da63aa0",
    "parameter_optimizer.py": "f0740d06272a69e8db84a08ad0ce39fe29dbf2992bcc3909f66116be69881095",
    "plan_resource_estimator.py": "d3b06a81382df0d536415680d9c442e1346e7f130c6ebb50a217ed6649fdc8c0",
    "common_outer_loop.py": "61a8b15483f486229f7463df5b1caa68de3211a93794fede7d9c26886d5e7631",
    "resource_utility.py": "cf823efe25d13c90a69cb91deff7b0e2ad805d594ab5bc40d3ed18241d3fd8a2",
}
EXPECTED_HISTORY_SHA256 = "f16c86ee554675471890036727011b6182b4ba3081d0e5e306e7801a398a60c5"
EXPECTED_CONFIG_SHA256 = "8436f6381f88ff3970ca10b24227942c6aebbe1177e5b43b02b3a5ded340a0da"
EXPECTED_SQL_SNAPSHOT_SHA256 = "842413fda8fa783bc8750f6b438a6b6d2aa41d7adef629cf7becbba9f38ab7fb"
EXPECTED_MODEL_SHA256 = {
    "cpu": "23ccf93ec42b85925db8367957d9066dbe553129c69a5c60a3c2185981e7f306",
    "memory": "85188f9c936b3d989adab751e36bd3d8237a104d082199e2dedd05fb1812b23c",
    "io": "4706567aeffd8e3d80935d1169d2c2807f38c31bfa615e07e0e0216655224257",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json_new(path: Path, value: Any) -> None:
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--attempt", type=Path, required=True)
    parser.add_argument("--history", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    workspace = args.workspace.resolve()
    attempt = args.attempt.resolve()
    history = args.history.resolve()
    config_path = args.config.resolve()
    output = args.output.resolve()

    for filename, expected in EXPECTED_CORE_SHA256.items():
        actual = sha256(workspace / filename)
        if actual != expected:
            raise RuntimeError(f"core SHA mismatch: {filename}: {actual} != {expected}")
    if sha256(history) != EXPECTED_HISTORY_SHA256:
        raise RuntimeError("final T-SGD generation history SHA mismatch")
    if sha256(config_path) != EXPECTED_CONFIG_SHA256:
        raise RuntimeError("literal SQL T-SGD config SHA mismatch")
    sql_snapshot = attempt / "sealed_startup_baseline_sql_job8_literal_v2.json"
    if sha256(sql_snapshot) != EXPECTED_SQL_SNAPSHOT_SHA256:
        raise RuntimeError("literal JOB-8 SQL snapshot SHA mismatch")
    for resource, expected in EXPECTED_MODEL_SHA256.items():
        path = attempt / "sealed_resource_models_p31_v1" / f"{resource}_resource_models.json"
        if sha256(path) != expected or path.stat().st_mode & 0o222:
            raise RuntimeError(f"sealed read-only {resource} model validation failed")

    sys.path.insert(0, str(workspace))
    from common_outer_loop import (  # noqa: E402
        prune_ranked_candidates_for_replay,
        select_plan_diverse_representatives,
    )
    from resource_utility import ResourceUtilityCalculator  # noqa: E402

    ranking_events: List[Dict[str, Any]] = []
    sql_events: List[Dict[str, Any]] = []
    with history.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("event") == "experience_table_all_subset_ranking":
                ranking_events.append(record)
            elif record.get("event") == "startup_baseline_sql_snapshot_loaded":
                sql_events.append(record)
    if len(ranking_events) != 1 or len(sql_events) != 1:
        raise RuntimeError("final generation history must have one ranking and one SQL event")
    ranking = ranking_events[0]
    if (
        ranking.get("subset_count") != 127
        or ranking.get("successful_subset_count") != 127
        or ranking.get("failed_subset_count") != 0
        or ranking.get("unique_candidate_count") != 32
        or len(ranking.get("candidate_rankings") or []) != 32
    ):
        raise RuntimeError("final generation ranking counts do not match 127 -> 32")
    sql_event = sql_events[0]
    if (
        sql_event.get("workload_query_count") != 8
        or sql_event.get("filtered_control_query_count") != 0
        or sql_event.get("sha256") != EXPECTED_SQL_SNAPSHOT_SHA256
    ):
        raise RuntimeError("final generation did not use exact literal JOB-8 SQL")

    candidates = copy.deepcopy(ranking["candidate_rankings"])
    for rank, candidate in enumerate(candidates, start=1):
        candidate["rank"] = rank
        coverage = candidate.get("plan_fingerprint_coverage") or {}
        if not (
            coverage.get("complete") is True
            and coverage.get("plannable_query_count") == 8
            and coverage.get("successful_explain_count") == 8
            and coverage.get("failed_explain_count") == 0
            and coverage.get("dynamic_binding_query_count") == 0
            and coverage.get("dynamic_binding_evidence_complete") is True
            and coverage.get("fingerprint_count") == 8
            and len(candidate.get("plan_fingerprints") or {}) == 8
            and candidate.get("plan_similarity_cluster_id")
        ):
            raise RuntimeError(f"candidate rank {rank} lacks complete exact plan evidence")
        pruning_input = candidate.get("optimistic_replay_pruning_input") or {}
        if not (
            pruning_input.get("calibration_ready") is True
            and pruning_input.get("calibration_scope_matches") is True
            and pruning_input.get("simultaneous_coverage_guaranteed") is True
        ):
            raise RuntimeError(f"candidate rank {rank} lacks a usable error box")
        for resource in ("cpu", "memory", "io"):
            bounds = (pruning_input.get("resource_error_bounds") or {}).get(resource) or {}
            artifact_path = Path(str(bounds.get("artifact_path") or ""))
            if sha256(artifact_path) != EXPECTED_MODEL_SHA256[resource]:
                raise RuntimeError(
                    f"candidate rank {rank} {resource} error box model SHA mismatch"
                )

    capability = {"cpu": 3.0, "memory": 2.91015625, "io": 256.0}
    calculator = ResourceUtilityCalculator(
        reward_type="sigmoid_linear",
        penalty_type="sigmoid_linear",
        overload_threshold=0.8,
        score_mode="band_target",
        resource_weights={"cpu": 1.0, "memory": 1.0, "io": 3.0},
        resource_target_ranges={
            "cpu": {"min_pct": 65.0, "max_pct": 80.0, "hard_overload_pct": 90.0},
            "memory": {"min_pct": 70.0, "max_pct": 80.0, "hard_overload_pct": 90.0},
            "io": {"min_pct": 50.0, "max_pct": 75.0, "hard_overload_pct": 90.0},
        },
    )
    incumbent_resource = {
        "cpu": 1.198025781650112,
        "memory": 2.502432668675258,
        "io": 93.0145,
    }
    incumbent_score = calculator.calculate_utility_score(
        incumbent_resource,
        capability,
    )
    kept, pruned, pruning_checks = prune_ranked_candidates_for_replay(
        candidates,
        incumbent_db_qps=2.027730735851316,
        incumbent_target_utilization=85.98963264172698,
        capability=capability,
        utility_calculator=calculator,
        best_score=incumbent_score,
    )
    if len(kept) != 32 or pruned:
        raise RuntimeError("wide conservative error boxes must keep all 32 candidates")
    representatives, suppressed, cluster_audit = select_plan_diverse_representatives(
        kept
    )
    if len(representatives) != len(kept) or suppressed:
        raise RuntimeError("plan clusters are audit-only and must not suppress candidates")
    first_five = representatives[:5]

    def public_candidate(candidate: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "rank": candidate["rank"],
            "subset_id": candidate.get("subset_id"),
            "optimized_params": copy.deepcopy(candidate.get("optimized_params")),
            "final_score": candidate.get("final_score"),
            "score_improvement": candidate.get("score_improvement"),
            "plan_similarity_cluster_id": candidate.get("plan_similarity_cluster_id"),
            "plan_fingerprints": copy.deepcopy(candidate.get("plan_fingerprints")),
            "plan_fingerprint_coverage": copy.deepcopy(
                candidate.get("plan_fingerprint_coverage")
            ),
            "optimistic_replay_pruning_input": copy.deepcopy(
                candidate.get("optimistic_replay_pruning_input")
            ),
        }

    manifest = {
        "schema_version": 1,
        "status": "fresh_tsgd_generation_sealed_ready_for_initial_batch",
        "algorithm": "experience_table_subset_sgd_v1",
        "legacy_grid_candidates_or_acceptance_imported": False,
        "generation_history_path": str(history),
        "generation_history_sha256": EXPECTED_HISTORY_SHA256,
        "config_path": str(config_path),
        "config_sha256": EXPECTED_CONFIG_SHA256,
        "literal_job8_sql_snapshot_path": str(sql_snapshot),
        "literal_job8_sql_snapshot_sha256": EXPECTED_SQL_SNAPSHOT_SHA256,
        "core_sha256": copy.deepcopy(EXPECTED_CORE_SHA256),
        "sealed_model_sha256": copy.deepcopy(EXPECTED_MODEL_SHA256),
        "tests": [
            {"command": "python3 shell/test_sealed_startup_baseline_sql_snapshot.py", "passed": True, "test_count": 4},
            {"command": "python3 shell/test_parameter_optimizer_gradient_subset_outer.py", "passed": True},
            {"command": "python3 shell/test_experience_table_subset_optimizer.py", "passed": True},
        ],
        "generation_counts": {
            "subset_count": 127,
            "successful_subset_count": 127,
            "failed_subset_count": 0,
            "unique_candidate_count": 32,
        },
        "exact_plan_evidence": {
            "complete_candidate_count": 32,
            "query_count_per_candidate": 8,
            "dynamic_binding_query_count": 0,
            "plan_cluster_count": cluster_audit["plan_cluster_count"],
            "representative_count": len(representatives),
            "suppressed_similar_candidate_count": len(suppressed),
            "cluster_audit": cluster_audit,
        },
        "error_box_evidence": {
            "usable_candidate_count": 32,
            "pruned_candidate_count": len(pruned),
            "kept_candidate_count": len(kept),
            "decision_reasons": sorted(
                {check["decision"]["reason"] for check in pruning_checks}
            ),
            "wide_envelope_policy": "conservative_keep_for_real_replay",
        },
        "staged_replay_semantics": {
            "cumulative_batch_sizes": [5, 10, 16],
            "initial_batch_must_complete_before_acceptance_decision": True,
            "expand_to_10_only_if_all_first_5_unacceptable": True,
            "expand_to_16_only_if_all_first_10_unacceptable": True,
            "online_T_update_after_first_real_batch": True,
            "online_T_reranks_unseen_suffix_only": True,
        },
        "representative_ranks_before_online_T": [
            item["rank"] for item in representatives
        ],
        "initial_batch_first_five": [public_candidate(item) for item in first_five],
        "formal_counters_at_seal": {
            "global_physical_lineage": 42,
            "formal_search_replays": 0,
            "accepted_shrinks": 0,
            "accepted_target": 10,
            "startup_baselines": 1,
        },
        "pause_marker_remains_required": True,
    }
    write_json_new(output, manifest)
    output.chmod(0o444)
    print(
        json.dumps(
            {
                "status": manifest["status"],
                "manifest_path": str(output),
                "manifest_sha256": sha256(output),
                "first_five_ranks": [item["rank"] for item in first_five],
                "plan_cluster_count": cluster_audit["plan_cluster_count"],
                "error_box_pruned_count": len(pruned),
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
