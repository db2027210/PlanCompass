#!/usr/bin/env python3
"""Seal JOB generation_02 after explicit unseen-candidate exhaustion."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional


EXECUTED_OPTIMIZER_SHA256 = (
    "f437673481a728b05dbe5ae92aa91eace2f1836c9fafa2df7641d260b7cb9989"
)
MANIFEST_SHA256 = (
    "ef904657ca8b6ac65abd80ae8df5f60f02ca714ce595097012bbe7a12d76c8df"
)
CONFIG_SHA256 = (
    "8c876f1c48cbf89c9f7c303db2cd930fa25370c8a83f60ac9ebf3c28d67941ff"
)
CHECKPOINT_SHA256 = (
    "8aec72f5c5d76d9f407457fe8b1408ca91df7682eb4bff9c6a1dd25c0e9f1aa9"
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def write_new_json(path: Path, value: Any) -> None:
    require(not path.exists() and not path.is_symlink(), f"refusing overwrite: {path}")
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def event(
    events: List[Dict[str, Any]],
    name: str,
    *,
    round_index: Optional[int] = None,
) -> Dict[str, Any]:
    matches = [
        item
        for item in events
        if item.get("event") == name
        and (round_index is None or item.get("round_index") == round_index)
    ]
    require(len(matches) == 1, f"expected one {name} round={round_index}, got {len(matches)}")
    return matches[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--generation-root", type=Path, required=True)
    args = parser.parse_args()

    generation = args.generation_root.resolve()
    attempt = generation.parent.parent
    run = generation / "run_04"
    history_paths = list(run.glob("optimizer_history_*.jsonl"))
    require(len(history_paths) == 1, "run_04 must contain exactly one root optimizer history")
    history_path = history_paths[0]
    events = [
        json.loads(line)
        for line in history_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    manifest_path = generation / "generation_02_manifest_v5_accepted_gate.json"
    config_path = generation / "config_v5_accepted_gate/memory/job.json"
    checkpoint_path = generation / "generation_02_accepted_resume_checkpoint_v1.json"
    require(sha256(manifest_path) == MANIFEST_SHA256, "manifest SHA drift")
    require(sha256(config_path) == CONFIG_SHA256, "config SHA drift")
    require(sha256(checkpoint_path) == CHECKPOINT_SHA256, "checkpoint SHA drift")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(
        manifest.get("parameter_optimizer_sha256") == EXECUTED_OPTIMIZER_SHA256,
        "executed optimizer SHA mismatch",
    )

    restored = event(events, "formal_search_accepted_checkpoint_validation_restored")
    require(restored.get("accepted_shrink_count") == 1, "accepted checkpoint count drift")
    for round_index in (1, 2):
        ranking = event(events, "experience_table_all_subset_ranking", round_index=round_index)
        require(ranking.get("subset_count") == 127, f"round {round_index} subset count")
        require(ranking.get("successful_subset_count") == 127, f"round {round_index} success count")
        require(ranking.get("failed_subset_count") == 0, f"round {round_index} failures")
        require(ranking.get("unique_candidate_count") == 2, f"round {round_index} unique count")

    round1_plan = event(events, "experience_table_gradient_real_replay_plan", round_index=1)
    require(round1_plan.get("selected_ranks") == [1], "round 1 did not select rank 1")
    require(round1_plan.get("skipped_incumbent_ranks") == [2], "round 1 incumbent rank drift")
    rejection = event(events, "feedback_estimation_error", round_index=1)
    require(rejection.get("reason") == "db_qps_not_strictly_higher", "p49 rejection reason drift")

    round2_plan = event(events, "experience_table_gradient_real_replay_plan", round_index=2)
    require(round2_plan.get("candidate_space_exhausted") is True, "round 2 not exhausted")
    require(round2_plan.get("selected_count") == 0, "round 2 unexpectedly selected candidate")
    require(round2_plan.get("skipped_seen_ranks") == [1], "round 2 seen rank drift")
    require(round2_plan.get("skipped_incumbent_ranks") == [2], "round 2 incumbent rank drift")
    exhausted = event(events, "experience_table_gradient_candidate_space_exhausted", round_index=2)
    require(exhausted.get("completion_status") == "incomplete", "target status must be incomplete")
    require(exhausted.get("accepted_shrink_count") == 1, "final accepted count drift")

    point49_path = attempt / "formal_points/point_0049/point_result.json"
    point49 = json.loads(point49_path.read_text(encoding="utf-8"))
    measurement = point49.get("measurement") or {}
    stability = measurement.get("resource_stability") or {}
    require(point49.get("status") == "complete", "p49 not complete")
    require(measurement.get("correctness_passed") is True, "p49 correctness failed")
    require(stability.get("passed") is True, "p49 stability failed")
    require(measurement.get("effective_measurement_seconds") == 180, "p49 duration drift")
    require(not (attempt / "formal_points/point_0050").exists(), "unexpected p50 exists")

    counters = {
        "global_replay_count": int((attempt / "replay_counter.txt").read_text().strip()),
        "formal_search_replay_count": int(
            (attempt / "formal_search_replay_counter.txt").read_text().strip()
        ),
        "accepted_shrink_count": int(
            (attempt / "formal_search_accepted_counter.txt").read_text().strip()
        ),
        "startup_baseline_count": int(
            (attempt / "formal_startup_baseline_counter.txt").read_text().strip()
        ),
    }
    require(
        counters
        == {
            "global_replay_count": 49,
            "formal_search_replay_count": 7,
            "accepted_shrink_count": 1,
            "startup_baseline_count": 1,
        },
        "final counters drift",
    )
    exception = event(events, "optimizer_process_exception")
    require(
        exception.get("exception_type") == "KeyError"
        and exception.get("exception_message") == "'threshold_pct'",
        "unexpected post-convergence report exception",
    )

    final_validation = event(events, "feedback_final_validation", round_index=1)[
        "final_validation"
    ]
    p49_memory_cv = next(
        field["coefficient_of_variation"]
        for field in stability["cross_window_blocks"][0]["fields"]
        if field["field"] == "memory_utilization"
    )
    seal = {
        "schema_version": 1,
        "event": "job_generation_02_explicit_no_representative_convergence",
        "status": "sealed_incomplete_target_1_of_5_candidate_space_exhausted",
        "target_accepted_shrinks": 5,
        "accepted_shrinks": 1,
        "termination_contract_satisfied": (
            "generation_explicitly_has_no_unseen_nonincumbent_representative"
        ),
        "history_path": str(history_path),
        "history_sha256": sha256(history_path),
        "executed_parameter_optimizer_sha256": EXECUTED_OPTIMIZER_SHA256,
        "manifest": {"path": str(manifest_path), "sha256": sha256(manifest_path)},
        "config": {"path": str(config_path), "sha256": sha256(config_path)},
        "accepted_checkpoint": {
            "path": str(checkpoint_path),
            "sha256": sha256(checkpoint_path),
        },
        "rounds": {
            "round_1": {
                "subset_total": 127,
                "subset_success": 127,
                "subset_failed": 0,
                "unique_candidates": 2,
                "selected_ranks": [1],
                "skipped_incumbent_ranks": [2],
            },
            "round_2": {
                "subset_total": 127,
                "subset_success": 127,
                "subset_failed": 0,
                "unique_candidates": 2,
                "selected_count": 0,
                "skipped_seen_ranks": [1],
                "skipped_incumbent_ranks": [2],
                "candidate_space_exhausted": True,
            },
        },
        "p49": {
            "point_result_path": str(point49_path),
            "point_result_sha256": sha256(point49_path),
            "status": point49["status"],
            "correctness_passed": measurement["correctness_passed"],
            "effective_measurement_seconds": measurement["effective_measurement_seconds"],
            "stability_passed": stability["passed"],
            "db_qps": measurement["db_qps"],
            "incumbent_db_qps": 2.0724007281451637,
            "rejection_reason": rejection["reason"],
            "memory_utilization": measurement["resource_mean_utilization"]["memory"],
            "memory_working_set_bytes": final_validation["resource_stats"][
                "formal_pg_resource"
            ]["aggregate"]["memory_working_set_bytes"],
            "memory_cross_window_cv": p49_memory_cv,
            "oom_killed": point49["oom_killed"],
            "database_restart_count": point49["database_restart_count"],
        },
        "counters": counters,
        "p50_exists": False,
        "launcher_exit": json.loads((run / "launcher_exit.json").read_text()),
        "post_convergence_exception": {
            "event_index": events.index(exception) + 1,
            "type": exception["exception_type"],
            "message": exception["exception_message"],
            "classification": "final_report_only_after_explicit_exhaustion_event",
            "changes_convergence_result": False,
        },
    }
    seal_path = generation / "generation_02_convergence_seal_v1.json"
    write_new_json(seal_path, seal)
    print(
        json.dumps(
            {
                "seal_path": str(seal_path),
                "seal_sha256": sha256(seal_path),
                "status": seal["status"],
                "counters": counters,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
