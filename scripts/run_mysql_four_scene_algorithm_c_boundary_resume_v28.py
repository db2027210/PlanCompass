#!/usr/bin/env python3
"""Resume Outer 17 Rank 3 after two excluded timeout waves.

This release also treats an exact, stably stopped owned XP container as proof
that no mysqld can retain client sessions.  The stopped predecessor remains
discard-only and is rebuilt from the frozen template before measurement.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import time
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v27 as v27


runtime = v27.runtime
ORIGINAL_CLASSIFIER = runtime.alg.classify_xp_mysql_liveness
ORIGINAL_V27_VALIDATOR = v27.validate_formal_partial_continuation
ORIGINAL_V27_STATE_DOCUMENT = v27.state_document


def classify_xp_mysql_liveness(worker: Mapping[str, Any]) -> dict[str, Any]:
    result = ORIGINAL_CLASSIFIER(worker)
    if result.get("status") != "IDENTITY_MISMATCH":
        return result
    worker_id = str(worker.get("worker_id") or "")
    container = str(worker.get("container") or "")
    expected_container = runtime.alg.reset.XP_CONTAINER_ALLOWLIST.get(worker_id)
    expected_id = str(worker.get("container_id") or "")
    if (
        not expected_container
        or container != expected_container
        or "0708" in container
        or len(expected_id) != 64
    ):
        return result
    try:
        first = runtime.alg.reset._docker_json("inspect", container)
        time.sleep(0.25)
        second = runtime.alg.reset._docker_json("inspect", container)
    except Exception:
        return result

    def stopped_exact(document: Mapping[str, Any]) -> bool:
        state = document.get("State") or {}
        labels = (document.get("Config") or {}).get("Labels") or {}
        return bool(
            document.get("Id") == expected_id
            and labels.get("ra.owner") == "mysql_four_scene_v5"
            and labels.get("ra.worker_id") == worker_id
            and state.get("Running") is False
            and state.get("Paused") is False
            and state.get("Restarting") is False
            and int(state.get("Pid") or 0) == 0
            and state.get("Status") in {"created", "exited", "dead"}
        )

    if not (stopped_exact(first) and stopped_exact(second)):
        return result
    first_state = first.get("State") or {}
    second_state = second.get("State") or {}
    return {
        "worker_id": worker_id,
        "expected_container": expected_container,
        "observed_container": container,
        "expected_container_id": expected_id,
        "status": "PROVEN_NO_LIVE_MYSQLD",
        "first_container_id": first.get("Id"),
        "second_container_id": second.get("Id"),
        "docker_running": False,
        "container_identity_stable": True,
        "container_status": second_state.get("Status"),
        "container_exit_code": second_state.get("ExitCode"),
        "container_pid": 0,
        "mysqld_rows": [],
        "live_mysqld_rows": [],
        "safe_to_discard_unresponsive_predecessor": True,
        "session_count_semantics": "zero_by_exact_stably_stopped_container",
        "stopped_predecessor_discard_only": True,
        "first_finished_at": first_state.get("FinishedAt"),
        "second_finished_at": second_state.get("FinishedAt"),
    }


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def _validate_timeout_batch(
    *, path: Path, rank: int, active: set[str], selected: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    batch = runtime.alg.load(path)
    measurements = _mapping(batch.get("measurements"), f"rank {rank} measurements")
    timeout_tasks = {
        str(row.get("task")) for row in (batch.get("failures") or [])
        if str(row.get("error") or "").startswith("TimeoutExpired:")
        and "timed out after 900 seconds" in str(row.get("error") or "")
    }
    skipped = {
        str(row.get("task")) for row in (batch.get("failures") or [])
        if row.get("error") == "skipped_after_invalid_workload"
    }
    if (
        batch.get("status") != "FAILED_CLOSED"
        or set(measurements) != {"job_w1", "tpch_w1", "xp_cpu_w1", "xp_io_w1"}
        or {str(row.get("scene")) for row in measurements.values()} != set(runtime.SCENES)
        or not {"pair_1", "xp_cpu_w1", "xp_io_w1"}.issubset(timeout_tasks)
        or skipped != {f"{worker}_plan_capture" for worker in measurements}
        or not 900 <= float(batch.get("elapsed_seconds") or 0) <= 912
        or any(row.get("hard_gate_passed") is not False for row in measurements.values())
        or any(row.get("coverage_complete") is not False for row in measurements.values())
    ):
        raise RuntimeError(f"technical timeout rank {rank} batch differs")
    for row in measurements.values():
        scene = str(row["scene"])
        expected_rank = rank if scene in active else 0
        if int(row.get("rank") or 0) != expected_rank:
            raise RuntimeError(f"technical timeout {scene} rank differs")
        if scene in active:
            params = selected[scene][rank - 1]["optimized_params"]
            if runtime.alg.optimizer_params_key(row["params"]) != runtime.alg.optimizer_params_key(params):
                raise RuntimeError(f"technical timeout {scene} params differ")
    binding = {
        "rank": rank,
        "path": str(path),
        "sha256": runtime.alg.sha(path),
        "technical_timeout_excluded": True,
    }
    exclusion = {
        "classification": "TECHNICAL_TIMEOUT_EXCLUDED",
        "batch_index": rank,
        "candidate_ranks": [rank],
        "timed_out_tasks": sorted(timeout_tasks),
        "derived_plan_capture_skips": sorted(skipped),
        "batch_result_path": str(path),
        "batch_result_sha256": runtime.alg.sha(path),
        "candidate_state_mutation": False,
        "accepted_state_credit_increment": 0,
        "rejected_outer_attempt_increment": 0,
        "convergence_mutation": False,
        "replay_history_mutation": False,
        "pruning_evidence_eligible": False,
        "acceptance_tier_mutation": False,
    }
    return binding, exclusion


def _rank3_recovery(
    *, source_root: Path, checkpoint: Mapping[str, Any],
    reconstructed: Mapping[str, Any], start_rank: int,
    completed_rank5_root: Path | None = None,
) -> dict[str, Any] | None:
    source_root = Path(source_root).resolve()
    if start_rank != 3 or completed_rank5_root is not None:
        return None
    state_path = source_root / "run_state.json"
    state = runtime.alg.load(state_path)
    expected_outer = int(reconstructed.get("outer_attempt") or 0) + 1
    outer_root = source_root / f"outer_{expected_outer:03d}"
    error = str(state.get("error") or "")
    state_fields = (
        "accepted_rounds", "rejected_outer_attempts", "acceptance_tier",
        "consecutive_failed_rounds", "interval_stable_rounds",
        "stable_intervals", "converged", "full_plan_replay_history_v2",
        "post_target_stability_confirmation_counts",
    )
    reset3 = source_root / "xp_resets" / f"outer_{expected_outer:03d}_batch_3"
    if (
        state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or not error.startswith(
            "RuntimeError: XP active client sessions were not proven drained before the 60s bound"
        )
        or state.get("formal_scope") != "mysql_only"
        or state.get("cross_engine_comparison_prohibited") is not True
        or state.get("formal_evidence_eligible") is not True
        or int(state.get("outer_attempt") or -1) != int(reconstructed.get("outer_attempt") or -2)
        or any(state.get(field) != reconstructed.get(field) for field in state_fields)
        or list(outer_root.glob("*.decision.json"))
        or (outer_root / "top5_batch_3/batch_result.json").exists()
        or (reset3.exists() and any(reset3.rglob("*")))
    ):
        return None

    ranking_path = outer_root / "predicted_rankings.json"
    ranking = runtime.alg.load(ranking_path)
    active = set(ranking.get("active_decision_scenes") or [])
    expected_active = {
        scene for scene in runtime.SCENES if not reconstructed["converged"][scene]
    }
    selected = _mapping(ranking.get("selected_top_k"), "selected Top-5")
    if (
        ranking.get("schema_version")
        != "mysql_four_scene_algorithm_c_rankings_boundary_resume_v2"
        or int(ranking.get("outer_attempt") or 0) != expected_outer
        or ranking.get("fixed_top_k") != 5
        or active != expected_active
        or any(len(selected.get(scene) or []) != 5 for scene in active)
    ):
        raise RuntimeError("rank-3 recovery ranking differs")

    inherited = _mapping(
        state.get("formal_partial_continuation"), "inherited continuation",
    )
    inherited_rows = inherited.get("completed_batches") or []
    if len(inherited_rows) != 1 or int(inherited_rows[0].get("rank") or 0) != 1:
        raise RuntimeError("inherited technical rank-1 binding differs")
    rank1_path = Path(str(inherited_rows[0].get("path") or "")).resolve()
    rank1, exclusion1 = _validate_timeout_batch(
        path=rank1_path, rank=1, active=active, selected=selected,
    )
    if (
        inherited_rows[0].get("sha256") != runtime.alg.sha(rank1_path)
        or inherited_rows[0].get("technical_timeout_excluded") is not True
    ):
        raise RuntimeError("inherited technical rank-1 hash differs")
    rank2_path = outer_root / "top5_batch_2/batch_result.json"
    rank2, exclusion2 = _validate_timeout_batch(
        path=rank2_path, rank=2, active=active, selected=selected,
    )

    baseline_binding = _mapping(
        state.get("formal_incumbent_baseline"), "formal incumbent baseline",
    )
    baseline_path = Path(str(baseline_binding.get("path") or "")).resolve()
    baseline = runtime.alg.load(baseline_path)
    if (
        baseline_binding.get("sha256") != runtime.alg.sha(baseline_path)
        or baseline.get("status") != "PASS"
        or baseline.get("failures") not in ([], None)
    ):
        raise RuntimeError("rank-3 recovery baseline differs")

    gate_path = outer_root / "top5_batch_3_cross_project_wave_quiet_gate_attempt_001.json"
    gate = runtime.alg.load(gate_path)
    if gate.get("status") != "PASS" or gate.get("errors") not in ([], None):
        raise RuntimeError("rank-3 recovery quiet gate differs")
    watchdog_path = source_root / "runtime_external_interference_watchdog.jsonl"
    watchdog = [
        json.loads(line) for line in watchdog_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    interference = {
        "runtime_interference_detected_under_exclusive_wave_lease",
        "cross_project_workload_lease_lost_during_formal_wave",
        "cross_project_workload_lease_deadline_exceeded",
    }
    if (
        not watchdog
        or watchdog[0].get("event") != "watchdog_started"
        or watchdog[-1].get("event") != "formal_unit_terminal"
        or watchdog[-1].get("interference_detected") is not False
        or any(row.get("event") in interference for row in watchdog)
    ):
        raise RuntimeError("rank-3 recovery non-interference evidence differs")

    return {
        "source_root": str(source_root),
        "source_state": {"path": str(state_path), "sha256": runtime.alg.sha(state_path)},
        "baseline": {"path": str(baseline_path), "sha256": runtime.alg.sha(baseline_path)},
        "predicted_rankings": {"path": str(ranking_path), "sha256": runtime.alg.sha(ranking_path)},
        "blocked_gate": {"path": str(gate_path), "sha256": runtime.alg.sha(gate_path)},
        "continuation_reason": "two_technical_timeouts_then_stopped_container_liveness_recovery",
        "runtime_interruption_watchdog": {"path": str(watchdog_path), "sha256": runtime.alg.sha(watchdog_path)},
        "completed_batches": [rank1, rank2],
        "start_rank": 3,
        "outer_attempt": expected_outer,
        "ranking_document": ranking,
        "evaluations": {scene: [] for scene in active},
        "technical_batch_exclusions": [exclusion1, exclusion2],
        "candidate_state_mutation_before_continuation": False,
        "accepted_state_credit_before_continuation": 0,
        "completed_rank_release_race": False,
        "completed_rank5_recovery": None,
        "post_drain_liveness_recovery": {
            "failed_reset_root": str(reset3),
            "exact_stopped_container_proof_supported": True,
            "fresh_reset_required": True,
        },
        "companion_aware_partial_inheritance": {
            "release_version": "v28",
            "active_decision_scenes": sorted(active),
            "companion_scenes": sorted(set(runtime.SCENES) - active),
            "completed_full_batch_bindings_unchanged": True,
            "companion_rows_used_for_decision_evaluations": False,
        },
    }


def validate_formal_partial_continuation(**kwargs: Any) -> dict[str, Any]:
    recovered = _rank3_recovery(**kwargs)
    if recovered is not None:
        return recovered
    return ORIGINAL_V27_VALIDATOR(**kwargs)


def state_document(**kwargs: Any) -> dict[str, Any]:
    document = ORIGINAL_V27_STATE_DOCUMENT(**kwargs)
    document["stopped_xp_liveness_release"] = {
        "release_version": "v28",
        "source_path": str(Path(__file__).resolve()),
        "source_sha256": runtime.alg.sha(Path(__file__).resolve()),
        "exact_owned_stably_stopped_container_proves_no_live_mysqld": True,
        "stopped_predecessor_discard_only": True,
        "workload_scoring_top5_binary_shrink_and_convergence_changed": False,
    }
    return document


def main() -> int:
    runtime.alg.classify_xp_mysql_liveness = classify_xp_mysql_liveness
    v27.validate_formal_partial_continuation = validate_formal_partial_continuation
    v27.state_document = state_document
    return v27.main()


if __name__ == "__main__":
    raise SystemExit(main())
