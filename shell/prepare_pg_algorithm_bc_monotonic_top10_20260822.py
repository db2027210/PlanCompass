#!/usr/bin/env python3
"""Prepare fresh B/C campaigns for monotonic-QPS Top-10 outer search."""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

from prepare_pg_algorithm_bc_last_accepted_20260822 import (
    B_SOURCE,
    C_SOURCE,
    PROJECT,
    SCENES,
    atomic_json,
    prepare_one,
    read_json,
    sha256,
)


C_DESTINATION = Path(
    "/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/"
    "20260822_algorithm_c_monotonic_qps_top10_current_interval_clean_r74"
)
B_DESTINATION = Path(
    "/disk1/resource_adaptive/pg_algorithm_b_four_grid_20260814/"
    "20260822_algorithm_b_monotonic_qps_top10_current_interval_clean_r10"
)
C_CONTROLLER = PROJECT / "shell/run_pg_algorithm_c_scene.py"
B_CONTROLLER = PROJECT / "shell/run_pg_algorithm_b_scene.py"
SUPERVISOR = PROJECT / "shell/run_pg_algorithm_c_cohort_support_supervisor.py"
FORMAL_TEST = PROJECT / "shell/test_algorithm_c_formal_gate_repairs.py"
B_TEST = PROJECT / "shell/test_pg_algorithm_b_same_framework.py"
QPS_CONTRACT = "candidate_qps_strictly_above_previous_accepted_round_v1"
LOCAL_TOP10_CONTRACT = "candidate_score_distinct_full_plan_local_top10_v1"
COVERAGE_CONTRACT = (
    "deterministic_current_interval_scored_top10_coverage_rescue_v3"
)
CONVERGENCE_CONTRACT = (
    "accepted_target_or_two_consecutive_budgeted_no_improvement_v3"
)
PROTECTED_ROOTS = (
    C_SOURCE,
    B_SOURCE,
    Path(
        "/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/"
        "20260822_algorithm_c_last_accepted_qps_clean_r67"
    ),
    Path(
        "/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/"
        "20260822_algorithm_c_last_accepted_qps_basin_escape_clean_r68"
    ),
    Path(
        "/disk1/resource_adaptive/pg_algorithm_b_four_grid_20260814/"
        "20260822_algorithm_b_last_accepted_qps_basin_escape_clean_r4"
    ),
    Path(
        "/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/"
        "20260822_algorithm_c_monotonic_qps_top10_current_interval_clean_r69"
    ),
    Path(
        "/disk1/resource_adaptive/pg_algorithm_b_four_grid_20260814/"
        "20260822_algorithm_b_monotonic_qps_top10_current_interval_clean_r5"
    ),
    Path(
        "/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/"
        "20260822_algorithm_c_monotonic_qps_top10_current_interval_clean_r70"
    ),
    Path(
        "/disk1/resource_adaptive/pg_algorithm_b_four_grid_20260814/"
        "20260822_algorithm_b_monotonic_qps_top10_current_interval_clean_r6"
    ),
    Path(
        "/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/"
        "20260822_algorithm_c_monotonic_qps_top10_current_interval_clean_r71"
    ),
    Path(
        "/disk1/resource_adaptive/pg_algorithm_b_four_grid_20260814/"
        "20260822_algorithm_b_monotonic_qps_top10_current_interval_clean_r7"
    ),
    Path(
        "/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/"
        "20260822_algorithm_c_monotonic_qps_top10_current_interval_clean_r72"
    ),
    Path(
        "/disk1/resource_adaptive/pg_algorithm_b_four_grid_20260814/"
        "20260822_algorithm_b_monotonic_qps_top10_current_interval_clean_r8"
    ),
    Path(
        "/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/"
        "20260822_algorithm_c_monotonic_qps_top10_current_interval_clean_r73"
    ),
    Path(
        "/disk1/resource_adaptive/pg_algorithm_b_four_grid_20260814/"
        "20260822_algorithm_b_monotonic_qps_top10_current_interval_clean_r9"
    ),
)


