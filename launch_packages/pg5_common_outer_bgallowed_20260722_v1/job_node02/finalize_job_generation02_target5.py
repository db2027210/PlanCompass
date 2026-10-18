#!/usr/bin/env python3
"""Bind JOB generation_02 v5 to the accepted-checkpoint gate repair."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any


OPTIMIZER_SHA256 = "f437673481a728b05dbe5ae92aa91eace2f1836c9fafa2df7641d260b7cb9989"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_new_json(path: Path, value: Any) -> None:
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
    parser.add_argument("--generation-root", type=Path, required=True)
    parser.add_argument("--optimizer", type=Path, required=True)
    args = parser.parse_args()
    generation = args.generation_root.resolve()
    optimizer = args.optimizer.resolve()
    if sha256(optimizer) != OPTIMIZER_SHA256:
        raise RuntimeError("target-5 optimizer SHA mismatch")

    manifest_v1_path = generation / "generation_02_manifest_v4_final.json"
    config_v1_path = generation / "config_v4_final/memory/job.json"
    checkpoint_path = generation / "generation_02_accepted_resume_checkpoint_v1.json"
    manifest_v1 = json.loads(manifest_v1_path.read_text(encoding="utf-8"))
    config = json.loads(config_v1_path.read_text(encoding="utf-8"))
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    if manifest_v1.get("status") != (
        "sealed_ready_v4_final_shared_sha_shrunken_scope_only"
    ):
        raise RuntimeError("generation_02 v4 manifest is not sealed")
    if checkpoint.get("counters") != {
        "global_replay_count": 48,
        "formal_search_replay_count": 6,
        "accepted_shrink_count": 1,
        "startup_baseline_count": 1,
    }:
        raise RuntimeError("generation_02 checkpoint counters drifted")
    attempt = Path(str(manifest_v1["attempt_root"])).resolve()
    live_counters = {
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
    if live_counters != checkpoint["counters"]:
        raise RuntimeError("live counters changed during aborted generation_02 run")
    if (attempt / "formal_points/point_0049").exists():
        raise RuntimeError("p49 exists; run_01 cannot be classified pre-point")

    config = copy.deepcopy(config)
    generation_meta = config["_tsgd_generation"]
    generation_meta["parameter_optimizer_sha256"] = OPTIMIZER_SHA256
    generation_meta["deployed_core_sha256"]["parameter_optimizer.py"] = (
        OPTIMIZER_SHA256
    )
    generation_meta["formal_measurement_v5"]["parameter_optimizer_sha256"] = (
        OPTIMIZER_SHA256
    )
    generation_meta["campaign_stability_contract_v5"][
        "validated_by_parameter_optimizer_sha256"
    ] = OPTIMIZER_SHA256
    generation_meta["accepted_target"] = 5
    generation_meta["accepted_remaining_at_start"] = 4
    generation_meta["normalized_width_stop"] = 1.0 / 32.0
    outer = config["formal_outer_protocol"]
    outer["accepted_shrink_target"] = 5
    outer["formal_search_accepted_target"] = 5
    outer["normalized_width_stop"] = 1.0 / 32.0
    outer["target5_wall_clock_goal_hours"] = 3.0
    outer["target5_optimizer_sha256"] = OPTIMIZER_SHA256
    if (
        outer.get("formal_search_accepted_count_start") != 1
        or outer.get("formal_search_replay_count_start") != 6
        or outer.get("generation_02_rerun_all_127_subsets") is not True
    ):
        raise RuntimeError("generation_02 config lost inherited state")
    run_root = generation / "run_04"
    config["logging"]["history_path"] = str(run_root / "optimizer_history.jsonl")
    config["plan_capture"]["output_dir"] = str(run_root / "plan_captures")
    config["resource_iteration_reports"]["output_dir"] = str(
        run_root / "resource_reports"
    )
    config["auto_explain"]["output_dir"] = str(run_root / "auto_explain")
    generation_meta["generation_restart_id"] = "run_04_shrunken_scope_only"
    generation_meta["run_03_role"] = "accepted_checkpoint_noise_gate_conflict_audit_only"
    config_path = generation / "config_v5_accepted_gate/memory/job.json"
    write_new_json(config_path, config)

    manifest_v2 = copy.deepcopy(manifest_v1)
    manifest_v2.update(
        {
            "schema_version": 5,
            "status": "sealed_ready_v5_accepted_gate_shrunken_scope_only",
            "supersedes_manifest_path": str(manifest_v1_path),
            "supersedes_manifest_sha256": sha256(manifest_v1_path),
            "config_path": str(config_path),
            "config_sha256": sha256(config_path),
            "parameter_optimizer_sha256": OPTIMIZER_SHA256,
            "accepted_target": 5,
            "accepted_at_start": 1,
            "accepted_remaining": 4,
            "normalized_width_stop": 1.0 / 32.0,
            "wall_clock_goal_hours": 3.0,
            "run_root": str(run_root),
            "round0_skip_contract": (
                "accepted_checkpoint_skips_preacceptance_round0_then_reconstructs_"
                "accepted_shrink_before_round1_127"
            ),
        }
    )
    manifest_v2["accepted_state"]["accepted_target"] = 5
    manifest_v2["accepted_state"]["normalized_width_stop"] = 1.0 / 32.0
    manifest_v2["candidate_generation"]["subset_count"] = 127
    manifest_v2["candidate_generation"]["rerun_required"] = True
    aborted_history = generation / "run_03/optimizer_history_2026-07-23_043657.jsonl"
    aborted_events = [
        json.loads(line)
        for line in aborted_history.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not any(
        event.get("event") == "final_feedback_initial_validation_rejected"
        and event.get("reason") == "hard_gate_failed"
        for event in aborted_events
    ):
        raise RuntimeError("run_03 lacks the accepted-checkpoint noise-gate conflict")
    aborted_audit = {
        "schema_version": 1,
        "event": "job_generation_02_run_03_accepted_gate_conflict_audit",
        "status": "aborted_before_formal_point_after_accepted_gate_conflict",
        "history_path": str(aborted_history),
        "history_sha256": sha256(aborted_history),
        "accepted_checkpoint_round0_skipped": True,
        "accepted_checkpoint_rejected_by_noise_gate": True,
        "optimizer_started": True,
        "formal_point_started": False,
        "counters_after_abort": checkpoint["counters"],
        "counts_as_candidate_generation": False,
        "counts_as_convergence": False,
        "replacement_run_root": str(run_root),
    }
    aborted_audit_path = generation / "run_03_accepted_gate_conflict_audit_v1.json"
    write_new_json(aborted_audit_path, aborted_audit)
    manifest_v2["run_03_accepted_gate_audit_path"] = str(aborted_audit_path)
    manifest_v2["run_03_accepted_gate_audit_sha256"] = sha256(aborted_audit_path)
    manifest_path = generation / "generation_02_manifest_v5_accepted_gate.json"
    write_new_json(manifest_path, manifest_v2)
    print(
        json.dumps(
            {
                "manifest_path": str(manifest_path),
                "manifest_sha256": sha256(manifest_path),
                "config_path": str(config_path),
                "config_sha256": sha256(config_path),
                "optimizer_sha256": OPTIMIZER_SHA256,
                "accepted": "1/5",
                "normalized_width_stop": 1.0 / 32.0,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
