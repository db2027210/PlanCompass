#!/usr/bin/env python3
"""Recover a completed Top-5 after confirmation-decision serialization failure."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v20 as confirmation
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v25 as v25


runtime = v25.runtime
ORIGINAL_V25_VALIDATOR = v25.validate_formal_partial_continuation
SERIALIZATION_ERROR = (
    "TypeError: ConfirmationCount.__new__() missing 1 required positional "
    "argument: 'confirmation_open'"
)


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def _confirmation_count_deepcopy(
    value: confirmation.ConfirmationCount, memo: dict[int, Any],
) -> confirmation.ConfirmationCount:
    duplicate = confirmation.ConfirmationCount(
        int(value), bool(value.confirmation_open),
    )
    memo[id(value)] = duplicate
    return duplicate


def _completed_top5_serialization_recovery(
    *, source_root: Path, checkpoint: Mapping[str, Any],
    reconstructed: Mapping[str, Any], start_rank: int,
    completed_rank5_root: Path | None,
) -> dict[str, Any] | None:
    source_root = Path(source_root).resolve()
    if (
        start_rank != 5
        or completed_rank5_root is None
        or Path(completed_rank5_root).resolve() != source_root
    ):
        return None
    state_path = source_root / "run_state.json"
    state = runtime.alg.load(state_path)
    expected_outer = int(reconstructed.get("outer_attempt") or 0) + 1
    outer_root = source_root / f"outer_{expected_outer:03d}"
    decision_paths = list(outer_root.glob("*.decision.json"))
    state_fields = (
        "accepted_rounds", "rejected_outer_attempts", "acceptance_tier",
        "consecutive_failed_rounds", "interval_stable_rounds",
        "converged",
    )
    confirmation_counts = state.get(
        "post_target_stability_confirmation_counts"
    ) or {}
    stable = state.get("stable_intervals") or {}
    reconstructed_stable = reconstructed.get("stable_intervals") or {}
    if (
        state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or state.get("error") != SERIALIZATION_ERROR
        or state.get("formal_scope") != "mysql_only"
        or state.get("cross_engine_comparison_prohibited") is not True
        or state.get("formal_evidence_eligible") is not True
        or state.get("accepted_state_credit_prohibited") is not False
        or int(state.get("outer_attempt") or -1)
        != int(reconstructed.get("outer_attempt") or -2)
        or any(state.get(field) != reconstructed.get(field) for field in state_fields)
        or confirmation_counts != {
            "job_memory": 1,
            "tpch_sf4_memory": 0,
            "hybench_xp_cpu": 0,
            "hybench_xp_io": 0,
        }
        or any(
            stable.get(scene) != reconstructed_stable.get(scene)
            for scene in runtime.SCENES if scene != "job_memory"
        )
        or stable.get("job_memory") == reconstructed_stable.get("job_memory")
        or decision_paths
    ):
        return None

    ranking_path = outer_root / "predicted_rankings.json"
    ranking = runtime.alg.load(ranking_path)
    active = list(ranking.get("active_decision_scenes") or [])
    selected = _mapping(ranking.get("selected_top_k"), "selected Top-5")
    expected_active = {
        scene for scene in runtime.SCENES
        if not reconstructed["converged"][scene]
    }
    if (
        ranking.get("schema_version")
        != "mysql_four_scene_algorithm_c_rankings_boundary_resume_v2"
        or int(ranking.get("outer_attempt") or 0) != expected_outer
        or ranking.get("fixed_top_k") != 5
        or set(active) != expected_active
        or any(len(selected.get(scene) or []) != 5 for scene in active)
    ):
        raise RuntimeError("completed Top-5 recovery ranking differs")

    baseline_binding = _mapping(
        state.get("formal_incumbent_baseline"), "formal incumbent baseline",
    )
    baseline_path = Path(str(baseline_binding.get("path") or "")).resolve()
    baseline = runtime.alg.load(baseline_path)
    if (
        baseline_binding.get("sha256") != runtime.alg.sha(baseline_path)
        or baseline.get("status") != "PASS"
        or baseline.get("failures") not in ([], None)
        or len(baseline.get("measurements") or {}) != len(runtime.alg.WORKER_IDS)
        or any(
            row.get("hard_gate_passed") is not True
            or row.get("coverage_complete") is not True
            for row in (baseline.get("measurements") or {}).values()
        )
    ):
        raise RuntimeError("completed Top-5 recovery baseline differs")

    inherited = {
        int(row.get("rank") or 0): dict(row)
        for row in (
            (state.get("formal_partial_continuation") or {})
            .get("completed_batches") or []
        )
        if isinstance(row, Mapping)
    }
    evaluations = {scene: [] for scene in active}
    bindings: list[dict[str, Any]] = []
    companion_scenes: set[str] = set()
    for rank in range(1, 6):
        local = outer_root / f"top5_batch_{rank}/batch_result.json"
        prior = inherited.get(rank)
        path = local if local.is_file() else Path(str((prior or {}).get("path") or "")).resolve()
        if (
            not path.is_file()
            or path.is_symlink()
            or (not local.is_file() and (
                prior is None
                or prior.get("path") != str(path)
                or prior.get("sha256") != runtime.alg.sha(path)
            ))
        ):
            raise RuntimeError(f"completed Top-5 rank {rank} binding differs")
        batch = runtime.alg.load(path)
        measurements = _mapping(batch.get("measurements"), f"rank {rank} measurements")
        observed = {str(row.get("scene")) for row in measurements.values()}
        if (
            batch.get("status") != "PASS"
            or batch.get("failures") not in ([], None)
            or len(measurements) != len(runtime.alg.WORKER_IDS) // 2
            or observed != set(runtime.SCENES)
            or any(
                (
                    int(row.get("rank") or 0) != rank
                    if str(row.get("scene")) in active
                    else int(row.get("rank") or 0) != 0
                )
                or row.get("hard_gate_passed") is not True
                or row.get("coverage_complete") is not True
                for row in measurements.values()
            )
        ):
            raise RuntimeError(f"completed Top-5 rank {rank} evidence differs")
        binding = {"rank": rank, "path": str(path), "sha256": runtime.alg.sha(path)}
        bindings.append(binding)
        for row in measurements.values():
            scene = str(row["scene"])
            if scene not in active:
                companion_scenes.add(scene)
                continue
            expected_params = _mapping(
                selected[scene][rank - 1].get("optimized_params"),
                f"{scene} rank {rank} predicted params",
            )
            if runtime.alg.optimizer_params_key(row["params"]) != runtime.alg.optimizer_params_key(expected_params):
                raise RuntimeError(f"completed Top-5 {scene} rank {rank} params differ")
            seeded = copy.deepcopy(dict(row))
            seeded["formal_completed_top5_replay_source"] = copy.deepcopy(binding)
            evaluations[scene].append(seeded)

    watchdog_path = source_root / "runtime_external_interference_watchdog.jsonl"
    rows = [
        json.loads(line) for line in watchdog_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    interference_events = {
        "runtime_interference_detected_under_exclusive_wave_lease",
        "cross_project_workload_lease_lost_during_formal_wave",
        "cross_project_workload_lease_deadline_exceeded",
    }
    if (
        not rows
        or rows[0].get("event") != "watchdog_started"
        or rows[-1].get("event") != "formal_unit_terminal"
        or rows[-1].get("interference_detected") is not False
        or any(row.get("event") in interference_events for row in rows)
    ):
        raise RuntimeError("completed Top-5 non-interference evidence differs")

    gate_path = outer_root / "top5_batch_5_cross_project_wave_quiet_gate_attempt_001.json"
    gate = runtime.alg.load(gate_path)
    if gate.get("status") != "PASS":
        raise RuntimeError("completed Top-5 final quiet gate differs")
    return {
        "source_root": str(source_root),
        "source_state": {"path": str(state_path), "sha256": runtime.alg.sha(state_path)},
        "baseline": {"path": str(baseline_path), "sha256": runtime.alg.sha(baseline_path)},
        "predicted_rankings": {"path": str(ranking_path), "sha256": runtime.alg.sha(ranking_path)},
        "blocked_gate": {"path": str(gate_path), "sha256": runtime.alg.sha(gate_path)},
        "continuation_reason": "completed_top5_confirmation_serialization_recovery",
        "runtime_interruption_watchdog": {
            "path": str(watchdog_path), "sha256": runtime.alg.sha(watchdog_path),
        },
        "completed_batches": bindings,
        "start_rank": 6,
        "outer_attempt": expected_outer,
        "ranking_document": ranking,
        "evaluations": evaluations,
        "technical_batch_exclusions": [],
        "candidate_state_mutation_before_continuation": False,
        "accepted_state_credit_before_continuation": 0,
        "completed_rank_release_race": False,
        "completed_rank5_recovery": {
            "source_root": str(source_root),
            "source_state": {"path": str(state_path), "sha256": runtime.alg.sha(state_path)},
            "watchdog": {"path": str(watchdog_path), "sha256": runtime.alg.sha(watchdog_path), "interference_detected": False},
            "batch": copy.deepcopy(bindings[-1]),
            "measurement_replayed_without_rerun": True,
            "decision_state_mutation_before_recovery": False,
            "failed_write_artifact_count": 0,
            "in_memory_confirmation_discarded_in_favor_of_checkpoint": True,
        },
        "companion_aware_partial_inheritance": {
            "release_version": "v26",
            "active_decision_scenes": sorted(active),
            "companion_scenes": sorted(companion_scenes),
            "completed_full_batch_bindings_unchanged": True,
            "companion_rows_used_for_decision_evaluations": False,
            "companion_full_workload_evidence_preserved": True,
        },
    }


def validate_formal_partial_continuation(**kwargs: Any) -> dict[str, Any]:
    recovered = _completed_top5_serialization_recovery(**kwargs)
    if recovered is not None:
        return recovered
    return ORIGINAL_V25_VALIDATOR(**kwargs)


def main() -> int:
    confirmation.ConfirmationCount.__deepcopy__ = _confirmation_count_deepcopy
    v25.validate_formal_partial_continuation = validate_formal_partial_continuation
    return v25.main()


if __name__ == "__main__":
    raise SystemExit(main())