def protected_authority_files(root: Path) -> Iterable[Path]:
    for name in (
        "fresh_campaign_manifest.json",
        "algorithm_c_then_b_handoff_status.json",
        "campaign_technical_failure_manifest.json",
    ):
        path = root / name
        if path.is_file():
            yield path
    for scene in SCENES:
        scene_dir = root / scene
        for name in (
            "optimizer_config.json",
            "algorithm_c_state.json",
            "algorithm_c.jsonl",
            "algorithm_b_state.json",
            "algorithm_b.jsonl",
            "authority_manifest.json",
        ):
            path = scene_dir / name
            if path.is_file():
                yield path


def protected_hashes() -> Dict[str, Any]:
    roots: Dict[str, Any] = {}
    for root in PROTECTED_ROOTS:
        files = {
            str(path.relative_to(root)): sha256(path)
            for path in protected_authority_files(root)
        }
        roots[str(root)] = {
            "exists": root.is_dir(),
            "authority_file_count": len(files),
            "authority_files": files,
        }
    return {
        "contract": "preexisting_campaign_authority_read_only_sha256_v1",
        "captured_at_epoch_s": time.time(),
        "roots": roots,
    }


def update_fresh_config(path: Path, *, algorithm: str) -> str:
    config = copy.deepcopy(read_json(path))
    shared = config.get("algorithm_c")
    if not isinstance(shared, dict):
        raise RuntimeError(f"shared outer config missing: {path}")
    shared["accepted_start"] = 0
    shared["accepted_target"] = 7
    no_improvement_rounds = 2
    # With convergence after two consecutive misses, the longest sequence that
    # can still reach seven accepted rounds alternates miss/accept and needs
    # 2 * accepted_target - 1 outer rounds.  A limit equal to the accepted
    # target incorrectly makes the first rejected round fatal to completion.
    outer_round_limit = 2 * int(shared["accepted_target"]) - 1
    shared["outer_round_limit"] = outer_round_limit
    shared["max_rounds"] = max(
        outer_round_limit,
        int(shared.get("max_rounds") or outer_round_limit),
    )
    shared["no_improvement_convergence_rounds"] = no_improvement_rounds
    shared["outer_qps_progress_contract"] = QPS_CONTRACT
    shared["local_candidate_replay_policy"] = {
        "contract": LOCAL_TOP10_CONTRACT,
        "rank_key": "candidate_score_descending",
        "full_plan_deduplicated": True,
        "physical_batch_limit": 5,
        "physical_total_limit": 10,
        "stop_on_first_accepted_batch": True,
    }
    shared["current_interval_coverage_policy"] = {
        "contract": COVERAGE_CONTRACT,
        "offline_design_point_count": 256,
        "parameter_domain": "current_formal_interval",
        "default_ranges_reopened": False,
        "rank_key": "candidate_score_descending",
        "full_plan_deduplicated": True,
        "physical_batch_limit": 5,
        "physical_total_limit": 10,
        "trigger": "local_scored_top10_completed_without_accept",
        "external_algorithm_evidence_used": False,
    }
    shared["incumbent_convergence_policy"] = {
        "contract": CONVERGENCE_CONTRACT,
        "consecutive_no_improvement_rounds": 2,
        "incumbent_selection_counts_as_accepted_improvement": False,
        "rejected_round_updates_incumbent": False,
        "rejected_round_updates_ranges": False,
        "rejected_round_updates_gradient": False,
        "technical_failure_counts_as_no_improvement": False,
    }
    shared["outer_round_budget_policy"] = {
        "contract": "accepted_target_with_two_miss_convergence_budget_v1",
        "accepted_target": int(shared["accepted_target"]),
        "consecutive_no_improvement_rounds": no_improvement_rounds,
        "outer_round_limit": outer_round_limit,
        "derivation": "2 * accepted_target - 1",
    }
    shared["runtime_space_safety_policy"] = {
        "contract": "algorithm_c_tmp_space_preflight_v1",
        "path": "/tmp",
        "minimum_available_bytes": 4 * 1024 * 1024 * 1024,
        "checked_before_every_cohort_marker_and_while_waiting": True,
        "cohort_receipt_write": "atomic_fsync_replace",
        "corrupt_or_partial_receipts_counted": False,
    }
    workload_contract = shared.get("workload_contract")
    if not isinstance(workload_contract, dict):
        raise RuntimeError(f"workload contract missing: {path}")
    workload_contract["formal_outer_search"] = {
        "qps_contract": QPS_CONTRACT,
        "three_tier_same_batch": True,
        "local_top10_contract": LOCAL_TOP10_CONTRACT,
        "current_interval_coverage_contract": COVERAGE_CONTRACT,
        "convergence_contract": CONVERGENCE_CONTRACT,
    }
    if algorithm == "B":
        algorithm_b = config.get("algorithm_b")
        if not isinstance(algorithm_b, dict):
            raise RuntimeError(f"Algorithm B config missing: {path}")
        algorithm_b["outer_qps_progress_contract"] = QPS_CONTRACT
        algorithm_b["shared_outer_top10_policy"] = True
    atomic_json(path, config)
    return sha256(path)


