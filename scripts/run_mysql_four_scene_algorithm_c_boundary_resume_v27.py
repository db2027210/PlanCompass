#!/usr/bin/env python3
"""Resume after XP crash timeout using refreshed post-drain liveness."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v19 as v19
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v20 as confirmation
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v22 as v22
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v26 as c26


runtime = c26.runtime
ORIGINAL_C26_VALIDATOR = c26.validate_formal_partial_continuation
ORIGINAL_DRAIN = v19.wait_for_xp_session_drain
ORIGINAL_V22_STATE_DOCUMENT = v22.state_document
RESET_ERROR_PREFIX = "CalledProcessError: Command '['mysql', "


def wait_for_xp_session_drain(
    workers: Mapping[str, Mapping[str, Any]], *, timeout_seconds: float = 60.0,
    poll_seconds: float = 1.0,
    liveness: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    result = ORIGINAL_DRAIN(
        workers, timeout_seconds=timeout_seconds, poll_seconds=poll_seconds,
        liveness=liveness,
    )
    refreshed = result.get("liveness") or {}
    if isinstance(liveness, dict):
        liveness.clear()
        liveness.update({key: dict(value) for key, value in refreshed.items()})
    return result


def state_document(**kwargs: Any) -> dict[str, Any]:
    document = ORIGINAL_V22_STATE_DOCUMENT(**kwargs)
    checkpoint_path = Path(str(document.get("checkpoint_path") or "")).resolve()
    checkpoint = runtime.alg.load(checkpoint_path)
    counts = (
        (checkpoint.get("reconstructed_state") or {}).get(
            "post_target_stability_confirmation_counts"
        ) or {}
    )
    accepted = document.get("accepted_rounds")
    if counts:
        if (
            not isinstance(accepted, confirmation.ConfirmationAcceptedRounds)
            or set(counts) != set(accepted)
        ):
            raise RuntimeError("checkpoint confirmation counter state differs")
        accepted.confirmation_acceptances = {
            scene: int(counts[scene]) for scene in accepted
        }
        confirmation.ACTIVE_ACCEPTED_ROUNDS = accepted
        document["post_target_stability_confirmation_counts"] = copy.deepcopy(
            accepted.confirmation_acceptances
        )
    document["post_drain_liveness_refresh_release"] = {
        "release_version": "v27",
        "source_path": str(Path(__file__).resolve()),
        "source_sha256": runtime.alg.sha(Path(__file__).resolve()),
        "reset_uses_final_drain_liveness": True,
        "dead_mysqld_discard_only": True,
        "workload_scoring_top5_binary_shrink_and_convergence_changed": False,
    }
    return document


def _technical_timeout_reset_failure_recovery(
    *, source_root: Path, checkpoint: Mapping[str, Any],
    reconstructed: Mapping[str, Any], start_rank: int,
    completed_rank5_root: Path | None = None,
) -> dict[str, Any] | None:
    source_root = Path(source_root).resolve()
    if start_rank != 2 or completed_rank5_root is not None:
        return None
    state_path = source_root / "run_state.json"
    state = runtime.alg.load(state_path)
    expected_outer = int(reconstructed.get("outer_attempt") or 0) + 1
    outer_root = source_root / f"outer_{expected_outer:03d}"
    reset_root = source_root / "xp_resets" / f"outer_{expected_outer:03d}_batch_2"
    drain_path = reset_root / "pre_reset_session_drain.json"
    batch_path = outer_root / "top5_batch_1/batch_result.json"
    gate_path = outer_root / "top5_batch_2_cross_project_wave_quiet_gate_attempt_001.json"
    ranking_path = outer_root / "predicted_rankings.json"
    watchdog_path = source_root / "runtime_external_interference_watchdog.jsonl"
    state_fields = (
        "accepted_rounds", "rejected_outer_attempts", "acceptance_tier",
        "consecutive_failed_rounds", "interval_stable_rounds",
        "stable_intervals", "converged", "full_plan_replay_history_v2",
    )
    expected_reset_files = {
        drain_path.resolve(),
        drain_path.with_name(drain_path.name + ".sha256").resolve(),
    }
    actual_reset_files = {
        path.resolve() for path in reset_root.rglob("*") if path.is_file()
    }
    if (
        state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or not str(state.get("error") or "").startswith(RESET_ERROR_PREFIX)
        or "information_schema.processlist" not in str(state.get("error"))
        or state.get("formal_scope") != "mysql_only"
        or state.get("cross_engine_comparison_prohibited") is not True
        or state.get("formal_evidence_eligible") is not True
        or int(state.get("outer_attempt") or -1)
        != int(reconstructed.get("outer_attempt") or -2)
        or any(state.get(field) != reconstructed.get(field) for field in state_fields)
        or list(outer_root.glob("*.decision.json"))
        or (outer_root / "top5_batch_2/batch_result.json").exists()
        or actual_reset_files != expected_reset_files
    ):
        return None

    ranking = runtime.alg.load(ranking_path)
    active = list(ranking.get("active_decision_scenes") or [])
    selected = ranking.get("selected_top_k") or {}
    expected_active = {
        scene for scene in runtime.SCENES if not reconstructed["converged"][scene]
    }
    if (
        ranking.get("schema_version")
        != "mysql_four_scene_algorithm_c_rankings_boundary_resume_v2"
        or int(ranking.get("outer_attempt") or 0) != expected_outer
        or ranking.get("fixed_top_k") != 5
        or set(active) != expected_active
        or any(len(selected.get(scene) or []) != 5 for scene in active)
    ):
        raise RuntimeError("post-timeout continuation ranking differs")

    batch = runtime.alg.load(batch_path)
    measurements = batch.get("measurements") or {}
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
        or not (900 <= float(batch.get("elapsed_seconds") or 0) <= 910)
        or any(row.get("hard_gate_passed") is not False for row in measurements.values())
        or any(row.get("coverage_complete") is not False for row in measurements.values())
    ):
        raise RuntimeError("post-timeout technical batch differs")
    for row in measurements.values():
        scene = str(row["scene"])
        if scene in active:
            expected_params = selected[scene][0]["optimized_params"]
            if runtime.alg.optimizer_params_key(row["params"]) != runtime.alg.optimizer_params_key(expected_params):
                raise RuntimeError(f"post-timeout {scene} rank-1 params differ")

    drain = runtime.alg.load(drain_path)
    gate = runtime.alg.load(gate_path)
    if (
        drain.get("status") != "PASS"
        or set(drain.get("provably_no_live_mysqld_workers") or [])
        != {"xp_cpu_w1", "xp_io_w1"}
        or any(
            (drain.get("liveness") or {}).get(worker, {}).get(
                "safe_to_discard_unresponsive_predecessor"
            ) is not True
            for worker in ("xp_cpu_w1", "xp_io_w1")
        )
        or gate.get("status") != "PASS"
        or gate.get("errors") not in ([], None)
    ):
        raise RuntimeError("post-timeout reset boundary evidence differs")
    rows = [
        json.loads(line) for line in watchdog_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    interference = {
        "runtime_interference_detected_under_exclusive_wave_lease",
        "cross_project_workload_lease_lost_during_formal_wave",
        "cross_project_workload_lease_deadline_exceeded",
    }
    if (
        not rows or rows[0].get("event") != "watchdog_started"
        or rows[-1].get("event") != "formal_unit_terminal"
        or rows[-1].get("interference_detected") is not False
        or any(row.get("event") in interference for row in rows)
    ):
        raise RuntimeError("post-timeout non-interference evidence differs")

    baseline_binding = state.get("formal_incumbent_baseline") or {}
    baseline_path = Path(str(baseline_binding.get("path") or "")).resolve()
    baseline = runtime.alg.load(baseline_path)
    if (
        baseline_binding.get("sha256") != runtime.alg.sha(baseline_path)
        or baseline.get("status") != "PASS"
        or baseline.get("failures") not in ([], None)
    ):
        raise RuntimeError("post-timeout baseline binding differs")
    binding = {
        "rank": 1, "path": str(batch_path), "sha256": runtime.alg.sha(batch_path),
        "technical_timeout_excluded": True,
    }
    exclusion = {
        "classification": "TECHNICAL_TIMEOUT_EXCLUDED",
        "batch_index": 1, "candidate_ranks": [1],
        "timed_out_tasks": sorted(timeout_tasks),
        "derived_plan_capture_skips": sorted(skipped),
        "batch_result_path": str(batch_path),
        "batch_result_sha256": runtime.alg.sha(batch_path),
        "candidate_state_mutation": False,
        "accepted_state_credit_increment": 0,
        "rejected_outer_attempt_increment": 0,
        "convergence_mutation": False,
        "replay_history_mutation": False,
        "pruning_evidence_eligible": False,
        "acceptance_tier_mutation": False,
    }
    return {
        "source_root": str(source_root),
        "source_state": {"path": str(state_path), "sha256": runtime.alg.sha(state_path)},
        "baseline": {"path": str(baseline_path), "sha256": runtime.alg.sha(baseline_path)},
        "predicted_rankings": {"path": str(ranking_path), "sha256": runtime.alg.sha(ranking_path)},
        "blocked_gate": {"path": str(gate_path), "sha256": runtime.alg.sha(gate_path)},
        "continuation_reason": "technical_timeout_then_stale_liveness_reset_failure",
        "runtime_interruption_watchdog": {"path": str(watchdog_path), "sha256": runtime.alg.sha(watchdog_path)},
        "completed_batches": [binding],
        "start_rank": 2,
        "outer_attempt": expected_outer,
        "ranking_document": ranking,
        "evaluations": {scene: [] for scene in active},
        "technical_batch_exclusions": [exclusion],
        "candidate_state_mutation_before_continuation": False,
        "accepted_state_credit_before_continuation": 0,
        "completed_rank_release_race": False,
        "completed_rank5_recovery": None,
        "post_drain_liveness_recovery": {
            "failed_reset_root": str(reset_root),
            "pre_reset_session_drain": {"path": str(drain_path), "sha256": runtime.alg.sha(drain_path)},
            "final_liveness_used_by_successor_reset": True,
            "fresh_reset_required": True,
        },
    }


def validate_formal_partial_continuation(**kwargs: Any) -> dict[str, Any]:
    recovered = _technical_timeout_reset_failure_recovery(**kwargs)
    if recovered is not None:
        return recovered
    return ORIGINAL_C26_VALIDATOR(**kwargs)


def main() -> int:
    v19.wait_for_xp_session_drain = wait_for_xp_session_drain
    v22.state_document = state_document
    c26.validate_formal_partial_continuation = validate_formal_partial_continuation
    return c26.main()


if __name__ == "__main__":
    raise SystemExit(main())
