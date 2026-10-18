#!/usr/bin/env python3
"""Prepare an isolated JOB T-SGD generation without importing grid counters."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any


CORE_SHA256 = {
    "gradient_descent_optimizer.py": "687006473e87974d6b91a7b26308539c33554fafc715265699a2e61140f42914",
    "experience_table_subset_optimizer.py": "f147e0d874f47643c9613c4a9f67e1fbfd1791c1485b357b535587ca77fad0cf",
    "parameter_optimizer.py": "d386c1b3b8cf41b2e864cbf7536a8ef90b2f184116a21a11def69671b94eda36",
    "common_outer_loop.py": "61a8b15483f486229f7463df5b1caa68de3211a93794fede7d9c26886d5e7631",
    "resource_utility.py": "cf823efe25d13c90a69cb91deff7b0e2ad805d594ab5bc40d3ed18241d3fd8a2",
    "formal_contracts.py": "85822a22364e1eb98498f680bc397c953b0e3337befd9cbad0d37b5bb49d5c91",
}
STABILITY_V4_SHA256 = "ba2810b36fd1caf36b8f6679e380e5e97f13ff5d357d3227fbfd7d0219393853"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_text_new(path: Path, value: str) -> None:
    if path.exists() or path.is_symlink():
        raise RuntimeError(f"refusing to overwrite sealed output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        handle.write(value)
        handle.flush()
        os.fsync(handle.fileno())


def write_json_new(path: Path, value: Any) -> None:
    write_text_new(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-config", type=Path, required=True)
    parser.add_argument("--legacy-attempt", type=Path, required=True)
    parser.add_argument("--new-attempt", type=Path, required=True)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--config-output", type=Path, required=True)
    args = parser.parse_args()

    source_config = args.source_config.resolve()
    legacy = args.legacy_attempt.resolve()
    new = args.new_attempt.resolve()
    workspace = args.workspace.resolve()
    config_output = args.config_output.resolve()
    if new.exists() or new.is_symlink():
        raise RuntimeError(f"new T-SGD attempt already exists: {new}")
    for name, expected in CORE_SHA256.items():
        actual = sha256(workspace / name)
        if actual != expected:
            raise RuntimeError(f"deployed core SHA mismatch: {name}: {actual} != {expected}")
    stability_path = (
        workspace
        / "config/formal/pg5_common_outer_bgallowed_20260722_v1/stability_gate_contract_v4.json"
    )
    if sha256(stability_path) != STABILITY_V4_SHA256:
        raise RuntimeError("rolling stability v4 SHA mismatch")

    expected_legacy_counters = {
        "replay_counter.txt": 42,
        "formal_search_replay_counter.txt": 11,
        "formal_search_accepted_counter.txt": 1,
        "formal_startup_baseline_counter.txt": 1,
    }
    for name, expected in expected_legacy_counters.items():
        actual = int((legacy / name).read_text(encoding="utf-8").strip())
        if actual != expected:
            raise RuntimeError(f"legacy counter drift: {name}: {actual} != {expected}")
    if (legacy / "formal_points/point_0043").exists():
        raise RuntimeError("legacy p43 unexpectedly exists")

    config = json.loads(source_config.read_text(encoding="utf-8"))
    protocol = config.setdefault("formal_outer_protocol", {})
    for key in (
        "formal_search_resume_checkpoint_path",
        "formal_search_resume_checkpoint_sha256",
        "resume_next_feedback_round",
        "resume_replays_are_evidence_only_no_rerun",
        "plan_fingerprint_artifact_path",
        "plan_fingerprint_artifact_sha256",
        "plan_cluster_basis",
    ):
        protocol.pop(key, None)
    protocol.update(
        {
            "method_id": "experience_table_all_subset_tsgd_v1",
            "algorithm_generation": "job_tsgd_fresh_v1",
            "all_nonempty_subset_count": 127,
            "global_replay_ordinal_at_binding": 42,
            "global_replay_counter_path": str(new / "replay_counter.txt"),
            "formal_search_replay_count_start": 0,
            "formal_search_replay_counter_path": str(
                new / "formal_search_replay_counter.txt"
            ),
            "formal_search_accepted_count_start": 0,
            "formal_search_accepted_counter_path": str(
                new / "formal_search_accepted_counter.txt"
            ),
            "formal_startup_baseline_count_start": 1,
            "formal_startup_baseline_counter_path": str(
                new / "formal_startup_baseline_counter.txt"
            ),
            "formal_search_initial_replay_top_k": 5,
            "formal_search_second_replay_top_k": 10,
            "formal_search_max_replay_top_k": 16,
            "formal_search_accepted_target": 10,
            "accepted_shrink_target": 10,
            "initial_real_replay_count": 0,
            "old_grid_points_count_as_new_acceptance": False,
            "old_grid_points_count_as_new_shrink": False,
            "new_candidate_rank_origin": 1,
        }
    )
    config["stability_gate_contract"] = {
        "path": str(stability_path),
        "sha256": STABILITY_V4_SHA256,
    }
    config.setdefault("logging", {})["history_path"] = str(
        new / "optimizer_history.jsonl"
    )
    config.setdefault("auto_explain", {})["output_dir"] = str(
        new / "generated_output/job_tsgd_fresh_v1/memory/job_auto_explain"
    )
    config.setdefault("plan_capture", {})["output_dir"] = str(new / "plan_captures")
    config.setdefault("resource_iteration_reports", {})["output_dir"] = str(
        new / "resource_reports"
    )
    retraining = (
        config.setdefault("metrics", {})
        .setdefault("plan_estimator", {})
        .setdefault("model_retraining", {})
    )
    retraining["output_dir"] = str(
        new / "generated_config/job_tsgd_fresh_v1/memory/job_resource_models"
    )
    retraining["run_id"] = "job_tsgd_fresh_v1_memory_models"
    config["_tsgd_generation"] = {
        "generation_id": "job_tsgd_fresh_v1",
        "source_config_path": str(source_config),
        "source_config_sha256": sha256(source_config),
        "legacy_attempt_path": str(legacy),
        "legacy_grid_evidence_role": "audit_only",
        "legacy_grid_acceptance_or_shrink_imported": False,
        "new_acceptance_count_start": 0,
        "new_shrink_count_start": 0,
        "new_rank_origin": 1,
        "deployed_core_sha256": copy.deepcopy(CORE_SHA256),
        "exact_plan_cluster_required_before_real_replay": True,
        "candidate_replay_release_state": "paused_before_first_claim",
    }

    new.mkdir(parents=True, exist_ok=False)
    for name, value in {
        "replay_counter.txt": 42,
        "formal_search_replay_counter.txt": 0,
        "formal_search_accepted_counter.txt": 0,
        "formal_startup_baseline_counter.txt": 1,
    }.items():
        write_text_new(new / name, f"{value}\n")

    old_pointer = legacy / "fresh_restart_r0/latest.json"
    pointer = json.loads(old_pointer.read_text(encoding="utf-8"))
    write_json_new(new / "fresh_restart_r0/latest.json", pointer)

    legacy_artifacts = {}
    for label, path in {
        "p31_r0": legacy / "formal_points/point_0031/point_result.json",
        "p40_grid_accept": legacy / "formal_points/point_0040/point_result.json",
        "p42_grid_rank1": legacy / "formal_points/point_0042/point_result.json",
        "p40_grid_checkpoint": legacy / "accepted_resume_checkpoint_p40.json",
        "p42_grid_checkpoint": legacy / "partial_resume_checkpoint_after_p42_exact_plan_v2.json",
        "grid_plan_ranking": legacy / "round1_plan_fingerprints_transition_v1/round1_ranking_with_plan_fingerprints.json",
        "algorithm_mismatch_seal": legacy / "job_p42_resume_blocked_algorithm_mismatch.json",
    }.items():
        if not path.is_file():
            raise RuntimeError(f"legacy audit artifact missing: {label}: {path}")
        legacy_artifacts[label] = {"path": str(path), "sha256": sha256(path)}
    write_json_new(
        new / "legacy_grid_evidence_audit_only.json",
        {
            "schema_version": 1,
            "status": "sealed_audit_only",
            "algorithm_generation": "pre_tsgd_grid",
            "counts_toward_job_tsgd_fresh_v1_acceptance": False,
            "counts_toward_job_tsgd_fresh_v1_shrink": False,
            "counts_toward_job_tsgd_fresh_v1_candidate_rank": False,
            "legacy_counters": expected_legacy_counters,
            "artifacts": legacy_artifacts,
        },
    )
    write_json_new(
        new / "pause_before_next_formal_claim.json",
        {
            "schema_version": 1,
            "pause": True,
            "global_ordinal_after_completed_point": 42,
            "reason": (
                "Generate 127 fresh T-SGD subset candidates, calibrate resource error "
                "boxes, and seal exact plan-cluster representatives before rank-1 claim."
            ),
            "algorithm_generation": "job_tsgd_fresh_v1",
            "formal_search_replay_count": 0,
            "accepted_shrink_count": 0,
        },
    )
    write_json_new(config_output, config)
    write_json_new(
        new / "tsgd_generation_preflight_seal.json",
        {
            "schema_version": 1,
            "status": "paused_generation_ready",
            "algorithm_generation": "job_tsgd_fresh_v1",
            "config_path": str(config_output),
            "config_sha256": sha256(config_output),
            "core_sha256": CORE_SHA256,
            "stability_gate_v4_sha256": STABILITY_V4_SHA256,
            "counter_binding": {
                "global": 42,
                "new_formal_search": 0,
                "new_accepted_shrink": 0,
                "startup_baseline_reuse": 1,
            },
            "replay_schedule": [5, 10, 16],
            "accepted_shrink_target": 10,
            "exact_plan_cluster_required_before_release": True,
        },
    )
    print(
        json.dumps(
            {
                "config_path": str(config_output),
                "config_sha256": sha256(config_output),
                "new_attempt": str(new),
                "status": "paused_generation_ready",
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