def seal_campaign(
    prepared: Mapping[str, Any],
    *,
    algorithm: str,
    history_hashes: Mapping[str, Any],
) -> Dict[str, Any]:
    destination = Path(str(prepared["destination"]))
    manifest_path = Path(str(prepared["manifest"]))
    manifest = copy.deepcopy(read_json(manifest_path))
    for scene in SCENES:
        config_path = destination / scene / "optimizer_config.json"
        config_sha = update_fresh_config(config_path, algorithm=algorithm)
        manifest["scenes"][scene]["new_config_sha256"] = config_sha
    controller = C_CONTROLLER if algorithm == "C" else B_CONTROLLER
    manifest.update(
        {
            "contract": "pg_algorithm_bc_monotonic_qps_top10_fresh_campaign_v1",
            "algorithm": algorithm,
            "qps_progress_contract": QPS_CONTRACT,
            "three_tier_acceptance": (
                "same_batch_strict_then_secondary_then_fallback"
            ),
            "local_candidate_contract": LOCAL_TOP10_CONTRACT,
            "current_interval_coverage_contract": COVERAGE_CONTRACT,
            "incumbent_convergence_contract": CONVERGENCE_CONTRACT,
            "runtime_space_safety_contract": (
                "algorithm_c_tmp_space_preflight_v1"
            ),
            "accepted_improvement_requires_qps_above_last_accepted": True,
            "incumbent_convergence_counts_as_accepted_improvement": False,
            "algorithm_controller": str(controller),
            "algorithm_controller_sha256": sha256(controller),
            "shared_outer_controller": str(C_CONTROLLER),
            "shared_outer_controller_sha256": sha256(C_CONTROLLER),
            "support_supervisor": str(SUPERVISOR),
            "support_supervisor_sha256": sha256(SUPERVISOR),
            "formal_test": str(FORMAL_TEST),
            "formal_test_sha256": sha256(FORMAL_TEST),
            "algorithm_b_test": str(B_TEST),
            "algorithm_b_test_sha256": sha256(B_TEST),
            "preexisting_campaign_authority": copy.deepcopy(history_hashes),
            "preexisting_campaigns_read_only": True,
            "sealed_at_epoch_s": time.time(),
        }
    )
    atomic_json(manifest_path, manifest)
    return {
        "algorithm": algorithm,
        "destination": str(destination),
        "manifest": str(manifest_path),
        "manifest_sha256": sha256(manifest_path),
        "controller_sha256": sha256(controller),
    }


def main() -> int:
    for destination in (C_DESTINATION, B_DESTINATION):
        if destination.exists():
            raise RuntimeError(f"refusing to overwrite campaign: {destination}")
    history_hashes = protected_hashes()
    prepared_c = prepare_one(C_SOURCE, C_DESTINATION, algorithm="C")
    prepared_b = prepare_one(B_SOURCE, B_DESTINATION, algorithm="B")
    results = [
        seal_campaign(
            prepared_c,
            algorithm="C",
            history_hashes=history_hashes,
        ),
        seal_campaign(
            prepared_b,
            algorithm="B",
            history_hashes=history_hashes,
        ),
    ]
    print(json.dumps(results, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
