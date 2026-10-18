#!/usr/bin/env python3
"""Resume Algorithm C with paired revalidation and PG coverage expansion.

The predecessor compared candidate QPS measured on different physical lanes
against one global incumbent sample.  On heterogeneous lanes this can promote
a fast-lane sample and then reject equivalent points measured elsewhere.  This
successor keeps raw QPS as evidence, validates every apparent acceptance and
every second consecutive rejection against a fresh incumbent replay on the
same lane, and completes PostgreSQL's deterministic current-interval plan
coverage before a second rejection is allowed to shrink the interval.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import copy
import fcntl
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Callable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from common_outer_loop import advance_acceptance_tier, decide_validation_acceptance
from scripts import build_mysql_algorithm_c_outer_checkpoint_v4 as checkpointmod
from scripts import run_mysql_four_scene_algorithm_c_resume_v1 as predecessor
from scripts import run_mysql_four_scene_algorithm_c_v2 as alg
from scripts import mysql_cross_project_wave_coordination_v1 as coordination
from scripts import mysql_algorithm_b_pg_outer_policy_v31 as pg_outer
from scripts import mysql_algorithm_c_global_coverage_v1 as global_coverage
from scripts import mysql_outer_rejection_cycle_v1 as rejection_cycle
from scripts import mysql_async_scene_start_barrier_v1 as scene_start_barrier
from mysql_algorithm_c_plan_replay_pruning import (
    CONTRACT_VERSION as PLAN_PRUNING_CONTRACT_VERSION,
    rank_and_prune_candidates,
    record_outcomes as record_plan_replay_outcomes,
)


FULL_SCENES = tuple(alg.SCENES)
SCENES = FULL_SCENES
INDEPENDENT_SCENE_EXECUTION = False
NO_LEASE_RUNTIME = False
STATE_SCHEMA = "mysql_four_scene_algorithm_c_boundary_resume_state_v2"
TARGET_ACCEPTED_ROUNDS = predecessor.TARGET_ACCEPTED_ROUNDS
MAXIMUM_REJECTED_OUTER_ATTEMPTS_PER_SCENE = (
    predecessor.MAXIMUM_REJECTED_OUTER_ATTEMPTS_PER_SCENE
)
OPTIMIZATION_CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260815_v46"
    / "optimization_contract.json"
)
PLAN_PRUNING_CONTRACT = (
    ROOT / "config/formal/mysql_algorithm_c_plan_replay_pruning_20260813_v2"
    / "contract.json"
)
WAVE_COORDINATION_CONTRACT = coordination.CONTRACT_PATH
NON_TARGET_BOTTLENECK_THRESHOLD = 0.8
FIXED_TOP_K = 5
INDEPENDENT_SCENE_WORKER_COUNT = 2
FORMAL_CANDIDATE_BATCHES = ((1, 2), (3, 4), (5,))
REUSE_CHECKPOINT_INCUMBENT_BASELINE = False
MAXIMUM_OUTER_ATTEMPTS = 100
BASE_CANDIDATE_STEP_FRACTION = 0.08
SUPPLEMENTARY_CANDIDATE_STEP_FRACTIONS = (
    0.16, 0.24, 0.32, 0.40, 0.48, 0.56, 0.64, 0.72, 0.80, 0.88, 0.96, 1.0,
)
LANE_PAIRED_REVALIDATION_SCHEMA = (
    "mysql_algorithm_c_lane_paired_incumbent_revalidation_v1"
)
PG_CURRENT_INTERVAL_COVERAGE_SCHEMA = global_coverage.SCHEMA_VERSION


def candidate_non_target_bottleneck_gate(
    row: Mapping[str, Any], *, target_resource: str,
) -> tuple[bool, list[str]]:
    """Fail closed when a candidate creates a non-target bottleneck."""
    resources = row.get("resource_means")
    if not isinstance(resources, Mapping):
        return False, sorted(name for name in ("cpu", "memory", "io") if name != target_resource)
    violations: list[str] = []
    for name in ("cpu", "memory", "io"):
        if name == target_resource:
            continue
        try:
            value = float(resources[name])
        except (KeyError, TypeError, ValueError):
            violations.append(name)
            continue
        if not math.isfinite(value) or value >= NON_TARGET_BOTTLENECK_THRESHOLD:
            violations.append(name)
    return not violations, sorted(violations)


def _path(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular non-symlink file required: {path}")
    return path


def _directory(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_dir() or path.is_symlink():
        raise RuntimeError(f"regular non-symlink directory required: {path}")
    return path


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} object required")
    return dict(value)


def _validate_formal_partial_continuation(
    *, source_root: Path, checkpoint: Mapping[str, Any],
    reconstructed: Mapping[str, Any], start_rank: int,
    completed_rank5_root: Path | None = None,
) -> dict[str, Any]:
    """Bind a failed pre-measurement boundary and reuse only completed ranks."""
    if start_rank not in range(2, 6):
        raise RuntimeError("formal partial continuation rank must be 2..5")
    source_state_path = source_root / "run_state.json"
    source_state = alg.load(source_state_path)
    expected_outer = int(reconstructed["outer_attempt"]) + 1
    outer_root = source_root / f"outer_{expected_outer:03d}"
    ranking_path = outer_root / "predicted_rankings.json"
    interruption_watchdog_path = (
        source_root / "runtime_external_interference_watchdog.jsonl"
    )
    runtime_watchdog_exists = interruption_watchdog_path.is_file()
    interruption_watchdog_rows = (
        [
            json.loads(line)
            for line in interruption_watchdog_path.read_text(
                encoding="utf-8"
            ).splitlines()
            if line.strip()
        ]
        if runtime_watchdog_exists else []
    )
    runtime_interrupted = any(
        row.get("event") in {
            "runtime_interference_detected_under_exclusive_wave_lease",
            "cross_project_workload_lease_lost_during_formal_wave",
        }
        for row in interruption_watchdog_rows
    )
    release_race_stage = (
        f"OUTER_{expected_outer:03d}_BATCH_{start_rank - 1}_PLAN_CAPTURE"
    )
    lease_loss_events = [
        row for row in interruption_watchdog_rows
        if row.get("event")
        == "cross_project_workload_lease_lost_during_formal_wave"
    ]
    completed_rank_release_race = (
        runtime_interrupted
        and start_rank >= 2
        and source_state.get("status") == "FAILED_CLOSED"
        and source_state.get("stage") == "FAILED"
        and source_state.get("error") == "KeyboardInterrupt: "
        and len(lease_loss_events) == 1
        and lease_loss_events[0].get("runtime_stage") == release_race_stage
        and lease_loss_events[0].get("coordination_phase") == "measuring"
        and lease_loss_events[0].get(
            "lease_loss_confirmed_after_fresh_state_reread"
        ) is not True
        and (
            outer_root / f"top5_batch_{start_rank - 1}/batch_result.json"
        ).is_file()
    )
    baseline_binding = source_state.get("formal_incumbent_baseline")
    baseline_path = (
        Path(str(_mapping(baseline_binding, "formal baseline binding")["path"]))
        .expanduser().resolve()
        if isinstance(baseline_binding, Mapping)
        else source_root / "formal_incumbent_baseline/batch_result.json"
    )
    gate_rank = start_rank - 1 if completed_rank_release_race else start_rank
    gate_path = outer_root / (
        f"top5_batch_{gate_rank}_cross_project_wave_quiet_gate_attempt_001.json"
    )
    ranking = alg.load(ranking_path)
    baseline = alg.load(baseline_path)
    gate = alg.load(gate_path)
    technical_reset_root = (
        source_root / "xp_resets"
        / f"outer_{expected_outer:03d}_batch_{start_rank}"
    )
    technical_batch_path = (
        outer_root / f"top5_batch_{start_rank - 1}/batch_result.json"
    )
    technical_session_drain_continuation = (
        not runtime_interrupted
        and runtime_watchdog_exists
        and start_rank >= 2
        and source_state.get("status") == "FAILED_CLOSED"
        and source_state.get("stage") == "FAILED"
        and str(source_state.get("error") or "").startswith(
            "RuntimeError: XP active client sessions were not proven drained before "
        )
        and gate.get("status") == "PASS"
        and gate.get("errors") in ([], None)
        and technical_batch_path.is_file()
        and not (outer_root / f"top5_batch_{start_rank}/batch_result.json").exists()
        and technical_reset_root.is_dir()
        and not list(technical_reset_root.rglob("*"))
        and interruption_watchdog_rows
        and interruption_watchdog_rows[0].get("event") == "watchdog_started"
        and interruption_watchdog_rows[-1].get("event") == "formal_unit_terminal"
        and interruption_watchdog_rows[-1].get("interference_detected") is False
    )
    state_fields = (
        "accepted_rounds", "rejected_outer_attempts", "acceptance_tier",
        "consecutive_failed_rounds", "interval_stable_rounds",
        "stable_intervals", "converged",
    )
    shared_authority_differs = (
        source_state.get("formal_evidence_eligible") is not True
        or source_state.get("formal_scope") != "mysql_only"
        or source_state.get("cross_engine_comparison_prohibited") is not True
        or int(source_state.get("outer_attempt") or -1)
        != int(reconstructed["outer_attempt"])
        or any(source_state.get(field) != reconstructed.get(field) for field in state_fields)
        or baseline.get("status") != "PASS"
        or baseline.get("failures") not in ([], None)
        or len(baseline.get("measurements") or {}) != len(alg.WORKER_IDS)
        or any(
            row.get("hard_gate_passed") is not True
            or row.get("coverage_complete") is not True
            for row in (baseline.get("measurements") or {}).values()
        )
        or ranking.get("schema_version")
        != "mysql_four_scene_algorithm_c_rankings_boundary_resume_v2"
        or int(ranking.get("outer_attempt") or 0) != expected_outer
        or ranking.get("fixed_top_k") != 5
        or set(ranking.get("active_decision_scenes") or [])
        != {scene for scene in SCENES if not reconstructed["converged"][scene]}
        or (outer_root / f"top5_batch_{start_rank}/batch_result.json").exists()
    )
    if runtime_interrupted:
        watchdog_interruption = source_state.get("watchdog_interruption") or {}
        expected_measurement_stage = (
            f"OUTER_{expected_outer:03d}_BATCH_{start_rank}_MEASURING"
        )
        expected_reset_stage = (
            f"OUTER_{expected_outer:03d}_BATCH_{start_rank}_RESETTING_XP"
        )
        interrupted_stage = (
            watchdog_interruption.get("runtime_stage")
            or source_state.get("stage")
        )
        reset_root = (
            source_root / "xp_resets"
            / f"outer_{expected_outer:03d}_batch_{start_rank}"
        )
        reset_interrupted_before_mutation = (
            interrupted_stage == expected_reset_stage
            and not (reset_root / "reset_binding.json").exists()
            and not list(reset_root.glob("*/reset_worker.json"))
        )
        accepted_interrupted_stage = (
            interrupted_stage == expected_measurement_stage
            or reset_interrupted_before_mutation
        )
        watchdog_marked_failed = (
            source_state.get("status") == "FAILED_CLOSED"
            and source_state.get("stage") == "FAILED"
            and source_state.get("error")
            == (
                "RuntimeError: runtime watchdog stopped formal run after "
                "runtime_interference_detected_under_exclusive_wave_lease"
            )
            and watchdog_interruption.get("event")
            == "runtime_interference_detected_under_exclusive_wave_lease"
            and accepted_interrupted_stage
            and watchdog_interruption.get("formal_state_credit_increment") == 0
            and watchdog_interruption.get("interrupted_candidate_replay_prohibited")
            is True
        )
        legacy_stale_running = (
            source_state.get("status") == "RUNNING"
            and source_state.get("stage") == expected_measurement_stage
            and source_state.get("error") in (None, "")
        )
        interference_events = [
            row for row in interruption_watchdog_rows
            if row.get("event")
            == "runtime_interference_detected_under_exclusive_wave_lease"
        ]
        if completed_rank_release_race:
            runtime_authority_differs = (
                gate.get("status") != "PASS"
                or gate.get("errors") not in ([], None)
                or not interruption_watchdog_rows
                or interruption_watchdog_rows[0].get("event")
                != "watchdog_started"
                or interruption_watchdog_rows[-1].get("event")
                != "formal_failed_closed_after_sigint"
                or len(lease_loss_events) != 1
                or interference_events
                or any(
                    (outer_root / f"top5_batch_{rank}/batch_result.json").exists()
                    for rank in range(start_rank, 6)
                )
                or any(
                    (source_root / "xp_resets" / f"outer_{expected_outer:03d}_batch_{rank}").exists()
                    for rank in range(start_rank, 6)
                )
            )
        else:
            runtime_authority_differs = (
                not (watchdog_marked_failed or legacy_stale_running)
                or gate.get("status") != "PASS"
                or gate.get("errors") not in ([], None)
                or not interruption_watchdog_rows
                or interruption_watchdog_rows[0].get("event") != "watchdog_started"
                or interruption_watchdog_rows[-1].get("event")
                != "formal_forced_stopped_after_sigint_timeout"
                or len(interference_events) != 1
                or interference_events[0].get("runtime_stage") != interrupted_stage
                or not (interference_events[0].get("blockers") or [])
                or any(
                    (outer_root / f"top5_batch_{rank}/batch_result.json").exists()
                    for rank in range(start_rank, 6)
                )
            )
        if shared_authority_differs or runtime_authority_differs:
            raise RuntimeError("formal runtime-interrupted continuation authority differs")
    else:
        external_gate_continuation = (
            source_state.get("status") == "FAILED_CLOSED"
            and source_state.get("error")
            == "RuntimeError: external physical workload bypassed the exclusive wave lease"
            and gate.get("status") == "BLOCKED_FAIL_CLOSED"
            and gate.get("errors")
            == ["external_database_physical_workload_present_while_exclusive"]
            and any(
                sample.get("external_workloads")
                for sample in gate.get("samples") or []
            )
        )
        if shared_authority_differs or not (
            external_gate_continuation or technical_session_drain_continuation
        ):
            raise RuntimeError("formal partial continuation authority differs")

    selected = _mapping(ranking.get("selected_top_k"), "partial selected top-k")
    active_scenes = list(ranking["active_decision_scenes"])
    if any(len(selected.get(scene) or []) != 5 for scene in active_scenes):
        raise RuntimeError("formal partial continuation Top-5 differs")
    evaluations: dict[str, list[dict[str, Any]]] = {
        scene: [] for scene in active_scenes
    }
    technical_batch_exclusions: list[dict[str, Any]] = []
    batch_bindings: list[dict[str, Any]] = []
    inherited_rows = (
        (source_state.get("formal_partial_continuation") or {})
        .get("completed_batches") or []
    )
    inherited_bindings = {
        int(row.get("rank") or 0): row
        for row in inherited_rows
        if isinstance(row, Mapping)
    }
    if len(inherited_bindings) != len(inherited_rows):
        raise RuntimeError("formal partial inherited batch bindings differ")
    for rank in range(1, start_rank):
        local_batch_path = outer_root / f"top5_batch_{rank}/batch_result.json"
        inherited = inherited_bindings.get(rank)
        batch_path = (
            local_batch_path
            if local_batch_path.is_file()
            else Path(str((inherited or {}).get("path") or "")).expanduser().resolve()
        )
        if (
            not batch_path.is_file()
            or batch_path.is_symlink()
            or (
                inherited is not None
                and not local_batch_path.is_file()
                and (
                    inherited.get("path") != str(batch_path)
                    or inherited.get("sha256") != alg.sha(batch_path)
                )
            )
        ):
            raise RuntimeError(f"formal partial rank {rank} binding differs")
        batch = alg.load(batch_path)
        measurements = _mapping(
            batch.get("measurements"), f"formal partial rank {rank} measurements"
        )
        inherited_technical_timeout = bool(
            inherited is not None
            and inherited.get("technical_timeout_excluded") is True
        )
        technical_timeout_authorized = (
            inherited_technical_timeout
            or (
                technical_session_drain_continuation
                and rank == start_rank - 1
            )
        )
        technical_timeout_batch = (
            technical_timeout_authorized
            and batch.get("status") == "FAILED_CLOSED"
            and len(measurements) == len(active_scenes)
            and {str(row.get("scene")) for row in measurements.values()}
            == set(active_scenes)
            and all(
                int(row.get("rank") or 0) == rank
                and row.get("hard_gate_passed") is False
                and row.get("coverage_complete") is False
                for row in measurements.values()
            )
            and classify_technical_timeout_batch(
                batch,
                {
                    worker_id: {
                        "rank": rank,
                        "params": dict(row["params"]),
                    }
                    for worker_id, row in measurements.items()
                },
            ) is not None
        )
        passed_batch = (
            batch.get("status") == "PASS"
            and batch.get("failures") in ([], None)
            and len(measurements) == len(active_scenes)
            and {str(row.get("scene")) for row in measurements.values()}
            == set(active_scenes)
            and all(
                int(row.get("rank") or 0) == rank
                and row.get("hard_gate_passed") is True
                and row.get("coverage_complete") is True
                for row in measurements.values()
            )
        )
        if (
            (inherited_technical_timeout and not technical_timeout_batch)
            or not (passed_batch or technical_timeout_batch)
        ):
            raise RuntimeError(f"formal partial rank {rank} evidence differs")
        binding = {
            "rank": rank, "path": str(batch_path), "sha256": alg.sha(batch_path),
        }
        if technical_timeout_batch:
            binding["technical_timeout_excluded"] = True
        batch_bindings.append(binding)
        if technical_timeout_batch:
            technical = classify_technical_timeout_batch(
                batch,
                {
                    worker_id: {"rank": rank, "params": dict(row["params"])}
                    for worker_id, row in measurements.items()
                },
            )
            assert technical is not None
            technical_batch_exclusions.append({
                **technical,
                "batch_index": rank,
                "candidate_ranks": [rank],
                "batch_result_path": str(batch_path),
                "batch_result_sha256": alg.sha(batch_path),
                "formal_partial_replay_source": copy.deepcopy(binding),
            })
            continue
        for row in measurements.values():
            scene = str(row["scene"])
            expected_params = _mapping(
                selected[scene][rank - 1].get("optimized_params"),
                f"formal partial {scene} rank {rank} predicted params",
            )
            if alg.optimizer_params_key(row["params"]) != alg.optimizer_params_key(
                expected_params
            ):
                raise RuntimeError(
                    f"formal partial {scene} rank {rank} parameters differ"
                )
            seeded = copy.deepcopy(dict(row))
            seeded["formal_partial_replay_source"] = copy.deepcopy(binding)
            evaluations[scene].append(seeded)
    completed_rank5_recovery: dict[str, Any] | None = None
    if completed_rank5_root is not None:
        if start_rank != 5:
            raise RuntimeError("completed rank 5 recovery requires start rank 5")
        recovery_state_path = completed_rank5_root / "run_state.json"
        recovery_watchdog_path = (
            completed_rank5_root / "runtime_external_interference_watchdog.jsonl"
        )
        recovery_batch_path = (
            completed_rank5_root
            / f"outer_{expected_outer:03d}/top5_batch_5/batch_result.json"
        )
        for path in (recovery_state_path, recovery_watchdog_path, recovery_batch_path):
            if not path.is_file() or path.is_symlink():
                raise RuntimeError(f"completed rank 5 recovery evidence differs: {path}")
        recovery_state = alg.load(recovery_state_path)
        recovery_partial = _mapping(
            recovery_state.get("formal_partial_continuation"),
            "completed rank 5 recovery partial lineage",
        )
        watchdog_rows = [
            json.loads(line)
            for line in recovery_watchdog_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if (
            recovery_state.get("status") != "FAILED_CLOSED"
            or recovery_state.get("error")
            != "KeyError: 'full_plan_replay_history_v2'"
            or recovery_state.get("formal_evidence_eligible") is not True
            or recovery_state.get("accepted_state_credit_prohibited") is not False
            or recovery_state.get("formal_scope") != "mysql_only"
            or recovery_state.get("cross_engine_comparison_prohibited") is not True
            or recovery_state.get("candidate_batches_for_this_execution") != [[5]]
            or recovery_partial.get("source_root") != str(source_root)
            or int(recovery_partial.get("outer_attempt") or 0) != expected_outer
            or int(recovery_partial.get("start_rank") or 0) != 5
            or recovery_partial.get("completed_batches") != batch_bindings
            or not watchdog_rows
            or watchdog_rows[0].get("event") != "watchdog_started"
            or watchdog_rows[-1].get("event") != "formal_unit_terminal"
            or watchdog_rows[-1].get("interference_detected") is not False
            or any(row.get("physical_workloads") for row in watchdog_rows)
            or any(row.get("event") == "external_postgres_interference" for row in watchdog_rows)
        ):
            raise RuntimeError("completed rank 5 recovery authority differs")
        recovery_batch = alg.load(recovery_batch_path)
        recovery_measurements = _mapping(
            recovery_batch.get("measurements"),
            "completed rank 5 recovery measurements",
        )
        if (
            recovery_batch.get("status") != "PASS"
            or recovery_batch.get("failures") not in ([], None)
            or len(recovery_measurements) != len(active_scenes)
            or {str(row.get("scene")) for row in recovery_measurements.values()}
            != set(active_scenes)
            or any(
                int(row.get("rank") or 0) != 5
                or row.get("hard_gate_passed") is not True
                or row.get("coverage_complete") is not True
                for row in recovery_measurements.values()
            )
        ):
            raise RuntimeError("completed rank 5 measurement differs")
        recovery_binding = {
            "rank": 5,
            "path": str(recovery_batch_path),
            "sha256": alg.sha(recovery_batch_path),
        }
        for row in recovery_measurements.values():
            scene = str(row["scene"])
            expected_params = _mapping(
                selected[scene][4].get("optimized_params"),
                f"formal partial {scene} rank 5 predicted params",
            )
            if alg.optimizer_params_key(row["params"]) != alg.optimizer_params_key(
                expected_params
            ):
                raise RuntimeError(
                    f"formal partial {scene} recovered rank 5 parameters differ"
                )
            seeded = copy.deepcopy(dict(row))
            seeded["formal_completed_rank5_replay_source"] = copy.deepcopy(
                recovery_binding
            )
            evaluations[scene].append(seeded)
        batch_bindings.append(recovery_binding)
        completed_rank5_recovery = {
            "source_root": str(completed_rank5_root),
            "source_state": {
                "path": str(recovery_state_path),
                "sha256": alg.sha(recovery_state_path),
            },
            "watchdog": {
                "path": str(recovery_watchdog_path),
                "sha256": alg.sha(recovery_watchdog_path),
                "interference_detected": False,
            },
            "batch": copy.deepcopy(recovery_binding),
            "measurement_replayed_without_rerun": True,
            "decision_state_mutation_before_recovery": False,
        }
    return {
        "source_root": str(source_root),
        "source_state": {"path": str(source_state_path), "sha256": alg.sha(source_state_path)},
        "baseline": {"path": str(baseline_path), "sha256": alg.sha(baseline_path)},
        "predicted_rankings": {"path": str(ranking_path), "sha256": alg.sha(ranking_path)},
        "blocked_gate": {"path": str(gate_path), "sha256": alg.sha(gate_path)},
        "continuation_reason": (
            "completed_rank_release_watchdog_toctou_recovery"
            if completed_rank_release_race
            else (
                "runtime_external_interference_watchdog_stop"
                if runtime_interrupted else (
                    "technical_timeout_batch_then_empty_next_reset_failure"
                    if technical_session_drain_continuation
                    else "premeasurement_external_workload_gate"
                )
            )
        ),
        "runtime_interruption_watchdog": (
            {
                "path": str(interruption_watchdog_path),
                "sha256": alg.sha(interruption_watchdog_path),
            }
            if runtime_interrupted else None
        ),
        "completed_batches": batch_bindings,
        "start_rank": 6 if completed_rank5_recovery is not None else start_rank,
        "outer_attempt": expected_outer,
        "ranking_document": ranking,
        "evaluations": evaluations,
        "technical_batch_exclusions": technical_batch_exclusions,
        "candidate_state_mutation_before_continuation": False,
        "accepted_state_credit_before_continuation": 0,
        "completed_rank_release_race": completed_rank_release_race,
        "completed_rank5_recovery": completed_rank5_recovery,
    }


def _scene_mapping(value: Any, label: str) -> dict[str, Any]:
    result = _mapping(value, label)
    if set(result) == set(SCENES):
        return result
    if INDEPENDENT_SCENE_EXECUTION and set(result) == set(FULL_SCENES):
        return {scene: result[scene] for scene in SCENES}
    raise RuntimeError(f"{label} scene set differs")


def _configure_live_authority(
    runtime_preflight: Path, prepared_binding: Path, benchmark_accounts: Path,
) -> None:
    alg.r0mod.PREFLIGHT = runtime_preflight
    alg.r0mod.RUNTIME_PREFLIGHT = runtime_preflight
    alg.r0mod.PREPARED = prepared_binding
    alg.r0mod.BENCHMARK_ACCOUNTS = benchmark_accounts


def _rank_scene(
    *, scene: str, params: Mapping[str, float], ranges: Mapping[str, Any],
    resources: Mapping[str, float], freeze: Mapping[str, Any],
    worker: Mapping[str, Any], step_fraction: float = BASE_CANDIDATE_STEP_FRACTION,
) -> dict[str, Any]:
    """Rank one scene on its own frozen representative data plane."""
    started = time.monotonic()
    rankings = alg.candidate_rankings(
        scene, params, ranges, resources, freeze=freeze, worker=worker,
        step_fraction=step_fraction,
    )
    return {
        "scene": scene,
        "representative_worker_id": worker["worker_id"],
        "candidate_step_fraction": step_fraction,
        "elapsed_seconds": time.monotonic() - started,
        "rankings": rankings,
    }


def _selectable_unique_parameter_points(
    rows: Sequence[Mapping[str, Any]],
    permanently_invalid_param_keys: set[str],
) -> int:
    return len({
        alg.optimizer_params_key(row["optimized_params"])
        for row in rows
        if alg.optimizer_params_key(row["optimized_params"])
        not in permanently_invalid_param_keys
    })


def supplement_and_select_fixed_top_k(
    *, base_rankings: Sequence[Mapping[str, Any]],
    plan_history: Sequence[Mapping[str, Any]],
    replayed_param_keys: set[str],
    permanently_invalid_param_keys: set[str],
    supplementary_factory: Callable[
        [float, str], Sequence[Mapping[str, Any]]
    ],
    top_k: int = FIXED_TOP_K,
) -> tuple[
    list[dict[str, Any]], list[dict[str, Any]], dict[str, Any],
    list[dict[str, Any]], list[dict[str, Any]],
]:
    """Fill a rounded/boundary-collapsed pool with legal in-range step points.

    Every supplementary pool uses the same gradients, plan-physics model,
    candidate score, plan-history pruning and real-replay requirement.  This
    changes neither the score nor fixed Top-K; it only avoids treating several
    subsets that collapse to one optimizer point as distinct candidates.
    """
    if top_k != FIXED_TOP_K:
        raise ValueError(f"formal fixed top-k must remain {FIXED_TOP_K}")
    pools: list[Sequence[Mapping[str, Any]]] = [base_rankings]
    applied_strategies: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []

    def evaluate_pool(
        *, fraction: float, direction_mode: str,
    ) -> tuple[
        int, list[dict[str, Any]], list[dict[str, Any]],
        list[dict[str, Any]], list[dict[str, Any]]
    ]:
        combined = alg.merge_candidate_rankings(pools)
        reordered, hard_pruned, pruning_audit = rank_and_prune_candidates(
            combined, plan_history,
        )
        legal_unique = _selectable_unique_parameter_points(
            reordered, permanently_invalid_param_keys,
        )
        attempts.append({
            "candidate_step_fraction": fraction,
            "candidate_direction_mode": direction_mode,
            "combined_rows": len(combined),
            "hard_pruned_rows": len(hard_pruned),
            "legal_unique_parameter_points": legal_unique,
        })
        return legal_unique, combined, reordered, hard_pruned, pruning_audit

    legal_unique, combined, reordered, hard_pruned, pruning_audit = evaluate_pool(
        fraction=BASE_CANDIDATE_STEP_FRACTION, direction_mode="gradient",
    )
    if legal_unique < top_k:
        previous_unique = legal_unique
        for fraction in SUPPLEMENTARY_CANDIDATE_STEP_FRACTIONS:
            pools.append(list(supplementary_factory(fraction, "gradient")))
            applied_strategies.append({
                "step_fraction": fraction, "direction_mode": "gradient",
            })
            (
                legal_unique, combined, reordered, hard_pruned, pruning_audit,
            ) = evaluate_pool(
                fraction=fraction, direction_mode="gradient",
            )
            if legal_unique >= top_k:
                break
            if legal_unique == previous_unique:
                break
            previous_unique = legal_unique
    if legal_unique < top_k:
        for fraction in (
            BASE_CANDIDATE_STEP_FRACTION,
            *SUPPLEMENTARY_CANDIDATE_STEP_FRACTIONS,
        ):
            pools.append(list(supplementary_factory(
                fraction, "inward_if_boundary_collapsed",
            )))
            applied_strategies.append({
                "step_fraction": fraction,
                "direction_mode": "inward_if_boundary_collapsed",
            })
            (
                legal_unique, combined, reordered, hard_pruned, pruning_audit,
            ) = evaluate_pool(
                fraction=fraction,
                direction_mode="inward_if_boundary_collapsed",
            )
            if legal_unique >= top_k:
                break
    plan_unique: list[dict[str, Any]] = []
    seen_plans: set[str] = set()
    for raw in reordered:
        row = copy.deepcopy(dict(raw))
        plan = pg_outer.full_plan_fingerprint(row)
        if plan in seen_plans:
            continue
        seen_plans.add(plan)
        plan_unique.append(row)
    selected, replay_dedup = alg.select_unreplayed_candidates(
        plan_unique, replayed_param_keys, top_k=top_k,
        permanently_invalid_param_keys=permanently_invalid_param_keys,
    )
    replay_dedup["candidate_pool_supplementation_v1"] = {
        "reason": "exhaust_legal_search_before_accepting_partial_top_k",
        "base_step_fraction": BASE_CANDIDATE_STEP_FRACTION,
        "supplementary_strategies_applied": applied_strategies,
        "attempts": attempts,
        "top_k_limit": top_k,
        "top_k_is_upper_bound": True,
        "partial_top_k": len(selected) < top_k,
        "duplicate_plan_padding_allowed": False,
        "baseline_padding_allowed": False,
        "candidate_score_mutated": False,
        "qps_used_for_generation_resource_prediction_gradient_or_score": False,
        "hard_pruning_bypassed": False,
        "inward_fallback_only_for_boundary_collapsed_parameters": True,
        "real_replay_required": True,
    }
    return combined, selected, replay_dedup, hard_pruned, pruning_audit


def _single_candidate_live_representative(
    *, scene: str, workers: Mapping[str, Mapping[str, Any]],
) -> str:
    """Choose the deterministic frozen lane for a serialized candidate wave.

    XP assignments are reset from the sealed template before every replay, so
    their predecessor SQL liveness cannot be a precondition for selecting the
    reset target.  The reset path proves the exact container identity and
    either drains a live server or proves that no live mysqld exists.  JOB and
    TPC-H are not rebuilt here and therefore still require a live SQL lane.
    """
    scene_workers = sorted(
        worker_id for worker_id, worker in workers.items()
        if worker["scene"] == scene
    )
    if len(scene_workers) != INDEPENDENT_SCENE_WORKER_COUNT:
        raise RuntimeError(f"{scene} frozen worker cardinality differs")
    if scene in {"hybench_xp_cpu", "hybench_xp_io"}:
        return scene_workers[0]
    live = [
        worker_id for worker_id in scene_workers
        if alg.mysql_server_liveness(workers[worker_id])
    ]
    if not live:
        raise RuntimeError(f"{scene} has no live MySQL lane for candidate replay")
    return live[0]


def _build_single_candidate_assignments(
    *, rankings: Mapping[str, list[dict[str, Any]]],
    params_by_scene: Mapping[str, Mapping[str, float]],
    workers: Mapping[str, Mapping[str, Any]], rank: int,
) -> tuple[dict[str, dict[str, Any]], set[str]]:
    """Use exactly one live frozen lane per scene for this candidate."""
    assignments: dict[str, dict[str, Any]] = {}
    companion_workers: set[str] = set()
    for scene in SCENES:
        worker_id = _single_candidate_live_representative(
            scene=scene, workers=workers,
        )
        if scene in rankings:
            rows = rankings[scene]
            if rank < 1 or rank > len(rows):
                raise RuntimeError(f"{scene} candidate rank {rank} is absent")
            assignment = {
                "rank": rank,
                "params": copy.deepcopy(rows[rank - 1]["optimized_params"]),
                "candidate_physical_plan_sha256": (
                    rows[rank - 1].get("resource_model", {}).get(
                        "candidate_plan_sha256"
                    )
                ),
            }
        else:
            assignment = {
                "rank": 0,
                "params": copy.deepcopy(dict(params_by_scene[scene])),
            }
            companion_workers.add(worker_id)
        assignments[worker_id] = assignment
    return assignments, companion_workers


def _validate_pilot_authority() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Validate frozen identity while allowing one failed XP lane in a pilot."""
    freeze = alg.load(alg.FREEZE)
    prepared = alg.load(alg.r0mod.PREPARED)
    accounts = alg.load(alg.r0mod.BENCHMARK_ACCOUNTS)
    freeze_workers = {row["worker_id"]: row for row in freeze.get("workers", [])}
    prepared_workers = {row["worker_id"]: row for row in prepared.get("workers", [])}
    account_workers = {row["worker_id"]: row for row in accounts.get("workers", [])}
    if (
        freeze.get("schema_version") != alg.r0mod.FREEZE_SCHEMA
        or prepared.get("status") != "PREPARED"
        or prepared.get("freeze_sha256") != alg.sha(alg.FREEZE)
        or accounts.get("status") != "PASS"
        or accounts.get("freeze_sha256") != alg.sha(alg.FREEZE)
        or accounts.get("prepared_binding_sha256") != alg.sha(alg.r0mod.PREPARED)
        or set(freeze_workers) != set(alg.WORKER_IDS)
        or set(prepared_workers) != set(freeze_workers)
        or set(account_workers) != set(freeze_workers)
    ):
        raise RuntimeError("pilot v15 authority chain differs")
    workers: dict[str, dict[str, Any]] = {}
    unavailable: list[str] = []
    for worker_id, frozen in freeze_workers.items():
        adopted = prepared_workers[worker_id]
        account = account_workers[worker_id]
        if (
            account.get("container_id") != adopted.get("container_id")
            or account.get("database") != adopted.get("database")
            or account.get("benchmark_user") != "ra_benchmark"
            or account.get("ready") is not True
        ):
            raise RuntimeError(f"{worker_id} pilot account authority differs")
        merged = {**frozen, **adopted, **account}
        workers[worker_id] = merged
        try:
            inspected = alg.reset.prep._docker_json("inspect", frozen["container"])
        except Exception:
            inspected = {}
        if (
            inspected.get("Id") != adopted.get("container_id")
            or (inspected.get("State") or {}).get("Running") is not True
            or not alg.mysql_server_liveness(merged)
        ):
            unavailable.append(worker_id)
    if (
        len(unavailable) > 2
        or any(worker_id not in alg.XP_IDS for worker_id in unavailable)
        or any(
            not any(
                worker["scene"] == scene and worker_id not in unavailable
                for worker_id, worker in workers.items()
            )
            for scene in SCENES
        )
    ):
        raise RuntimeError(f"pilot live-lane authority differs: {unavailable}")
    return freeze, workers, {
        "captured_at": alg.now(),
        "passed": True,
        "pilot_only": True,
        "formal_evidence_eligible": False,
        "accepted_state_credit_prohibited": True,
        "unavailable_lanes": unavailable,
        "live_lane_required_per_scene": True,
    }


def _validate_wave_coordination_contract() -> dict[str, Any]:
    contract = alg.load(WAVE_COORDINATION_CONTRACT)
    bindings = _mapping(contract.get("source_bindings"), "coordination bindings")
    required_bindings = {
        "coordination_source": Path(coordination.__file__).resolve(),
        "boundary_resume_controller": Path(__file__).resolve(),
        "runtime_watchdog": ROOT / "scripts/watch_mysql_formal_external_postgres_interference_v1.py",
        "live_preflight": ROOT / "scripts/preflight_mysql_four_scene_live_v8.py",
        "auto_resume_waiter": ROOT / "scripts/wait_and_resume_mysql_algorithm_c_after_quiet_v1.py",
    }
    if (
        contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("lock_path") != str(coordination.LOCK_PATH)
        or contract.get("candidate_batches") != [[1, 2], [3, 4], [5]]
        or contract.get("job_tpch_concurrent_full_suite_preserved") is not True
        or contract.get("native_xp_workload_unchanged") is not True
        or float(contract.get("per_wave_quiet_seconds", 0)) < 15.0
        or float(contract.get("lease_acquisition_timeout_seconds", 0)) > 3.0
        or float(contract.get("lease_acquisition_timeout_seconds", 0)) <= 0.0
        or float(contract.get("maximum_quiet_wait_while_held_seconds", 0)) > 20.0
        or int(contract.get("maximum_gate_attempts_per_wave", 0)) != 1
        or int(contract.get("persistent_d_state_required_consecutive_samples", 0)) != 3
        or float(contract.get("maximum_wave_lease_seconds", 0)) > 1140.0
        or float(contract.get("maximum_wave_lease_seconds", 0)) < 900.0
        or float(contract.get("fair_release_yield_seconds", 0)) < 0.5
        or any(
            bindings.get(f"{label}_path") != str(path.relative_to(ROOT))
            or bindings.get(f"{label}_sha256") != alg.sha(path)
            for label, path in required_bindings.items()
        )
    ):
        raise RuntimeError("cross-project formal wave coordination authority differs")
    return contract


def _coordination_update(
    *, state_path: Path, state: dict[str, Any], phase: str, stage: str,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    state["stage"] = stage
    state["stage_updated_at"] = alg.now()
    row = dict(state.get("cross_project_wave_coordination") or {})
    row.update({"phase": phase, "updated_at": alg.now()})
    if extra:
        row.update(copy.deepcopy(dict(extra)))
    state["cross_project_wave_coordination"] = row
    alg.atomic(state_path, state)
    return state


def _independent_campaign_run_id(run_id: str) -> str:
    """Return the shared identity of the four independently scheduled lanes."""
    async_handoff_marker = "v36_to_c_per_scene_20260819"
    if async_handoff_marker in run_id:
        return async_handoff_marker
    for suffix in ("_xp_cpu", "_xp_io", "_tpch", "_job"):
        if run_id.endswith(suffix):
            campaign = run_id[:-len(suffix)]
            campaign = re.sub(r"_retry[0-9]+$", "", campaign)
            if campaign:
                return campaign
    raise RuntimeError("independent scene run id lacks a frozen scene suffix")


def _measure_batch_with_fair_coordination(
    *, freeze: Mapping[str, Any], workers: dict[str, dict[str, Any]],
    assignments: Mapping[str, Mapping[str, Any]], output: Path,
    reset_token: str, state_path: Path, state: dict[str, Any],
    stage_prefix: str, run_id: str, contract: Mapping[str, Any],
    interference_override_pilot: bool = False,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Run one fixed candidate wave under a bounded, fair exclusive lease."""

    if NO_LEASE_RUNTIME:
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        gate_path = output.parent / f"{output.name}_dynamic_noninterference_gate.json"
        if gate_path.exists() or gate_path.is_symlink():
            raise FileExistsError(f"fresh dynamic gate required: {gate_path}")
        state = _coordination_update(
            state_path=state_path, state=state,
            phase="dynamic_noninterference_gate",
            stage=f"{stage_prefix}_DYNAMIC_NONINTERFERENCE_GATE",
            extra={
                "batch_output": str(output),
                "lease_mode": "none",
                "shared_nvme_mutual_exclusion": False,
                "numa_exclusive_scheduler_lock": False,
                "whole_round_resource_lease": False,
                "other_scene_completion_required": False,
                "external_process_control_or_stop_performed": False,
            },
        )
        target_cpus = {
            cpu
            for worker in workers.values()
            for cpu in alg.r0mod._cpus(str(worker["database_cpuset"]))
            | alg.r0mod._cpus(str(worker["client_cpuset"]))
        }
        gate = coordination.rolling_quiet_gate(
            run_id=(
                _independent_campaign_run_id(run_id)
                if INDEPENDENT_SCENE_EXECUTION else run_id
            ),
            required_seconds=(
                15.0 if interference_override_pilot
                else float(contract["per_wave_quiet_seconds"])
            ),
            maximum_wait_seconds=(
                15.0 if interference_override_pilot
                else float(
                    contract.get(
                        "maximum_quiet_wait_seconds",
                        contract.get("maximum_quiet_wait_while_held_seconds", 60.0),
                    )
                )
            ),
            maximum_load1=float(contract.get("maximum_idle_load1", 512.0)),
            target_cpus=target_cpus,
        )
        gate.update({
            "run_identity_scope": "independent_mysql_scene",
            "run_identity": (
                _independent_campaign_run_id(run_id)
                if INDEPENDENT_SCENE_EXECUTION else run_id
            ),
            "lease_mode": "none",
            "external_process_control_or_stop_performed": False,
        })
        alg.atomic(gate_path, gate)
        if gate.get("status") != "PASS" and not interference_override_pilot:
            raise RuntimeError("dynamic non-interference gate failed closed")
        state = _coordination_update(
            state_path=state_path, state=state,
            phase="measuring", stage=f"{stage_prefix}_MEASURING",
            extra={
                "quiet_gate_path": str(gate_path),
                "quiet_gate_sha256": alg.sha(gate_path),
                "four_scene_wave_barrier": False,
                "other_scene_completion_required": False,
                "lease_mode": "none",
            },
        )
        measured = alg.measure_batch(
            freeze, workers, assignments, output, reset_token,
            state_path=state_path, stage_prefix=stage_prefix,
        )
        return measured, alg.load(state_path)

    acquisition_attempt = 0
    while True:
        acquisition_attempt += 1
        state = _coordination_update(
            state_path=state_path, state=state,
            phase="waiting_for_lease",
            stage=f"{stage_prefix}_WAITING_FOR_CROSS_PROJECT_WORKLOAD_LEASE",
            extra={
                "batch_output": str(output),
                "acquisition_attempt": acquisition_attempt,
                "formal_state_mutation_while_waiting": False,
            },
        )
        try:
            acquire = (
                coordination.acquire_shared_blocking
                if INDEPENDENT_SCENE_EXECUTION
                else coordination.acquire_exclusive_blocking
            )
            lease = acquire(
                timeout_seconds=(
                    900.0 if INDEPENDENT_SCENE_EXECUTION else
                    float(contract["lease_acquisition_timeout_seconds"])
                ),
            )
        except TimeoutError:
            state["cross_project_wave_coordination"]["lease_timeout_count"] = int(
                state["cross_project_wave_coordination"].get("lease_timeout_count", 0)
            ) + 1
            alg.atomic(state_path, state)
            if acquisition_attempt >= int(contract["maximum_gate_attempts_per_wave"]):
                raise RuntimeError(
                    "cross-project workload lease was not acquired within the "
                    "bounded pre-measurement window"
                )
            time.sleep(float(contract["fair_release_yield_seconds"]))
            continue

        # The physical measurement function owns ``output`` and deliberately
        # creates it without exist_ok so a stale/partial replay directory can
        # never be reused.  Keep coordination evidence beside that directory;
        # pre-creating ``output`` here would fail every otherwise-valid wave
        # before the first physical workload starts.
        output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        gate_path = output.parent / (
            f"{output.name}_cross_project_wave_quiet_gate_"
            f"attempt_{acquisition_attempt:03d}.json"
        )
        if gate_path.exists() or gate_path.is_symlink():
            raise FileExistsError(f"fresh coordination gate required: {gate_path}")
        state = _coordination_update(
            state_path=state_path, state=state,
            phase="lease_acquired",
            stage=f"{stage_prefix}_CROSS_PROJECT_LEASE_ACQUIRED",
            extra={
                "lease_holder_pid": os.getpid(),
                "lock_path": str(coordination.LOCK_PATH),
                "lease_mode": (
                    "shared_mysql_scene" if INDEPENDENT_SCENE_EXECUTION
                    else "exclusive_legacy_wave"
                ),
                "lock_inode": lease.inode,
                "lease_acquired_at": lease.acquired_at,
                "lease_wait_seconds": lease.wait_seconds,
            },
        )
        measured: dict[str, dict[str, Any]] | None = None
        gate: dict[str, Any] | None = None
        external_workload_wait = False
        release_error: BaseException | None = None
        try:
            state = _coordination_update(
                state_path=state_path, state=state,
                phase="quiet_gate",
                stage=f"{stage_prefix}_CROSS_PROJECT_QUIET_GATE",
            )
            quiet_gate_run_id = (
                _independent_campaign_run_id(run_id)
                if INDEPENDENT_SCENE_EXECUTION else run_id
            )
            gate = coordination.rolling_quiet_gate(
                run_id=quiet_gate_run_id,
                required_seconds=(
                    15.0 if interference_override_pilot
                    else float(contract["per_wave_quiet_seconds"])
                ),
                maximum_wait_seconds=(
                    15.0 if interference_override_pilot
                    else float(contract["maximum_quiet_wait_while_held_seconds"])
                ),
                maximum_load1=float(contract["maximum_idle_load1"]),
                target_cpus={
                    cpu
                    for worker in workers.values()
                    for cpu in alg.r0mod._cpus(str(worker["database_cpuset"]))
                    | alg.r0mod._cpus(str(worker["client_cpuset"]))
                } if INDEPENDENT_SCENE_EXECUTION else None,
            )
            gate["run_identity_scope"] = (
                "shared_independent_mysql_campaign"
                if INDEPENDENT_SCENE_EXECUTION else "single_controller"
            )
            gate["run_identity"] = quiet_gate_run_id
            alg.atomic(gate_path, gate)
            if gate.get("status") != "PASS" and not interference_override_pilot:
                if "external_database_physical_workload_present_while_exclusive" in (
                    gate.get("errors") or []
                ):
                    if not INDEPENDENT_SCENE_EXECUTION:
                        raise RuntimeError(
                            "external physical workload bypassed the exclusive wave lease"
                        )
                    external_workload_wait = True
                    gate["independent_scene_wait_for_external_quiet"] = True
                    gate["optimizer_or_measurement_state_mutated"] = False
                    alg.atomic(gate_path, gate)
            if gate.get("status") == "PASS" or interference_override_pilot:
                if interference_override_pilot and gate.get("status") != "PASS":
                    gate["interference_override_pilot"] = True
                    gate["formal_evidence_eligible"] = False
                    gate["accepted_state_credit_prohibited"] = True
                    alg.atomic(gate_path, gate)
                state = _coordination_update(
                    state_path=state_path, state=state,
                    phase="measuring",
                    stage=f"{stage_prefix}_COORDINATED_MEASUREMENT_HANDOFF",
                    extra={
                        "quiet_gate_path": str(gate_path),
                        "quiet_gate_sha256": alg.sha(gate_path),
                        "four_scene_wave_barrier": not INDEPENDENT_SCENE_EXECUTION,
                        "other_scene_completion_required": False,
                    },
                )
                measured = alg.measure_batch(
                    freeze, workers, assignments, output, reset_token,
                    state_path=state_path, stage_prefix=stage_prefix,
                )
        finally:
            try:
                if state_path.is_file():
                    state = alg.load(state_path)
                state = _coordination_update(
                    state_path=state_path, state=state,
                    phase="released",
                    stage=f"{stage_prefix}_CROSS_PROJECT_LEASE_RELEASED",
                    extra={
                        "lease_release_pending": True,
                        "last_gate_status": None if gate is None else gate.get("status"),
                    },
                )
            except BaseException as error:
                release_error = error
            held_seconds = coordination.release(lease)
            if release_error is None:
                state["cross_project_wave_coordination"].update({
                    "lease_release_pending": False,
                    "last_lease_held_seconds": held_seconds,
                    "last_released_at": alg.now(),
                })
                alg.atomic(state_path, state)
            time.sleep(float(contract["fair_release_yield_seconds"]))
            if release_error is not None:
                raise release_error
        if measured is not None:
            return measured, state
        if INDEPENDENT_SCENE_EXECUTION and external_workload_wait:
            state = _coordination_update(
                state_path=state_path, state=state,
                phase="waiting_for_external_quiet",
                stage=(
                    f"{stage_prefix}_WAITING_FOR_EXTERNAL_CROSS_PROJECT_QUIET"
                ),
                extra={
                    "independent_scene_retry": True,
                    "other_mysql_scene_completion_required": False,
                    "optimizer_or_measurement_state_mutated": False,
                    "retry_seconds": 30.0,
                },
            )
            time.sleep(30.0)
            continue
        # Host pressure did not settle while the bounded lease was held.  No
        # workload or optimizer state changed; fail promptly instead of
        # re-entering an unbounded pre-measurement retry loop.
        if acquisition_attempt >= int(contract["maximum_gate_attempts_per_wave"]):
            raise RuntimeError(
                "cross-project quiet gate exhausted its single bounded attempt"
            )


def _validate_lineage(
    checkpoint: Mapping[str, Any], runtime_preflight: Path,
    prepared_binding: Path, benchmark_accounts: Path,
) -> None:
    preflight = alg.load(runtime_preflight)
    prepared = alg.load(prepared_binding)
    accounts = alg.load(benchmark_accounts)
    if (
        preflight.get("status") != "PASS"
        or float(preflight.get("observed_seconds", 0.0)) < 60.0
        or preflight.get("formal_launch_authorized") is not True
        or preflight.get("freeze_sha256") != checkpoint.get("freeze_sha256")
        or any((preflight.get("accepted_rounds") or {}).values())
        or prepared.get("status") != "PREPARED"
        or prepared.get("freeze_sha256") != checkpoint.get("freeze_sha256")
        or prepared.get("preflight_sha256") != alg.sha(runtime_preflight)
        or prepared.get("workload_started") is not False
        or prepared.get("fresh_r0_complete") is not False
        or prepared.get("formal_outer_started") is not False
        or len(prepared.get("workers") or []) != len(alg.WORKER_IDS)
        or accounts.get("status") != "PASS"
        or accounts.get("prepared_binding_sha256") != alg.sha(prepared_binding)
        or accounts.get("freeze_sha256") != checkpoint.get("freeze_sha256")
        or accounts.get("passwords_emitted") is not False
        or accounts.get("workload_started") is not False
        or len(accounts.get("workers") or []) != len(alg.WORKER_IDS)
    ):
        raise RuntimeError("boundary-resume live lineage differs")


def _validate_fresh_r0(
    path: Path, checkpoint: Mapping[str, Any], runtime_preflight: Path,
    prepared_binding: Path, benchmark_accounts: Path, *, pilot: bool = False,
) -> dict[str, Any]:
    value = alg.load(path)
    if pilot:
        if (
            value.get("status") != "R0_INVALIDATED_FAIL_CLOSED"
            or value.get("formal_result_credit_prohibited") is not True
            or value.get("formal_outer_launch_prohibited") is not True
            or value.get("external_process_mutation") is not False
            or value.get("docker_mutation") is not False
        ):
            raise RuntimeError("pilot R0 invalidation authority differs")
        return value
    if (
        value.get("status") != "PASS"
        or value.get("fresh_r0_complete") is not True
        or value.get("formal_outer_launch_authorized") is not True
        or value.get("formal_outer_started") is not False
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("freeze_sha256") != checkpoint.get("freeze_sha256")
        or value.get("preflight_sha256") != alg.sha(runtime_preflight)
        or value.get("prepared_binding_sha256") != alg.sha(prepared_binding)
        or value.get("benchmark_accounts_sha256") != alg.sha(benchmark_accounts)
        or any((value.get("accepted_rounds") or {}).values())
        or (value.get("target_bottleneck_gate") or {}).get("passed") is not True
        or value.get("failures") not in ([], None)
        or value.get("client_isolation_failures") not in ([], None)
    ):
        raise RuntimeError("fresh v18 R0 authority differs")
    return value


def _validate_replay_quarantine(
    path: Path, checkpoint: Mapping[str, Any],
) -> dict[str, dict[str, list[str]]]:
    value = alg.load(path)
    scene_keys = _scene_mapping(
        value.get("quarantined_param_keys_by_scene"),
        "replay quarantine keys",
    )
    scene_rows = _scene_mapping(
        value.get("rows_by_scene"), "replay quarantine rows",
    )
    soft_keys = _scene_mapping(
        value.get("soft_replayed_param_keys_by_scene"),
        "soft replay quarantine keys",
    )
    permanent_keys = _scene_mapping(
        value.get("permanent_invalid_param_keys_by_scene"),
        "permanent invalid replay quarantine keys",
    )
    quarantine_counts = _scene_mapping(
        value.get("quarantined_counts_by_scene"),
        "replay quarantine counts",
    )
    soft_counts = _scene_mapping(
        value.get("soft_replayed_counts_by_scene"),
        "soft replay quarantine counts",
    )
    permanent_counts = _scene_mapping(
        value.get("permanent_invalid_counts_by_scene"),
        "permanent invalid replay quarantine counts",
    )
    evidence = _mapping(value.get("evidence"), "replay quarantine evidence")
    if (
        value.get("schema_version") != "mysql_algorithm_c_replay_quarantine_v6"
        or value.get("status") != "PASS"
        or value.get("formal_scope") != "mysql_only"
        or value.get("cross_engine_comparison_prohibited") is not True
        or value.get("freeze_sha256") != checkpoint.get("freeze_sha256")
        or value.get("resume_checkpoint_outer_attempt")
        != checkpoint.get("completed_outer_attempts")
        or value.get("formal_state_mutation") is not False
        or value.get("accepted_rounds_mutation") is not False
        or value.get("rejected_rounds_mutation") is not False
        or value.get("interval_convergence_mutation") is not False
        or value.get("cycle_reset_may_clear_soft_replayed") is not True
        or value.get("cycle_reset_may_clear_permanent_invalid") is not False
        or any(
            not isinstance(scene_keys[scene], list)
            or int(quarantine_counts[scene]) < 0
            or len(scene_keys[scene]) != int(quarantine_counts[scene])
            or len(set(scene_keys[scene])) != int(quarantine_counts[scene])
            or not isinstance(scene_rows[scene], list)
            or len(scene_rows[scene]) != int(quarantine_counts[scene])
            or [row.get("optimizer_params_key") for row in scene_rows[scene]]
            != scene_keys[scene]
            or any(row.get("formal_state_mutation") is not False for row in scene_rows[scene])
            or not isinstance(soft_keys[scene], list)
            or int(soft_counts[scene]) < 0
            or len(soft_keys[scene]) != int(soft_counts[scene])
            or len(set(soft_keys[scene])) != int(soft_counts[scene])
            or not isinstance(permanent_keys[scene], list)
            or int(permanent_counts[scene]) < 0
            or len(permanent_keys[scene]) != int(permanent_counts[scene])
            or len(set(permanent_keys[scene])) != int(permanent_counts[scene])
            or int(soft_counts[scene]) + int(permanent_counts[scene])
            != int(quarantine_counts[scene])
            or set(soft_keys[scene]).intersection(permanent_keys[scene])
            or set(soft_keys[scene]) | set(permanent_keys[scene])
            != set(scene_keys[scene])
            for scene in SCENES
        )
    ):
        raise RuntimeError("replay quarantine authority differs")
    for binding in evidence.values():
        row = _mapping(binding, "replay quarantine evidence binding")
        bound = Path(str(row.get("path") or "")).resolve()
        if (
            not bound.is_file() or bound.is_symlink()
            or alg.sha(bound) != row.get("sha256")
        ):
            raise RuntimeError(f"replay quarantine evidence differs: {bound}")
    return {
        "combined": {scene: list(scene_keys[scene]) for scene in SCENES},
        "soft": {scene: list(soft_keys[scene]) for scene in SCENES},
        "permanent": {scene: list(permanent_keys[scene]) for scene in SCENES},
    }


def classify_technical_timeout_batch(
    document: Mapping[str, Any], assignments: Mapping[str, Mapping[str, Any]],
    *, batch_result_path: Path | None = None,
) -> dict[str, Any] | None:
    """Recognize candidate-local workload timeout or XP drain failures.

    PostgreSQL's outer controller retains valid siblings in a physical wave and
    excludes only the failed validation.  MySQL workload timeouts already had
    that semantic classification, but an XP adaptive stop could exit 2 after a
    successful measurement when graceful client-operation drain timed out.  The
    latter is technical only when the immutable worker logs, final metrics, and
    stability decision all prove that exact post-measurement condition.

    Any other failure shape remains fail-closed.
    """
    failures = document.get("failures")
    measurements = document.get("measurements")
    if (
        document.get("status") != "FAILED_CLOSED"
        or not isinstance(failures, list)
        or not failures
        or not isinstance(measurements, Mapping)
        or set(measurements) != set(assignments)
    ):
        return None
    workload_tasks = set(alg.XP_IDS).intersection(assignments)
    for pair in (1, 2):
        if (
            f"job_w{pair}" in assignments
            and f"tpch_w{pair}" in assignments
        ):
            workload_tasks.add(f"pair_{pair}")
    timed_out_tasks: list[str] = []
    terminal_drain_tasks: list[str] = []
    derived_plan_skips: list[str] = []
    for failure in failures:
        if not isinstance(failure, Mapping):
            return None
        task = str(failure.get("task") or "")
        error = str(failure.get("error") or "")
        if task in workload_tasks and error.startswith("TimeoutExpired:"):
            timed_out_tasks.append(task)
        elif (
            task in set(alg.XP_IDS).intersection(assignments)
            and error.startswith("RuntimeError: HyBench exited 2; see ")
        ):
            if batch_result_path is None:
                return None
            worker_root = batch_result_path.parent / task
            stderr_path = worker_root / "stderr.log"
            stdout_path = worker_root / "stdout.log"
            stability_path = worker_root / "resource_stability.json"
            if (
                not stderr_path.is_file()
                or stderr_path.is_symlink()
                or not stdout_path.is_file()
                or stdout_path.is_symlink()
                or not stability_path.is_file()
                or stability_path.is_symlink()
            ):
                return None
            stderr_text = stderr_path.read_text(
                encoding="utf-8", errors="replace",
            )
            stdout_text = stdout_path.read_text(
                encoding="utf-8", errors="replace",
            )
            try:
                stability = alg.load(stability_path)
            except (OSError, ValueError, json.JSONDecodeError):
                return None
            selected_seconds = float(
                stability.get("selected_measurement_seconds") or 0.0
            )
            if not (
                "java.lang.IllegalStateException: client operation drain timed out"
                in stderr_text
                and '"workload_error":"IllegalStateException"' in stdout_text
                and '"stop_requested":true' in stdout_text
                and '"forced_boundary_stop":false' in stdout_text
                and stability.get("schema_version")
                == "mysql_xp_adaptive_stop_v1"
                and stability.get("stable") is True
                and stability.get("stop_request_published") is True
                and 300.0 <= selected_seconds <= 900.0
                and float(stability.get("maximum_measurement_seconds") or 0.0)
                == 900.0
            ):
                return None
            terminal_drain_tasks.append(task)
        elif task.endswith("_plan_capture") and error == "skipped_after_invalid_workload":
            derived_plan_skips.append(task)
        else:
            return None
    if not timed_out_tasks and not terminal_drain_tasks:
        return None

    direct_failed_worker_ids: set[str] = set(terminal_drain_tasks)
    for task in timed_out_tasks:
        if task in assignments:
            direct_failed_worker_ids.add(task)
        elif task.startswith("pair_"):
            pair = task.removeprefix("pair_")
            direct_failed_worker_ids.update(
                worker_id for worker_id in assignments
                if worker_id in {f"job_w{pair}", f"tpch_w{pair}"}
            )
    if not direct_failed_worker_ids:
        return None
    invalid_worker_ids = {
        worker_id for worker_id, row in measurements.items()
        if not (
            isinstance(row, Mapping)
            and row.get("hard_gate_passed") is True
            and row.get("coverage_complete") is True
        )
    }
    if not direct_failed_worker_ids.issubset(invalid_worker_ids):
        return None
    failed_worker_ids = invalid_worker_ids
    derived_unavailable_worker_ids = (
        invalid_worker_ids - direct_failed_worker_ids
    )

    classification = (
        "TECHNICAL_TIMEOUT_EXCLUDED"
        if not terminal_drain_tasks
        else "TECHNICAL_CANDIDATE_TERMINAL_EXCLUDED"
        if not timed_out_tasks
        else "TECHNICAL_TIMEOUT_OR_CANDIDATE_TERMINAL_EXCLUDED"
    )
    return {
        "classification": classification,
        "timed_out_tasks": sorted(timed_out_tasks),
        "terminal_drain_tasks": sorted(terminal_drain_tasks),
        "direct_failed_worker_ids": sorted(direct_failed_worker_ids),
        "derived_unavailable_worker_ids": sorted(
            derived_unavailable_worker_ids
        ),
        "failed_worker_ids": sorted(failed_worker_ids),
        "derived_plan_capture_skips": sorted(derived_plan_skips),
        "candidate_state_mutation": False,
        "accepted_state_credit_increment": 0,
        "rejected_outer_attempt_increment": 0,
        "acceptance_tier_mutation": False,
        "convergence_mutation": False,
        "replay_history_mutation": False,
        "pruning_evidence_eligible": False,
    }


def classify_premeasurement_reset_timeout(
    error: BaseException, *, batch_result_path: Path,
) -> dict[str, Any] | None:
    """Isolate only the bounded XP pre-reset active-session probe timeout."""
    if not isinstance(error, subprocess.TimeoutExpired):
        return None
    command = error.cmd
    if not isinstance(command, (list, tuple)):
        return None
    argv = [str(item) for item in command]
    if (
        batch_result_path.exists()
        or not argv
        or Path(argv[0]).name != "mysql"
        or not any(item.startswith("--port=653") for item in argv)
        or "SELECT COUNT(*) FROM information_schema.processlist "
        "WHERE ID<>CONNECTION_ID() AND COMMAND<>'Daemon' "
        "AND USER<>'event_scheduler'" not in argv
        or float(error.timeout or 0) > 15.0
    ):
        return None
    return {
        "classification": "TECHNICAL_PREMEASUREMENT_RESET_TIMEOUT_EXCLUDED",
        "timed_out_command_executable": Path(argv[0]).name,
        "timeout_seconds": float(error.timeout),
        "physical_workload_started": False,
        "candidate_state_mutation": False,
        "accepted_state_credit_increment": 0,
        "rejected_outer_attempt_increment": 0,
        "acceptance_tier_mutation": False,
        "convergence_mutation": False,
        "replay_history_mutation": False,
        "pruning_evidence_eligible": False,
    }


def _replay_pg_expansion_waves(
    *,
    batch_kind: str,
    candidate_sets: Mapping[str, list[dict[str, Any]]],
    params_by_scene: Mapping[str, Mapping[str, float]],
    workers: Mapping[str, Mapping[str, Any]],
    freeze: Mapping[str, Any],
    output: Path,
    outer_attempt: int,
    state_path: Path,
    state: dict[str, Any],
    incumbents: Mapping[str, Mapping[str, Any]],
    canonical_baselines: Mapping[str, Mapping[str, Any]],
    run_id: str,
    coordination_contract: Mapping[str, Any],
    interference_override_pilot: bool,
) -> tuple[
    dict[str, list[dict[str, Any]]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, Any],
]:
    """Replay one complete PG expansion Top-K as physical [2,2,1] waves.

    PostgreSQL evaluates the semantic batch only after every admitted point in
    that batch has been replayed.  A point accepted in wave one therefore does
    not suppress waves two and three; it only wins (or loses) when the complete
    batch is ordered by the shared tier/target/QPS/I/O key.
    """
    evaluations = {scene: [] for scene in candidate_sets}
    companion_audit: list[dict[str, Any]] = []
    technical_exclusions: list[dict[str, Any]] = []
    accepted_by_scene: dict[str, dict[str, Any]] = {}
    for batch_index, ranks in enumerate(FORMAL_CANDIDATE_BATCHES, 1):
        rankings_for_wave = {
            scene: rows
            for scene, rows in candidate_sets.items()
            if any(rank <= len(rows) for rank in ranks)
        }
        assignments, companion_workers = predecessor.build_batch_assignments(
            rankings=rankings_for_wave,
            params_by_scene=params_by_scene,
            workers=workers,
            ranks=ranks,
        )
        batch_root = (
            output / f"outer_{outer_attempt:03d}"
            / batch_kind / f"wave_{batch_index}"
        )
        batch_path = batch_root / "batch_result.json"
        try:
            measured, state = _measure_batch_with_fair_coordination(
                freeze=freeze,
                workers=workers,
                assignments=assignments,
                output=batch_root,
                reset_token=(
                    f"outer_{outer_attempt:03d}_{batch_kind}_{batch_index}"
                ),
                state_path=state_path,
                state=state,
                stage_prefix=(
                    f"OUTER_{outer_attempt:03d}_{batch_kind.upper()}_"
                    f"WAVE_{batch_index}"
                ),
                run_id=run_id,
                contract=coordination_contract,
                interference_override_pilot=interference_override_pilot,
            )
        except BaseException as error:
            technical = classify_premeasurement_reset_timeout(
                error, batch_result_path=batch_path,
            )
            if technical is None:
                raise
            technical_exclusions.append({
                **technical,
                "batch_kind": batch_kind,
                "batch_index": batch_index,
                "candidate_ranks": list(ranks),
                "error": f"{type(error).__name__}: {error}",
            })
            continue
        document = alg.load(batch_path)
        if (
            document.get("status") != "PASS"
            or document.get("failures") not in ([], None)
            or len(document.get("measurements") or {}) != len(assignments)
        ):
            technical = classify_technical_timeout_batch(document, assignments)
            if technical is None:
                raise RuntimeError(
                    f"outer {outer_attempt} {batch_kind} wave {batch_index} "
                    "failed closed"
                )
            technical_exclusions.append({
                **technical,
                "batch_kind": batch_kind,
                "batch_index": batch_index,
                "candidate_ranks": list(ranks),
                "batch_result_path": str(batch_path),
                "batch_result_sha256": alg.sha(batch_path),
            })
            continue
        for worker_id, row in measured.items():
            scene = str(row["scene"])
            if worker_id in companion_workers:
                companion_audit.append({
                    "batch_kind": batch_kind,
                    "batch_index": batch_index,
                    "worker_id": worker_id,
                    "scene": scene,
                    "hard_gate_passed": row.get("hard_gate_passed"),
                    "coverage_complete": row.get("coverage_complete"),
                })
                continue
            row["candidate_ranking"] = copy.deepcopy(
                candidate_sets[scene][int(row["rank"]) - 1]
            )
            evaluations[scene].append(row)
    for scene, rows in evaluations.items():
        accepted, _valid = pg_outer.evaluate_batch(
            rows,
            incumbents[scene],
            canonical_baseline=canonical_baselines[scene],
            target=alg.TARGET[scene],
        )
        if accepted is not None:
            accepted_by_scene[scene] = {
                "batch_kind": batch_kind,
                "wave_ranks": [1, 2, 3, 4, 5],
                "selected_rank": int(accepted["row"]["rank"]),
                "selected_param_signature": alg.optimizer_params_key(
                    accepted["row"]["params"]
                ),
                "semantic_batch_evaluated_after_all_physical_waves": True,
            }
    return (
        evaluations,
        companion_audit,
        technical_exclusions,
        accepted_by_scene,
        state,
    )


def _state_document(
    *, args: argparse.Namespace, checkpoint_path: Path,
    checkpoint: Mapping[str, Any], reconstructed: Mapping[str, Any],
) -> dict[str, Any]:
    completed = (
        0 if args.fresh_search
        else int(checkpoint["completed_outer_attempts"])
    )
    baseline = _mapping(
        _mapping(checkpoint.get("evidence"), "checkpoint evidence").get("baseline"),
        "checkpoint baseline",
    )
    pilot = bool(args.interference_override_pilot)
    plan_replay_history = _scene_mapping(
        reconstructed.get("full_plan_replay_history_v2"),
        "checkpoint full plan replay history v2",
    )
    if any(not isinstance(plan_replay_history[scene], list) for scene in SCENES):
        raise RuntimeError("checkpoint full plan replay history rows differ")
    document = {
        "schema_version": STATE_SCHEMA,
        "algorithm": "C",
        "status": "RUNNING",
        "stage": "VALIDATING_BOUNDARY_RESUME_AUTHORITY",
        "started_at": alg.now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "execution_class": (
            "INTERFERENCE_OVERRIDE_PILOT"
            if pilot else "FORMAL_MYSQL_ONLY"
        ),
        "formal_evidence_eligible": not pilot,
        "accepted_state_credit_prohibited": pilot,
        "active_scenes": list(SCENES),
        "async_scene": SCENES[0] if INDEPENDENT_SCENE_EXECUTION else None,
        "four_scene_wave_barrier": not INDEPENDENT_SCENE_EXECUTION,
        "other_scene_completion_required": False,
        "per_scene_outer_attempt_counter": INDEPENDENT_SCENE_EXECUTION,
        "scene_may_advance_immediately_after_own_round": (
            INDEPENDENT_SCENE_EXECUTION
        ),
        "completed_scene_companion_workload": False,
        "postgresql_cohort_barrier_compatibility": False,
        "pid": os.getpid(),
        "outer_attempt": completed,
        "completed_predecessor_outer_attempts": completed,
        "accepted_rounds": copy.deepcopy(reconstructed["accepted_rounds"]),
        "rejected_outer_attempts": copy.deepcopy(
            reconstructed["rejected_outer_attempts"]
        ),
        "acceptance_tier": copy.deepcopy(reconstructed["acceptance_tier"]),
        "consecutive_failed_rounds": copy.deepcopy(
            reconstructed["consecutive_failed_rounds"]
        ),
        "acceptance_tier_downgrade_after_rounds": None,
        "converged": copy.deepcopy(reconstructed["converged"]),
        "interval_stable_rounds": copy.deepcopy(
            reconstructed["interval_stable_rounds"]
        ),
        "stable_intervals": copy.deepcopy(reconstructed["stable_intervals"]),
        "full_plan_replay_history_v2": copy.deepcopy(plan_replay_history),
        "valid_replay_archive_by_scene": {
            scene: [] for scene in SCENES
        },
        "previously_accepted_parameter_signatures": {
            scene: [] for scene in SCENES
        },
        "convergence_rule": {
            "metric": "maximum_log_domain_width_relative_to_frozen_initial",
            "maximum_interval_ratio": 0.01,
            "maximum_relative_width": 0.01,
            "required_consecutive_stable_accepted_rounds": 0,
            "rejected_outer_round_resets_stability_streak": False,
            "all_active_parameters_must_satisfy_interval": True,
            "accepted_target_is_immediately_terminal": False,
            "post_search_stability_confirmation": False,
            "two_consecutive_rejections_shrink_around_incumbent": True,
            "forced_shrink_grants_accepted_credit": False,
        },
        "lane_paired_incumbent_revalidation": {
            "schema_version": LANE_PAIRED_REVALIDATION_SCHEMA,
            "applies_to": list(SCENES),
            "trigger_on_apparent_acceptance": True,
            "trigger_before_second_rejection_shrink": True,
            "same_worker_candidate_and_incumbent_ratio": True,
            "raw_db_qps_preserved": True,
            "decision_qps": "global_incumbent_times_same_lane_qps_ratio",
            "control_measurements_grant_no_state_credit": True,
        },
        "attempt_budget": {
            "target_accepted_rounds_per_scene": TARGET_ACCEPTED_ROUNDS,
            "maximum_rejected_outer_attempts_per_scene": None,
            "maximum_outer_attempts": MAXIMUM_OUTER_ATTEMPTS,
            "accepted_and_rejected_budgets_are_separate": False,
        },
        "real_top_k_limit": 5,
        "top_k_is_upper_bound": True,
        "partial_top_k_allowed": True,
        "duplicate_plan_padding_allowed": False,
        "baseline_padding_allowed": False,
        "candidate_batches": [[1, 2], [3, 4], [5]],
        "post_search_stability_confirmation": False,
        "fresh_search": bool(args.fresh_search),
        "converged_scene_execution": (
            "scene_controller_terminates_without_companion"
            if INDEPENDENT_SCENE_EXECUTION
            else "incumbent_companion_full_workload"
        ),
        "checkpoint_path": str(checkpoint_path),
        "checkpoint_sha256": alg.sha(checkpoint_path),
        "baseline_path": str(baseline["path"]),
        "baseline_sha256": str(baseline["sha256"]),
        "runtime_preflight_path": str(args.runtime_preflight),
        "runtime_preflight_sha256": alg.sha(args.runtime_preflight),
        "prepared_binding_path": str(args.prepared_binding),
        "prepared_binding_sha256": alg.sha(args.prepared_binding),
        "benchmark_accounts_path": str(args.benchmark_accounts),
        "benchmark_accounts_sha256": alg.sha(args.benchmark_accounts),
        "fresh_r0_path": str(args.fresh_r0),
        "fresh_r0_sha256": alg.sha(args.fresh_r0),
        "replay_quarantine_path": str(args.replay_quarantine),
        "replay_quarantine_sha256": alg.sha(args.replay_quarantine),
        "freeze_path": str(alg.FREEZE),
        "freeze_sha256": alg.sha(alg.FREEZE),
        "resource_model_contract_path": str(alg.RESOURCE_MODEL_CONTRACT),
        "resource_model_contract_sha256": alg.sha(alg.RESOURCE_MODEL_CONTRACT),
        "resource_model_source_sha256": alg.sha(alg.RESOURCE_MODEL_SOURCE),
        "optimization_contract_path": str(OPTIMIZATION_CONTRACT),
        "optimization_contract_sha256": alg.sha(OPTIMIZATION_CONTRACT),
        "wave_coordination_contract_path": str(WAVE_COORDINATION_CONTRACT),
        "wave_coordination_contract_sha256": alg.sha(WAVE_COORDINATION_CONTRACT),
        "cross_project_wave_coordination": {
            "contract": "mysql_cross_project_fair_candidate_wave_lease_v1",
            "phase": "not_acquired",
            "lock_path": str(coordination.LOCK_PATH),
            "idle_external_controllers_are_inventory_not_workload": True,
            "active_external_physical_workloads_remain_fail_closed": True,
            "fixed_candidate_batches": [[1, 2], [3, 4], [5]],
        },
        "reset_source_path": str(Path(alg.reset.__file__).resolve()),
        "reset_source_sha256": alg.sha(Path(alg.reset.__file__).resolve()),
        "algorithm_source_path": str(Path(alg.__file__).resolve()),
        "algorithm_source_sha256": alg.sha(Path(alg.__file__).resolve()),
        "controller_source_path": str(Path(__file__).resolve()),
        "controller_source_sha256": alg.sha(Path(__file__).resolve()),
        "formal_outer_started": not pilot,
    }
    if pilot:
        document.update({
            "pilot_maximum_outer_attempts": 1,
            "pilot_diagnostic_acceptances": {scene: 0 for scene in SCENES},
            "pilot_diagnostic_convergence": {},
            "interference_evidence_path": str(args.interference_evidence),
            "interference_evidence_sha256": alg.sha(args.interference_evidence),
        })
    return document


def _measure_lane_incumbents(
    *,
    scene: str,
    incumbent_params: Mapping[str, float],
    freeze: Mapping[str, Any],
    workers: dict[str, dict[str, Any]],
    output: Path,
    outer_attempt: int,
    state_path: Path,
    state: dict[str, Any],
    run_id: str,
    coordination_contract: Mapping[str, Any],
    interference_override_pilot: bool,
) -> tuple[dict[str, dict[str, Any]], dict[str, Any], Path]:
    """Replay the incumbent once on every lane used by ``scene``.

    These rows are controls only: they never receive candidate rank, accepted
    credit, rejection credit, or convergence credit.  Each candidate is later
    divided by the control from its own worker, so physical-lane throughput and
    worker-local optimizer-plan differences cannot masquerade as parameter
    progress.
    """
    scene_workers = sorted(
        worker_id for worker_id, worker in workers.items()
        if worker.get("scene") == scene
    )
    if not scene_workers:
        raise RuntimeError(f"{scene} lane revalidation has no physical worker")
    assignments = {
        worker_id: {
            "rank": 0,
            "params": copy.deepcopy(dict(incumbent_params)),
            "lane_paired_incumbent_control": True,
        }
        for worker_id in scene_workers
    }
    root = (
        output / f"outer_{outer_attempt:03d}"
        / "lane_paired_incumbent_revalidation"
    )
    measured, state = _measure_batch_with_fair_coordination(
        freeze=freeze,
        workers=workers,
        assignments=assignments,
        output=root,
        reset_token=f"outer_{outer_attempt:03d}_lane_incumbent_revalidation",
        state_path=state_path,
        state=state,
        stage_prefix=(
            f"OUTER_{outer_attempt:03d}_LANE_INCUMBENT_REVALIDATION"
        ),
        run_id=run_id,
        contract=coordination_contract,
        interference_override_pilot=interference_override_pilot,
    )
    batch_path = root / "batch_result.json"
    document = alg.load(batch_path)
    if (
        document.get("status") != "PASS"
        or document.get("failures") not in ([], None)
        or set(measured) != set(scene_workers)
    ):
        raise RuntimeError(f"{scene} lane incumbent revalidation failed closed")
    incumbent_key = alg.optimizer_params_key(incumbent_params)
    for worker_id, row in measured.items():
        if (
            row.get("scene") != scene
            or row.get("hard_gate_passed") is not True
            or row.get("coverage_complete") is not True
            or alg.optimizer_params_key(row.get("params") or {}) != incumbent_key
            or not math.isfinite(float(row.get("db_qps") or math.nan))
            or float(row.get("db_qps") or 0.0) <= 0.0
        ):
            raise RuntimeError(
                f"{scene} invalid lane incumbent control: {worker_id}"
            )
        row["lane_paired_incumbent_control"] = True
        row["accepted_state_credit_increment"] = 0
        row["rejected_outer_attempt_increment"] = 0
        row["convergence_mutation"] = False
    return measured, state, batch_path


def _lane_normalized_candidate(
    *,
    row: dict[str, Any],
    lane_incumbent: Mapping[str, Any],
    global_incumbent_qps: float,
) -> dict[str, Any]:
    """Attach raw/paired evidence and return a decision-normalized row."""
    raw_qps = float(row.get("raw_db_qps", row["db_qps"]))
    lane_qps = float(lane_incumbent["db_qps"])
    if not all(map(math.isfinite, (raw_qps, lane_qps, global_incumbent_qps))):
        raise RuntimeError("lane-paired QPS must be finite")
    if raw_qps <= 0.0 or lane_qps <= 0.0 or global_incumbent_qps <= 0.0:
        raise RuntimeError("lane-paired QPS must be positive")
    gain = raw_qps / lane_qps - 1.0
    normalized = global_incumbent_qps * (1.0 + gain)
    row["raw_db_qps"] = raw_qps
    row["lane_incumbent_db_qps"] = lane_qps
    row["lane_paired_qps_gain"] = gain
    row["lane_normalized_db_qps"] = normalized
    row["db_qps"] = normalized
    return row


def main() -> int:
    global SCENES, INDEPENDENT_SCENE_EXECUTION
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--checkpoint", type=_path, required=True)
    parser.add_argument("--runtime-preflight", type=_path, required=True)
    parser.add_argument("--prepared-binding", type=_path, required=True)
    parser.add_argument("--benchmark-accounts", type=_path, required=True)
    parser.add_argument("--fresh-r0", type=_path, required=True)
    parser.add_argument("--replay-quarantine", type=_path, required=True)
    parser.add_argument("--interference-override-pilot", action="store_true")
    parser.add_argument("--interference-evidence", type=_path)
    parser.add_argument("--pilot-start-rank", type=int, default=1)
    parser.add_argument("--pilot-baseline", type=_path)
    parser.add_argument("--formal-partial-root", type=_directory)
    parser.add_argument("--formal-start-rank", type=int, default=1)
    parser.add_argument("--formal-completed-rank5-root", type=_directory)
    parser.add_argument("--fresh-search", action="store_true")
    parser.add_argument("--scene", choices=FULL_SCENES)
    parser.add_argument("--scene-start-barrier", type=Path)
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,160}", args.run_id) is None:
        parser.error("unsafe run id")
    if (args.scene is None) != (args.scene_start_barrier is None):
        parser.error("scene and scene-start-barrier must be supplied together")
    if args.scene is not None:
        SCENES = (args.scene,)
        predecessor.SCENES = SCENES
        INDEPENDENT_SCENE_EXECUTION = True
    if args.fresh_search and (
        args.interference_override_pilot
        or args.formal_partial_root is not None
        or args.formal_start_rank != 1
        or args.formal_completed_rank5_root is not None
    ):
        parser.error("fresh search cannot inherit pilot or partial outer state")
    if args.interference_override_pilot:
        if args.formal_partial_root is not None or args.formal_start_rank != 1:
            parser.error("formal partial continuation is incompatible with pilot mode")
        if "pilot_nonformal" not in args.run_id or args.interference_evidence is None:
            parser.error("pilot run id and interference evidence are required")
        interference = alg.load(args.interference_evidence)
        if (
            interference.get("status") != "BLOCKED_FAIL_CLOSED"
            or interference.get("formal_launch_authorized") is not False
            or interference.get("formal_scope") != "mysql_only"
            or interference.get("cross_engine_comparison_prohibited") is not True
            or not (interference.get("blockers") or [])
        ):
            parser.error("blocked live interference evidence required for pilot")
        if args.pilot_start_rank not in range(1, 6):
            parser.error("pilot start rank must be 1..5")
        if args.pilot_start_rank > 1 and args.pilot_baseline is None:
            parser.error("pilot continuation requires a passed pilot baseline")
    elif args.interference_evidence is not None:
        parser.error("interference evidence is pilot-only")
    elif args.pilot_start_rank != 1 or args.pilot_baseline is not None:
        parser.error("pilot continuation arguments are pilot-only")
    elif args.formal_partial_root is None and args.formal_start_rank != 1:
        parser.error("formal partial root is required when formal start rank is above 1")
    elif args.formal_partial_root is not None and args.formal_start_rank not in range(2, 6):
        parser.error("formal partial continuation start rank must be 2..5")
    elif args.formal_completed_rank5_root is not None and (
        args.formal_partial_root is None or args.formal_start_rank != 5
    ):
        parser.error("completed rank 5 recovery requires partial root and start rank 5")
    output = ROOT / "history/mysql_four_scene_20260809" / args.run_id
    if output.exists():
        raise RuntimeError(f"fresh formal output exists: {output}")
    optimization = alg.load(OPTIMIZATION_CONTRACT)
    coordination_contract = _validate_wave_coordination_contract()
    reset_source = Path(alg.reset.__file__).resolve()
    algorithm_source = Path(alg.__file__).resolve()
    bindings = optimization.get("bindings") or {}
    pg_search_outer = optimization.get("pg_search_outer") or {}
    if (
        optimization.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or optimization.get("formal_scope") != "mysql_only"
        or optimization.get("cross_engine_comparison_prohibited") is not True
        or int(pg_search_outer.get("real_top_k_limit") or 0) != 5
        or pg_search_outer.get("candidate_batches") != [[1, 2], [3, 4], [5]]
        or pg_search_outer.get("required_acceptance_tier")
        != "qps_strict_progress"
        or pg_search_outer.get("accepted_target") is not None
        or float(pg_search_outer.get("terminal_normalized_width") or 0.0)
        != rejection_cycle.TERMINAL_NORMALIZED_WIDTH
        or pg_search_outer.get("outer_rejection_cycle_policy_id")
        != rejection_cycle.POLICY_ID
        or pg_search_outer.get("post_search_stability_confirmation") is not False
        or (optimization.get("optimized_reset") or {}).get("reset_concurrency") != 4
        or bindings.get("reset_source_sha256") != alg.sha(reset_source)
        or bindings.get("algorithm_source_sha256") != alg.sha(algorithm_source)
    ):
        raise RuntimeError("outer-runtime optimization authority differs")
    bound_freeze = (ROOT / str(bindings["resource_freeze_path"])).resolve()
    if (
        not bound_freeze.is_file()
        or bound_freeze.is_symlink()
        or alg.sha(bound_freeze) != bindings.get("resource_freeze_sha256")
    ):
        raise RuntimeError("optimized boundary resource freeze binding differs")
    alg.FREEZE = bound_freeze
    alg.r0mod.FREEZE = bound_freeze
    alg.r0mod.FREEZE_SCHEMA = str(alg.load(bound_freeze)["schema_version"])
    checkpoint = checkpointmod.verify_checkpoint(args.checkpoint)
    reconstructed = _mapping(
        checkpoint.get("reconstructed_state"), "checkpoint reconstructed state"
    )
    if args.fresh_search:
        reconstructed = copy.deepcopy(reconstructed)
        reconstructed["outer_attempt"] = 0
        reconstructed["accepted_rounds"] = {scene: 0 for scene in SCENES}
        reconstructed["rejected_outer_attempts"] = {
            scene: 0 for scene in SCENES
        }
        reconstructed["acceptance_tier"] = {
            scene: "fallback" for scene in SCENES
        }
        reconstructed["consecutive_failed_rounds"] = {
            scene: 0 for scene in SCENES
        }
        reconstructed["interval_stable_rounds"] = {
            scene: 0 for scene in SCENES
        }
        reconstructed["post_target_stability_confirmation_counts"] = {}
        reconstructed["stable_intervals"] = {
            scene: None for scene in SCENES
        }
        reconstructed["converged"] = {scene: False for scene in SCENES}
        reconstructed["replayed_param_keys_by_scene"] = {
            scene: [] for scene in SCENES
        }
        reconstructed["full_plan_replay_history_v2"] = {
            scene: [] for scene in SCENES
        }
        reconstructed["params_by_scene"] = {
            scene: copy.deepcopy(alg.DEFAULTS) for scene in SCENES
        }
        for scene in SCENES:
            table = alg.load(alg.TABLE_ROOT / alg.TABLES[scene])
            reconstructed["active_by_scene"][scene] = list(
                table["parameter_space"]["active_params"]
            )
            reconstructed["ranges_by_scene"][scene] = copy.deepcopy(
                table["parameter_space"]["ranges"]
            )
    formal_partial = (
        _validate_formal_partial_continuation(
            source_root=args.formal_partial_root,
            checkpoint=checkpoint,
            reconstructed=reconstructed,
            start_rank=args.formal_start_rank,
            completed_rank5_root=args.formal_completed_rank5_root,
        )
        if args.formal_partial_root is not None else None
    )
    if (
        checkpoint.get("freeze_sha256") != alg.sha(alg.FREEZE)
        or (
            args.interference_override_pilot
            and interference.get("freeze_sha256") != checkpoint.get("freeze_sha256")
        )
    ):
        raise RuntimeError("boundary checkpoint freeze differs")
    _validate_lineage(
        checkpoint, args.runtime_preflight,
        args.prepared_binding, args.benchmark_accounts,
    )
    _validate_fresh_r0(
        args.fresh_r0, checkpoint, args.runtime_preflight,
        args.prepared_binding, args.benchmark_accounts,
        pilot=args.interference_override_pilot,
    )
    replay_quarantine = _validate_replay_quarantine(
        args.replay_quarantine, checkpoint,
    )
    _configure_live_authority(
        args.runtime_preflight, args.prepared_binding, args.benchmark_accounts,
    )
    output.mkdir(parents=True, mode=0o700)
    lock_name = (
        "mysql_four_scene_algorithm_c_v1"
        if args.scene is None else
        f"mysql_algorithm_c_independent_{args.scene}_v1"
    )
    lock = Path(f"/tmp/{lock_name}.lock").open("w")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        raise RuntimeError("another four-scene controller owns the lock") from error
    state_path = output / "run_state.json"
    state = _state_document(
        args=args, checkpoint_path=args.checkpoint,
        checkpoint=checkpoint, reconstructed=reconstructed,
    )
    alg.atomic(state_path, state)
    try:
        if args.interference_override_pilot:
            freeze, workers, point_gate = _validate_pilot_authority()
        else:
            if NO_LEASE_RUNTIME:
                alg.r0mod.COORDINATED_LEASE_HOLDER_PID = None
                alg.r0mod.COORDINATION_CONTRACT = None
                alg.r0mod.AUTHORITY_SCENE_SCOPE = (
                    args.scene if INDEPENDENT_SCENE_EXECUTION else None
                )
                try:
                    freeze, workers, point_gate = alg.r0mod._validate_authority()
                finally:
                    alg.r0mod.AUTHORITY_SCENE_SCOPE = None
                point_gate["prestart_lease_mode"] = "none"
                point_gate["sibling_mysql_scene_wait_required"] = False
                point_gate["whole_round_resource_lease"] = False
            else:
                prestart_acquire = (
                    coordination.acquire_shared_blocking
                    if INDEPENDENT_SCENE_EXECUTION
                    else coordination.acquire_exclusive_blocking
                )
                prestart_lease = prestart_acquire(
                    timeout_seconds=(
                        900.0 if INDEPENDENT_SCENE_EXECUTION else float(
                            coordination_contract["lease_acquisition_timeout_seconds"]
                        )
                    )
                )
                try:
                    alg.r0mod.COORDINATED_LEASE_HOLDER_PID = os.getpid()
                    alg.r0mod.COORDINATION_CONTRACT = WAVE_COORDINATION_CONTRACT
                    alg.r0mod.AUTHORITY_SCENE_SCOPE = (
                        args.scene if INDEPENDENT_SCENE_EXECUTION else None
                    )
                    freeze, workers, point_gate = alg.r0mod._validate_authority()
                    if INDEPENDENT_SCENE_EXECUTION:
                        point_gate["prestart_lease_mode"] = "shared_mysql_scene"
                        point_gate["sibling_mysql_scene_wait_required"] = False
                finally:
                    alg.r0mod.COORDINATED_LEASE_HOLDER_PID = None
                    alg.r0mod.COORDINATION_CONTRACT = None
                    alg.r0mod.AUTHORITY_SCENE_SCOPE = None
                    coordination.release(prestart_lease)
        if args.scene is not None:
            workers = {
                worker_id: worker for worker_id, worker in workers.items()
                if str(worker["scene"]) == args.scene
            }
            if len(workers) != INDEPENDENT_SCENE_WORKER_COUNT:
                raise RuntimeError(
                    f"{args.scene} frozen worker authority differs"
                )
            state["scene_start_authority_barrier"] = scene_start_barrier.wait_ready(
                barrier_root=args.scene_start_barrier,
                scene=args.scene,
                expected_scenes=SCENES,
                authority={
                    "point_gate_passed": point_gate.get("passed") is True,
                    "worker_ids": sorted(workers),
                    "fresh_search": args.fresh_search,
                },
            )
        state["prestart_point_gate"] = point_gate
        state["stage"] = "BOUNDARY_RESUME_AUTHORITY_VALIDATED"
        alg.atomic(state_path, state)
        ranking_log_root = output / "plan_ranking_client_processes"
        ranking_log_root.mkdir(parents=True, mode=0o700)
        for worker_id, worker in workers.items():
            worker["formal_client_log"] = str(ranking_log_root / f"{worker_id}.jsonl")

        params_by_scene = {
            scene: copy.deepcopy(_mapping(reconstructed["params_by_scene"][scene], "params"))
            for scene in SCENES
        }
        ranges_by_scene = {
            scene: copy.deepcopy(_mapping(reconstructed["ranges_by_scene"][scene], "ranges"))
            for scene in SCENES
        }
        initial_ranges_by_scene = {
            scene: copy.deepcopy(
                alg.load(alg.TABLE_ROOT / alg.TABLES[scene])[
                    "parameter_space"
                ]["ranges"]
            )
            for scene in SCENES
        }
        active_by_scene = {
            scene: list(reconstructed["active_by_scene"][scene]) for scene in SCENES
        }
        incumbents = {
            scene: copy.deepcopy(_mapping(reconstructed["incumbents"][scene], "incumbent"))
            for scene in SCENES
        }
        replayed = {
            scene: set(reconstructed["replayed_param_keys_by_scene"][scene])
            for scene in SCENES
        }
        permanently_invalid = {
            scene: set(replay_quarantine["permanent"][scene]) for scene in SCENES
        }
        if not args.fresh_search:
            for scene in SCENES:
                replayed[scene].update(replay_quarantine["soft"][scene])
        state["replay_quarantine_counts"] = {
            scene: len(replay_quarantine["combined"][scene]) for scene in SCENES
        }
        state["soft_replay_quarantine_counts"] = {
            scene: len(replay_quarantine["soft"][scene]) for scene in SCENES
        }
        state["permanent_invalid_quarantine_counts"] = {
            scene: len(permanently_invalid[scene]) for scene in SCENES
        }
        state["replay_quarantine_formal_state_mutation"] = False
        alg.atomic(state_path, state)
        outer_attempt = int(reconstructed["outer_attempt"])

        baseline_assignments = {
            worker_id: {
                "rank": 0,
                "params": copy.deepcopy(params_by_scene[str(worker["scene"])]),
            }
            for worker_id, worker in workers.items()
        }
        if formal_partial is not None:
            baseline_path = Path(formal_partial["baseline"]["path"])
            baseline_document = alg.load(baseline_path)
            baseline_rows = _mapping(
                baseline_document.get("measurements"),
                "formal partial baseline measurements",
            )
            state["formal_partial_continuation"] = {
                key: copy.deepcopy(value)
                for key, value in formal_partial.items()
                if key not in {"ranking_document", "evaluations"}
            }
            state["formal_partial_continuation"]["formal_evidence_eligible"] = True
            state["formal_partial_continuation"]["accepted_state_credit_prohibited"] = False
        elif args.pilot_baseline is not None:
            baseline_path = args.pilot_baseline
            baseline_document = alg.load(baseline_path)
            baseline_rows = _mapping(
                baseline_document.get("measurements"), "pilot baseline measurements"
            )
            state["pilot_reused_incumbent_baseline"] = {
                "path": str(baseline_path), "sha256": alg.sha(baseline_path),
                "formal_evidence_eligible": False,
                "accepted_state_credit_prohibited": True,
            }
        elif REUSE_CHECKPOINT_INCUMBENT_BASELINE and outer_attempt > 0:
            # A scene-local 2->4 worker transition changes only physical
            # candidate parallelism.  Preserve the checkpoint incumbent
            # without remeasuring it solely because two lanes were added.
            # Semantic rows make the existing baseline validation explicit;
            # every value comes from the SHA-bound reconstructed incumbent.
            baseline_path = args.checkpoint
            baseline_document = {"status": "PASS", "failures": []}
            baseline_rows = {
                worker_id: {
                    "scene": str(worker["scene"]),
                    "hard_gate_passed": True,
                    "coverage_complete": True,
                    "db_qps": float(incumbents[str(worker["scene"])]["db_qps"]),
                    "p95_ms": float(incumbents[str(worker["scene"])]["p95_ms"]),
                    "p99_ms": float(incumbents[str(worker["scene"])]["p99_ms"]),
                    "resource_means": copy.deepcopy(
                        incumbents[str(worker["scene"])]["resource_means"]
                    ),
                    "checkpoint_incumbent_semantic_row": True,
                    "physical_measurement_performed": False,
                }
                for worker_id, worker in workers.items()
            }
            state["checkpoint_incumbent_baseline_reuse"] = {
                "checkpoint_path": str(args.checkpoint),
                "checkpoint_sha256": alg.sha(args.checkpoint),
                "completed_outer_attempts": outer_attempt,
                "physical_measurement_performed": False,
                "completed_candidate_replay_due_to_transition": 0,
                "accepted_rounds_mutated": False,
            }
        else:
            baseline_rows, state = _measure_batch_with_fair_coordination(
                freeze=freeze, workers=workers, assignments=baseline_assignments,
                output=output / "formal_incumbent_baseline",
                reset_token="v18_pruning_v2_formal_incumbent_baseline",
                state_path=state_path, state=state,
                stage_prefix="FORMAL_INCUMBENT_BASELINE",
                run_id=args.run_id, contract=coordination_contract,
                interference_override_pilot=args.interference_override_pilot,
            )
            baseline_path = output / "formal_incumbent_baseline/batch_result.json"
            baseline_document = alg.load(baseline_path)
        if (
            baseline_document.get("status") != "PASS"
            or baseline_document.get("failures") not in ([], None)
            or set(baseline_rows) != set(workers)
            or any(
                row.get("hard_gate_passed") is not True
                or row.get("coverage_complete") is not True
                for row in baseline_rows.values()
            )
        ):
            raise RuntimeError("formal incumbent baseline failed closed")
        for scene in SCENES:
            rows = [row for row in baseline_rows.values() if row["scene"] == scene]
            if len(rows) != INDEPENDENT_SCENE_WORKER_COUNT:
                raise RuntimeError(f"{scene} formal incumbent baseline lane count differs")
            incumbents[scene] = {
                "db_qps": sum(float(row["db_qps"]) for row in rows) / len(rows),
                "p95_ms": sum(float(row["p95_ms"]) for row in rows) / len(rows),
                "p99_ms": sum(float(row["p99_ms"]) for row in rows) / len(rows),
                "resource_means": {
                    name: sum(float(row["resource_means"][name]) for row in rows) / len(rows)
                    for name in ("cpu", "memory", "io")
                },
            }
        state["formal_incumbent_baseline"] = {
            "status": "PASS", "path": str(baseline_path),
            "sha256": alg.sha(baseline_path), "worker_count": len(baseline_rows),
            "lanes_per_scene": INDEPENDENT_SCENE_WORKER_COUNT,
            "incumbents": copy.deepcopy(incumbents),
            "accepted_rounds_mutated": False,
        }
        state["stage"] = "FORMAL_INCUMBENT_BASELINE_COMPLETE"
        alg.atomic(state_path, state)

        while not all(bool(state["converged"][scene]) for scene in SCENES):
            candidate_space_exhausted = sorted(
                scene for scene, value in
                (state.get("candidate_space_exhausted") or {}).items()
                if value is True
            )
            if candidate_space_exhausted or outer_attempt >= MAXIMUM_OUTER_ATTEMPTS:
                state["termination_audit"] = {
                    "candidate_space_exhausted": candidate_space_exhausted,
                    "maximum_outer_attempts_exhausted": (
                        outer_attempt >= MAXIMUM_OUTER_ATTEMPTS
                    ),
                }
                break
            outer_attempt += 1
            state["stage"] = f"OUTER_{outer_attempt:03d}_PLAN_PHYSICS_RANKING"
            alg.atomic(state_path, state)
            active_scenes = [
                scene for scene in SCENES
                if not bool(state["converged"][scene])
            ]
            rankings: dict[str, list[dict[str, Any]]] = {}
            all_rankings: dict[str, list[dict[str, Any]]] = {}
            replay_dedup: dict[str, dict[str, Any]] = {}
            partial_this_outer = (
                formal_partial
                if formal_partial is not None
                and int(formal_partial["outer_attempt"]) == outer_attempt
                else None
            )
            representatives: dict[str, str] = {}
            ranking_rows: dict[str, dict[str, Any]] = {}
            if partial_this_outer is not None:
                source_ranking = _mapping(
                    partial_this_outer["ranking_document"],
                    "formal partial ranking document",
                )
                rankings = copy.deepcopy(_mapping(
                    source_ranking["selected_top_k"], "formal partial selected top-k",
                ))
                all_rankings = copy.deepcopy(_mapping(
                    source_ranking["all_nonempty_subsets"],
                    "formal partial all rankings",
                ))
                replay_dedup = copy.deepcopy(_mapping(
                    source_ranking["replay_dedup"], "formal partial replay dedup",
                ))
                ranking_elapsed = 0.0
            else:
                for scene in active_scenes:
                    representatives[scene] = (
                        _single_candidate_live_representative(
                            scene=scene, workers=workers,
                        )
                        if args.interference_override_pilot
                        else sorted(
                            worker_id for worker_id, worker in workers.items()
                            if worker["scene"] == scene
                        )[0]
                    )
                ranking_started = time.monotonic()
                with ThreadPoolExecutor(
                    max_workers=min(4, len(active_scenes)),
                    thread_name_prefix="formal-plan-ranking",
                ) as pool:
                    futures = {
                        pool.submit(
                            _rank_scene,
                            scene=scene,
                            params=params_by_scene[scene],
                            ranges=ranges_by_scene[scene],
                            resources=incumbents[scene]["resource_means"],
                            freeze=freeze,
                            worker=workers[representatives[scene]],
                        ): scene
                        for scene in active_scenes
                    }
                    for future in as_completed(futures):
                        scene = futures[future]
                        ranking_rows[scene] = future.result()
                        all_rankings[scene] = ranking_rows[scene]["rankings"]
                ranking_elapsed = time.monotonic() - ranking_started
                for scene in active_scenes:
                    plan_history = state.setdefault(
                        "full_plan_replay_history_v2", {}
                    ).setdefault(scene, [])
                    representative_worker = workers[representatives[scene]]
                    supplementation_elapsed = 0.0

                    def supplementary_factory(
                        step_fraction: float, direction_mode: str, *,
                        current_scene: str = scene,
                        current_worker: Mapping[str, Any] = representative_worker,
                    ) -> list[dict[str, Any]]:
                        nonlocal supplementation_elapsed
                        started = time.monotonic()
                        rows = alg.candidate_rankings(
                            current_scene,
                            params_by_scene[current_scene],
                            ranges_by_scene[current_scene],
                            incumbents[current_scene]["resource_means"],
                            freeze=freeze, worker=current_worker,
                            step_fraction=step_fraction,
                            direction_mode=direction_mode,
                        )
                        supplementation_elapsed += time.monotonic() - started
                        return rows

                    (
                        all_rankings[scene], rankings[scene],
                        replay_dedup[scene], hard_pruned, pruning_audit,
                    ) = supplement_and_select_fixed_top_k(
                        base_rankings=all_rankings[scene],
                        plan_history=plan_history,
                        replayed_param_keys=replayed[scene],
                        permanently_invalid_param_keys=permanently_invalid[scene],
                        supplementary_factory=supplementary_factory,
                        top_k=FIXED_TOP_K,
                    )
                    ranking_rows[scene][
                        "candidate_supplementation_elapsed_seconds"
                    ] = supplementation_elapsed
                    replay_dedup[scene]["plan_replay_pruning_v2"] = {
                        "contract": PLAN_PRUNING_CONTRACT_VERSION,
                        "contract_path": str(PLAN_PRUNING_CONTRACT),
                        "contract_sha256": alg.sha(PLAN_PRUNING_CONTRACT),
                        "hard_pruned_count": len(hard_pruned),
                        "hard_pruned": hard_pruned,
                        "audit": pruning_audit,
                        "candidate_score_mutated": False,
                        "qps_used_for_resource_prediction_gradient_or_candidate_score": False,
                    }
                    if replay_dedup[scene]["cycle_reset"]:
                        replayed[scene].clear()
                ranking_elapsed += sum(
                    float(row.get("candidate_supplementation_elapsed_seconds", 0.0))
                    for row in ranking_rows.values()
                )
            ranking_document = {
                "schema_version": "mysql_four_scene_algorithm_c_rankings_boundary_resume_v2",
                "outer_attempt": outer_attempt,
                "all_nonempty_subsets": all_rankings,
                "selected_top_k": rankings,
                "replay_dedup": replay_dedup,
                "active_decision_scenes": active_scenes,
                "incumbent_companion_scenes": [
                    scene for scene in SCENES if state["converged"][scene]
                ] if not INDEPENDENT_SCENE_EXECUTION else [],
                "real_top_k_limit": FIXED_TOP_K,
                "top_k_is_upper_bound": True,
                "partial_top_k_allowed": True,
                "duplicate_plan_padding_allowed": False,
                "baseline_padding_allowed": False,
                "qps_used_for_generation_or_ranking": False,
                "qps_used_for_plan_history_replay_ordering_only": True,
                "plan_replay_pruning_contract": PLAN_PRUNING_CONTRACT_VERSION,
                "plan_replay_pruning_contract_sha256": alg.sha(PLAN_PRUNING_CONTRACT),
                "resource_model": "mysql_algorithm_c_contextual_plan_physics_soft_v2",
                "operator_count_hard_pruning": False,
                "real_replay_required": True,
                "plan_ranking_concurrency": min(4, len(active_scenes)),
                "plan_ranking_elapsed_seconds": ranking_elapsed,
                "plan_ranking_scene_audit": {
                    scene: {
                        "representative_worker_id":
                            ranking_rows[scene]["representative_worker_id"],
                        "elapsed_seconds": ranking_rows[scene]["elapsed_seconds"],
                        "candidate_supplementation_elapsed_seconds":
                            ranking_rows[scene].get(
                                "candidate_supplementation_elapsed_seconds", 0.0,
                            ),
                    }
                    for scene in active_scenes if scene in ranking_rows
                },
                "optimized_reset_contract_sha256": alg.sha(OPTIMIZATION_CONTRACT),
                "replay_quarantine_path": str(args.replay_quarantine),
                "replay_quarantine_sha256": alg.sha(args.replay_quarantine),
                "formal_evidence_eligible": not args.interference_override_pilot,
                "formal_partial_continuation": partial_this_outer is not None,
                "formal_partial_source": (
                    None if partial_this_outer is None else {
                        key: copy.deepcopy(partial_this_outer[key])
                        for key in (
                            "source_state", "predicted_rankings", "blocked_gate",
                            "completed_batches", "start_rank",
                        )
                    }
                ),
            }
            alg.atomic(
                output / f"outer_{outer_attempt:03d}" / "predicted_rankings.json",
                ranking_document,
            )
            evaluations = (
                copy.deepcopy(partial_this_outer["evaluations"])
                if partial_this_outer is not None
                else {scene: [] for scene in active_scenes}
            )
            companion_audit: list[dict[str, Any]] = []
            technical_batch_exclusions: list[dict[str, Any]] = copy.deepcopy(
                (partial_this_outer or {}).get("technical_batch_exclusions") or []
            )
            candidate_batches = [
                (index, tuple(ranks))
                for index, ranks in enumerate(FORMAL_CANDIDATE_BATCHES, 1)
            ]
            if partial_this_outer is not None:
                candidate_batches = [
                    (rank, (rank,))
                    for rank in range(int(partial_this_outer["start_rank"]), 6)
                ]
            if args.interference_override_pilot and args.pilot_start_rank > 1:
                candidate_batches = [
                    (rank, (rank,)) for rank in range(args.pilot_start_rank, 6)
                ]
            state["candidate_batches_for_this_execution"] = [
                list(ranks) for _batch_index, ranks in candidate_batches
            ]
            state["formal_candidate_batches"] = [
                list(ranks) for ranks in FORMAL_CANDIDATE_BATCHES
            ]
            alg.atomic(state_path, state)
            unresolved_scenes = set(active_scenes)
            first_accepting_wave_by_scene: dict[str, dict[str, Any]] = {}
            for scene in sorted(active_scenes):
                accepted_signatures = set(
                    state["previously_accepted_parameter_signatures"][scene]
                )
                cached_rows = []
                for raw in state["valid_replay_archive_by_scene"][scene]:
                    row = copy.deepcopy(raw)
                    candidate = row.get("candidate_ranking") or {}
                    params = candidate.get("optimized_params") or row.get("params")
                    if not isinstance(params, Mapping):
                        continue
                    signature = alg.optimizer_params_key(params)
                    if (
                        signature in accepted_signatures
                        or signature == alg.optimizer_params_key(
                            params_by_scene[scene]
                        )
                        or any(
                            float(params[name])
                            < float(ranges_by_scene[scene][name]["lower"])
                            or float(params[name])
                            > float(ranges_by_scene[scene][name]["upper"])
                            for name in active_by_scene[scene]
                        )
                    ):
                        continue
                    row["cached_valid_replay_redecision"] = True
                    cached_rows.append(row)
                cached_accepted, _cached_valid = pg_outer.evaluate_batch(
                    cached_rows,
                    incumbents[scene],
                    canonical_baseline=state["formal_incumbent_baseline"][
                        "incumbents"
                    ][scene],
                    target=alg.TARGET[scene],
                )
                if cached_accepted is not None:
                    row = cached_accepted["row"]
                    evaluations[scene].append(row)
                    first_accepting_wave_by_scene[scene] = {
                        "batch_kind": "prephysical_cached_valid_replay",
                        "wave_ranks": [],
                        "selected_rank": int(row.get("rank") or 0),
                        "selected_param_signature": alg.optimizer_params_key(
                            row["params"]
                        ),
                    }
            unresolved_scenes.difference_update(
                first_accepting_wave_by_scene
            )
            for batch_index, ranks in candidate_batches:
                rankings_for_wave = {
                    scene: rows
                    for scene, rows in rankings.items()
                    if scene in unresolved_scenes
                    and any(rank <= len(rows) for rank in ranks)
                }
                if len(ranks) == 1:
                    assignments, companion_workers = (
                        _build_single_candidate_assignments(
                            rankings=rankings_for_wave, params_by_scene=params_by_scene,
                            workers=workers, rank=ranks[0],
                        )
                    )
                else:
                    assignments, companion_workers = predecessor.build_batch_assignments(
                        rankings=rankings_for_wave, params_by_scene=params_by_scene,
                        workers=workers, ranks=ranks,
                    )
                batch_path = (
                    output / f"outer_{outer_attempt:03d}"
                    / f"top5_batch_{batch_index}" / "batch_result.json"
                )
                try:
                    measured, state = _measure_batch_with_fair_coordination(
                        freeze=freeze, workers=workers, assignments=assignments,
                        output=batch_path.parent,
                        reset_token=f"outer_{outer_attempt:03d}_batch_{batch_index}",
                        state_path=state_path, state=state,
                        stage_prefix=f"OUTER_{outer_attempt:03d}_BATCH_{batch_index}",
                        run_id=args.run_id, contract=coordination_contract,
                        interference_override_pilot=args.interference_override_pilot,
                    )
                except BaseException as error:
                    technical = classify_premeasurement_reset_timeout(
                        error, batch_result_path=batch_path,
                    )
                    if technical is None:
                        raise
                    exclusion_path = batch_path.parent.with_name(
                        batch_path.parent.name + "_technical_reset_timeout.json"
                    )
                    alg.atomic(exclusion_path, {
                        **technical,
                        "batch_index": batch_index,
                        "candidate_ranks": list(ranks),
                        "captured_at": alg.now(),
                        "error": f"{type(error).__name__}: {error}",
                    })
                    technical_batch_exclusions.append({
                        **technical,
                        "batch_index": batch_index,
                        "candidate_ranks": list(ranks),
                        "technical_evidence_path": str(exclusion_path),
                        "technical_evidence_sha256": alg.sha(exclusion_path),
                    })
                    state["technical_batch_exclusions"] = copy.deepcopy(
                        technical_batch_exclusions
                    )
                    alg.atomic(state_path, state)
                    continue
                batch_document = alg.load(batch_path)
                if (
                    batch_document.get("status") != "PASS"
                    or batch_document.get("failures") not in ([], None)
                    or len(batch_document.get("measurements") or {})
                    != len(assignments)
                    or any(
                        row.get("hard_gate_passed") is not True
                        or row.get("coverage_complete") is not True
                        for row in (batch_document.get("measurements") or {}).values()
                    )
                ):
                    technical = classify_technical_timeout_batch(
                        batch_document, assignments,
                        batch_result_path=batch_path,
                    )
                    if technical is None:
                        raise RuntimeError(
                            f"outer {outer_attempt} batch {batch_index} failed closed; "
                            f"see {batch_path}"
                        )
                    failed_worker_ids = set(technical["failed_worker_ids"])
                    excluded_ranks = sorted({
                        int((assignments[worker_id] or {}).get("rank") or 0)
                        for worker_id in failed_worker_ids
                    })
                    retained_worker_ids: list[str] = []
                    for worker_id, row in measured.items():
                        if worker_id in failed_worker_ids:
                            continue
                        if (
                            row.get("hard_gate_passed") is not True
                            or row.get("coverage_complete") is not True
                        ):
                            raise RuntimeError(
                                "technical candidate classification left an "
                                f"invalid sibling: {worker_id}"
                            )
                        scene = str(row["scene"])
                        if worker_id in companion_workers:
                            companion_audit.append({
                                "batch_index": batch_index,
                                "worker_id": worker_id,
                                "scene": scene,
                                "incumbent_params": copy.deepcopy(row["params"]),
                                "hard_gate_passed": True,
                                "coverage_complete": True,
                                "retained_from_partially_failed_physical_wave": True,
                            })
                        else:
                            row["candidate_ranking"] = copy.deepcopy(
                                rankings[scene][int(row["rank"]) - 1]
                            )
                            evaluations[scene].append(row)
                        retained_worker_ids.append(worker_id)
                    technical_batch_exclusions.append({
                        **technical,
                        "batch_index": batch_index,
                        "candidate_ranks": excluded_ranks,
                        "physical_wave_ranks": list(ranks),
                        "retained_valid_worker_ids": sorted(retained_worker_ids),
                        "valid_sibling_measurements_retained": bool(
                            retained_worker_ids
                        ),
                        "batch_result_path": str(batch_path),
                        "batch_result_sha256": alg.sha(batch_path),
                    })
                    state["technical_batch_exclusions"] = copy.deepcopy(
                        technical_batch_exclusions
                    )
                    alg.atomic(state_path, state)
                    continue
                for worker_id, row in measured.items():
                    scene = str(row["scene"])
                    if worker_id in companion_workers:
                        companion_audit.append({
                            "batch_index": batch_index, "worker_id": worker_id,
                            "scene": scene,
                            "incumbent_params": copy.deepcopy(row["params"]),
                            "hard_gate_passed": row["hard_gate_passed"],
                            "coverage_complete": row["coverage_complete"],
                        })
                        if row["hard_gate_passed"] is not True:
                            raise RuntimeError(f"{worker_id} companion hard gate failed")
                    else:
                        row["candidate_ranking"] = copy.deepcopy(
                            rankings[scene][int(row["rank"]) - 1]
                        )
                        evaluations[scene].append(row)
            # Match PostgreSQL: the admitted base Top-K is one semantic batch.
            # All physical [2,2,1] waves finish before the batch is evaluated.
            for scene in sorted(unresolved_scenes):
                accepted_evaluation, _valid = pg_outer.evaluate_batch(
                    evaluations[scene],
                    incumbents[scene],
                    canonical_baseline=state["formal_incumbent_baseline"][
                        "incumbents"
                    ][scene],
                    target=alg.TARGET[scene],
                )
                if accepted_evaluation is not None:
                    first_accepting_wave_by_scene[scene] = {
                        "batch_kind": "base_top5",
                        "wave_ranks": [1, 2, 3, 4, 5],
                        "selected_rank": int(
                            accepted_evaluation["row"]["rank"]
                        ),
                        "selected_param_signature": alg.optimizer_params_key(
                            accepted_evaluation["row"]["params"]
                        ),
                        "semantic_batch_evaluated_after_all_physical_waves": True,
                    }
            unresolved_scenes.difference_update(first_accepting_wave_by_scene)
            fallback_sets: dict[str, list[dict[str, Any]]] = {}
            for scene in sorted(unresolved_scenes):
                base_signatures = {
                    alg.optimizer_params_key(row["optimized_params"])
                    for row in rankings.get(scene, [])
                }
                base_plans = {
                    pg_outer.full_plan_fingerprint(row)
                    for row in rankings.get(scene, [])
                }
                raw_expansion = [
                    row for row in all_rankings.get(scene, [])
                    if alg.optimizer_params_key(row["optimized_params"])
                    not in base_signatures
                    and pg_outer.full_plan_fingerprint(row) not in base_plans
                ]
                admitted, admission = pg_outer.admit_partial_topk(
                    raw_expansion,
                    incumbent_params=params_by_scene[scene],
                )
                fallback_sets[scene] = []
                for rank, row in enumerate(admitted, 1):
                    candidate = copy.deepcopy(row)
                    candidate["source_rank"] = candidate.get("rank")
                    candidate["rank"] = rank
                    fallback_sets[scene].append(candidate)
                ranking_document.setdefault(
                    "fallback_plan_change_admission", {}
                )[scene] = admission
            fallback_sets = {
                scene: rows for scene, rows in fallback_sets.items() if rows
            }
            if fallback_sets:
                (
                    fallback_evaluations,
                    fallback_companions,
                    fallback_technical,
                    fallback_accepted,
                    state,
                ) = _replay_pg_expansion_waves(
                    batch_kind="fallback_plan_change",
                    candidate_sets=fallback_sets,
                    params_by_scene=params_by_scene,
                    workers=workers,
                    freeze=freeze,
                    output=output,
                    outer_attempt=outer_attempt,
                    state_path=state_path,
                    state=state,
                    incumbents=incumbents,
                    canonical_baselines=state["formal_incumbent_baseline"][
                        "incumbents"
                    ],
                    run_id=args.run_id,
                    coordination_contract=coordination_contract,
                    interference_override_pilot=args.interference_override_pilot,
                )
                for scene, rows in fallback_evaluations.items():
                    evaluations[scene].extend(rows)
                companion_audit.extend(fallback_companions)
                technical_batch_exclusions.extend(fallback_technical)
                first_accepting_wave_by_scene.update(fallback_accepted)
                unresolved_scenes.difference_update(fallback_accepted)
                for scene, rows in fallback_sets.items():
                    rankings[scene].extend(rows)

            # PostgreSQL C does not treat the scored local Top-10 as an
            # exhausted interval.  Before a second consecutive no-improvement
            # decision may shrink around the incumbent, it probes a
            # deterministic sparse/Halton design over the *current* interval,
            # ranks those points without QPS, keeps distinct full plans, and
            # physically replays the admitted Top-5.  The predecessor merely
            # wrote ``next_outer_expansion=predicted_points_and_plan_tree``;
            # every generated point was still the same local 0.08 gradient.
            coverage_sets: dict[str, list[dict[str, Any]]] = {}
            coverage_generation_audit: dict[str, dict[str, Any]] = {}
            for scene in sorted(unresolved_scenes):
                if args.interference_override_pilot:
                    continue
                if int(state["consecutive_failed_rounds"][scene]) != 1:
                    continue
                representative = sorted(
                    worker_id for worker_id, worker in workers.items()
                    if worker["scene"] == scene
                )[0]
                state["stage"] = (
                    f"OUTER_{outer_attempt:03d}_CURRENT_INTERVAL_"
                    "PLAN_COVERAGE_RANKING"
                )
                state["stage_updated_at"] = alg.now()
                alg.atomic(state_path, state)
                coverage_started = time.monotonic()
                coverage_rankings = global_coverage.candidate_rankings(
                    scene,
                    params_by_scene[scene],
                    ranges_by_scene[scene],
                    incumbents[scene]["resource_means"],
                    freeze=freeze,
                    worker=workers[representative],
                )
                measured_signatures = {
                    alg.optimizer_params_key(row["params"])
                    for row in evaluations[scene]
                    if isinstance(row.get("params"), Mapping)
                }
                measured_signatures.update(replayed[scene])
                measured_plans = {
                    pg_outer.full_plan_fingerprint(
                        _mapping(row.get("candidate_ranking"), "candidate ranking")
                    )
                    for row in evaluations[scene]
                    if isinstance(row.get("candidate_ranking"), Mapping)
                }
                unseen = [
                    row for row in coverage_rankings
                    if alg.optimizer_params_key(row["optimized_params"])
                    not in measured_signatures
                    and pg_outer.full_plan_fingerprint(row) not in measured_plans
                ]
                admitted, admission = pg_outer.admit_partial_topk(
                    unseen,
                    incumbent_params=params_by_scene[scene],
                    previously_accepted_signatures=set(
                        state["previously_accepted_parameter_signatures"][scene]
                    ),
                )
                coverage_sets[scene] = []
                for rank, row in enumerate(admitted, 1):
                    candidate = copy.deepcopy(row)
                    candidate["source_rank"] = candidate.get("rank")
                    candidate["rank"] = rank
                    coverage_sets[scene].append(candidate)
                coverage_generation_audit[scene] = {
                    **global_coverage.audit_document(),
                    "trigger": "second_consecutive_rejection_before_shrink",
                    "representative_worker_id": representative,
                    "generated_candidate_count": len(coverage_rankings),
                    "unseen_parameter_and_plan_count": len(unseen),
                    "admission": admission,
                    "selected_candidate_count": len(coverage_sets[scene]),
                    "ranking_elapsed_seconds": time.monotonic()
                    - coverage_started,
                    "formal_incumbent_unchanged": True,
                    "formal_ranges_unchanged": True,
                    "accepted_or_rejected_credit_increment": 0,
                }
                ranking_document.setdefault(
                    "current_interval_plan_coverage_admission", {}
                )[scene] = copy.deepcopy(coverage_generation_audit[scene])
            coverage_sets = {
                scene: rows for scene, rows in coverage_sets.items() if rows
            }
            if coverage_sets:
                (
                    coverage_evaluations,
                    coverage_companions,
                    coverage_technical,
                    coverage_accepted,
                    state,
                ) = _replay_pg_expansion_waves(
                    batch_kind="current_interval_plan_coverage",
                    candidate_sets=coverage_sets,
                    params_by_scene=params_by_scene,
                    workers=workers,
                    freeze=freeze,
                    output=output,
                    outer_attempt=outer_attempt,
                    state_path=state_path,
                    state=state,
                    incumbents=incumbents,
                    canonical_baselines=state["formal_incumbent_baseline"][
                        "incumbents"
                    ],
                    run_id=args.run_id,
                    coordination_contract=coordination_contract,
                    interference_override_pilot=args.interference_override_pilot,
                )
                for scene, rows in coverage_evaluations.items():
                    evaluations[scene].extend(rows)
                companion_audit.extend(coverage_companions)
                technical_batch_exclusions.extend(coverage_technical)
                first_accepting_wave_by_scene.update(coverage_accepted)
                unresolved_scenes.difference_update(coverage_accepted)
                for scene, rows in coverage_sets.items():
                    rankings[scene].extend(rows)
            alg.atomic(
                output / f"outer_{outer_attempt:03d}"
                / "current_interval_plan_coverage_audit.json",
                {
                    "schema_version": PG_CURRENT_INTERVAL_COVERAGE_SCHEMA,
                    "status": "PASS",
                    "outer_attempt": outer_attempt,
                    "scenes": coverage_generation_audit,
                    "trigger_requires_prior_consecutive_rejection": True,
                    "coverage_precedes_second_rejection_shrink": True,
                    "qps_used_for_generation_or_ranking": False,
                    "external_algorithm_evidence_used": False,
                },
            )
            basin_sets: dict[str, list[dict[str, Any]]] = {}
            for scene in sorted(unresolved_scenes):
                if evaluations[scene]:
                    continue
                if ranges_by_scene[scene] == initial_ranges_by_scene[scene]:
                    continue
                representative = sorted(
                    worker_id for worker_id, worker in workers.items()
                    if worker["scene"] == scene
                )[0]
                full_rankings = _rank_scene(
                    scene=scene,
                    params=params_by_scene[scene],
                    ranges=initial_ranges_by_scene[scene],
                    resources=incumbents[scene]["resource_means"],
                    freeze=freeze,
                    worker=workers[representative],
                )["rankings"]
                admitted, admission = pg_outer.admit_partial_topk(
                    full_rankings,
                    incumbent_params=params_by_scene[scene],
                )
                basin_sets[scene] = []
                for rank, row in enumerate(admitted, 1):
                    candidate = copy.deepcopy(row)
                    candidate["source_rank"] = candidate.get("rank")
                    candidate["rank"] = rank
                    basin_sets[scene].append(candidate)
                ranking_document.setdefault(
                    "full_range_plan_basin_escape_admission", {}
                )[scene] = admission
            basin_sets = {
                scene: rows for scene, rows in basin_sets.items() if rows
            }
            if basin_sets:
                (
                    basin_evaluations,
                    basin_companions,
                    basin_technical,
                    basin_accepted,
                    state,
                ) = _replay_pg_expansion_waves(
                    batch_kind="full_range_plan_basin_escape",
                    candidate_sets=basin_sets,
                    params_by_scene=params_by_scene,
                    workers=workers,
                    freeze=freeze,
                    output=output,
                    outer_attempt=outer_attempt,
                    state_path=state_path,
                    state=state,
                    incumbents=incumbents,
                    canonical_baselines=state["formal_incumbent_baseline"][
                        "incumbents"
                    ],
                    run_id=args.run_id,
                    coordination_contract=coordination_contract,
                    interference_override_pilot=args.interference_override_pilot,
                )
                for scene, rows in basin_evaluations.items():
                    evaluations[scene].extend(rows)
                companion_audit.extend(basin_companions)
                technical_batch_exclusions.extend(basin_technical)
                first_accepting_wave_by_scene.update(basin_accepted)
                unresolved_scenes.difference_update(basin_accepted)
                for scene, rows in basin_sets.items():
                    rankings[scene].extend(rows)
            alg.atomic(
                output / f"outer_{outer_attempt:03d}"
                / "pg_outer_expansion_audit.json",
                {
                    "schema_version": "mysql_pg_outer_expansion_audit_v1",
                    "status": "PASS",
                    "outer_attempt": outer_attempt,
                    "fallback_plan_change_admission": ranking_document.get(
                        "fallback_plan_change_admission", {}
                    ),
                    "full_range_plan_basin_escape_admission": (
                        ranking_document.get(
                            "full_range_plan_basin_escape_admission", {}
                        )
                    ),
                    "current_interval_plan_coverage_admission": (
                        ranking_document.get(
                            "current_interval_plan_coverage_admission", {}
                        )
                    ),
                    "post_search_stability_confirmation": False,
                },
            )
            alg.atomic(output / f"outer_{outer_attempt:03d}" / "companion_audit.json", {
                "schema_version": (
                    "mysql_algorithm_c_async_no_companion_audit_v1"
                    if INDEPENDENT_SCENE_EXECUTION
                    else "mysql_algorithm_c_incumbent_companion_audit_v1"
                ),
                "status": "PASS", "outer_attempt": outer_attempt,
                "rows": companion_audit, "full_workload_contract_preserved": True,
                "incumbent_companion_workloads": (
                    False if INDEPENDENT_SCENE_EXECUTION else bool(companion_audit)
                ),
                "formal_evidence_eligible": not args.interference_override_pilot,
                "technical_batch_exclusions": technical_batch_exclusions,
                "technical_failures_excluded_from_decisions": True,
            })

            for scene in active_scenes:
                rows = evaluations[scene]
                if not rows:
                    if rankings.get(scene):
                        raise pg_outer.TechnicalRoundFailure(
                            f"{scene} round has no valid replay; semantic state unchanged"
                        )
                    state.setdefault("candidate_space_exhausted", {})[scene] = True
                    alg.atomic(
                        output / f"outer_{outer_attempt:03d}" / f"{scene}.decision.json",
                        {
                            "schema_version": "mysql_four_scene_algorithm_c_outer_decision_boundary_resume_v2",
                            "status": "CANDIDATE_SPACE_EXHAUSTED_CLEAN",
                            "scene": scene,
                            "outer_attempt": outer_attempt,
                            "accepted": None,
                            "evaluations": [],
                            "accepted_rounds": state["accepted_rounds"][scene],
                            "rejected_outer_attempts": state["rejected_outer_attempts"][scene],
                            "formal_evidence_eligible": not args.interference_override_pilot,
                            "accepted_state_credit_increment": 0,
                            "rejected_outer_attempt_increment": 0,
                            "acceptance_tier_mutation": False,
                            "convergence_mutation": False,
                            "replay_history_mutation": False,
                            "pruning_evidence_eligible": False,
                            "semantic_state_unchanged": True,
                        },
                    )
                    continue
                eligible: list[tuple[dict[str, Any], dict[str, Any]]] = []
                ordered_rows = sorted(rows, key=lambda item: item["rank"])
                for row in ordered_rows:
                    decision = pg_outer.outer_semantic_decision(
                        row,
                        incumbents[scene],
                        canonical_baseline=state["formal_incumbent_baseline"][
                            "incumbents"
                        ][scene],
                        target=alg.TARGET[scene],
                    )
                    row["latency_gate_passed"] = None
                    row["non_target_bottleneck_gate_passed"] = not bool(
                        decision["candidate_non_target_bottlenecks"]
                    )
                    row["non_target_bottleneck_threshold"] = (
                        NON_TARGET_BOTTLENECK_THRESHOLD
                    )
                    row["non_target_bottlenecks"] = copy.deepcopy(
                        decision["candidate_non_target_bottlenecks"]
                    )
                    row["outer_decision"] = decision
                    if decision["accepted"]:
                        eligible.append((row, decision))

                # A raw QPS comparison across different physical workers is
                # not a comparable outer decision.  Revalidate the incumbent
                # on every lane whenever a raw candidate appears acceptable,
                # and also immediately before the second consecutive
                # rejection would shrink the search interval.  The latter is
                # the requested "multiple rejects, then retest" behavior.
                paired_trigger = (
                    not args.interference_override_pilot
                    and (
                        bool(eligible)
                        or int(state["consecutive_failed_rounds"][scene]) == 1
                    )
                )
                if paired_trigger:
                    raw_eligible_count = len(eligible)
                    lane_controls, state, control_path = (
                        _measure_lane_incumbents(
                            scene=scene,
                            incumbent_params=params_by_scene[scene],
                            freeze=freeze,
                            workers=workers,
                            output=output,
                            outer_attempt=outer_attempt,
                            state_path=state_path,
                            state=state,
                            run_id=args.run_id,
                            coordination_contract=coordination_contract,
                            interference_override_pilot=(
                                args.interference_override_pilot
                            ),
                        )
                    )
                    eligible = []
                    paired_rows: list[dict[str, Any]] = []
                    global_incumbent_qps = float(
                        incumbents[scene]["db_qps"]
                    )
                    for row in ordered_rows:
                        worker_id = str(row.get("worker_id") or "")
                        if worker_id not in lane_controls:
                            raise RuntimeError(
                                f"{scene} candidate lacks same-lane incumbent: "
                                f"{worker_id}"
                            )
                        _lane_normalized_candidate(
                            row=row,
                            lane_incumbent=lane_controls[worker_id],
                            global_incumbent_qps=global_incumbent_qps,
                        )
                        decision = pg_outer.outer_semantic_decision(
                            row,
                            incumbents[scene],
                            canonical_baseline=state[
                                "formal_incumbent_baseline"
                            ]["incumbents"][scene],
                            target=alg.TARGET[scene],
                        )
                        decision.update({
                            "qps_reference_mode": (
                                "last_accepted_lane_paired_normalized"
                            ),
                            "candidate_raw_db_qps": row["raw_db_qps"],
                            "lane_incumbent_db_qps": row[
                                "lane_incumbent_db_qps"
                            ],
                            "lane_paired_qps_gain": row[
                                "lane_paired_qps_gain"
                            ],
                            "candidate_lane_normalized_db_qps": row[
                                "lane_normalized_db_qps"
                            ],
                            "lane_paired_incumbent_revalidation": True,
                        })
                        row["outer_decision"] = decision
                        row["lane_paired_incumbent_revalidation"] = True
                        if decision["accepted"]:
                            eligible.append((row, decision))
                        paired_rows.append({
                            "rank": int(row["rank"]),
                            "worker_id": worker_id,
                            "raw_db_qps": row["raw_db_qps"],
                            "lane_incumbent_db_qps": row[
                                "lane_incumbent_db_qps"
                            ],
                            "lane_paired_qps_gain": row[
                                "lane_paired_qps_gain"
                            ],
                            "lane_normalized_db_qps": row[
                                "lane_normalized_db_qps"
                            ],
                            "accepted_after_pairing": bool(
                                decision["accepted"]
                            ),
                        })
                    lane_qps = sorted(
                        float(row["db_qps"])
                        for row in lane_controls.values()
                    )
                    paired_audit_path = (
                        output / f"outer_{outer_attempt:03d}"
                        / "lane_paired_qps_decision.json"
                    )
                    alg.atomic(paired_audit_path, {
                        "schema_version": LANE_PAIRED_REVALIDATION_SCHEMA,
                        "status": "PASS",
                        "scene": scene,
                        "outer_attempt": outer_attempt,
                        "trigger": (
                            "apparent_raw_acceptance"
                            if raw_eligible_count
                            else "second_consecutive_rejection_before_shrink"
                        ),
                        "raw_eligible_count": raw_eligible_count,
                        "paired_eligible_count": len(eligible),
                        "global_incumbent_db_qps": global_incumbent_qps,
                        "incumbent_control_batch": {
                            "path": str(control_path),
                            "sha256": alg.sha(control_path),
                        },
                        "lane_incumbent_qps_min": lane_qps[0],
                        "lane_incumbent_qps_max": lane_qps[-1],
                        "lane_incumbent_qps_spread_ratio": (
                            lane_qps[-1] / lane_qps[0] - 1.0
                        ),
                        "decision_formula": (
                            "global_incumbent_db_qps * "
                            "candidate_raw_db_qps / lane_incumbent_db_qps"
                        ),
                        "candidate_rows": paired_rows,
                        "control_measurements_grant_no_state_credit": True,
                        "formal_evidence_eligible": True,
                    })
                    state.setdefault(
                        "lane_paired_incumbent_revalidations", []
                    ).append({
                        "scene": scene,
                        "outer_attempt": outer_attempt,
                        "path": str(paired_audit_path),
                        "sha256": alg.sha(paired_audit_path),
                        "trigger": (
                            "apparent_raw_acceptance"
                            if raw_eligible_count
                            else "second_consecutive_rejection_before_shrink"
                        ),
                        "raw_eligible_count": raw_eligible_count,
                        "paired_eligible_count": len(eligible),
                    })
                    alg.atomic(state_path, state)
                state["full_plan_replay_history_v2"][scene] = (
                    record_plan_replay_outcomes(
                        state["full_plan_replay_history_v2"][scene],
                        scene=scene,
                        outer_round=outer_attempt,
                        incumbent_qps=float(incumbents[scene]["db_qps"]),
                        evaluations=[
                            row for row in rows
                            if row.get("cached_valid_replay_redecision") is not True
                        ],
                    )
                )
                archive = state["valid_replay_archive_by_scene"][scene]
                archived_signatures = {
                    alg.optimizer_params_key(
                        (row.get("candidate_ranking") or {}).get(
                            "optimized_params"
                        ) or row.get("params") or {}
                    )
                    for row in archive
                }
                for row in rows:
                    if row.get("cached_valid_replay_redecision") is True:
                        continue
                    signature = alg.optimizer_params_key(row["params"])
                    if signature not in archived_signatures:
                        archive.append(copy.deepcopy(row))
                        archived_signatures.add(signature)
                moved_eligible: list[tuple[dict[str, Any], dict[str, Any], list[str]]] = []
                for row, decision in eligible:
                    try:
                        moved_subset = pg_outer.moved_shrink_subset(
                            incumbent_params=params_by_scene[scene],
                            candidate_ranking=_mapping(
                                row["candidate_ranking"],
                                "movement candidate ranking",
                            ),
                            active_params=active_by_scene[scene],
                        )
                    except RuntimeError as error:
                        if str(error) != "accepted candidate has no moved declared parameter":
                            raise
                        row["effective_parameter_movement_admissible"] = False
                        row["effective_parameter_movement_exclusion_reason"] = (
                            "mysql_float_readback_collapsed_to_incumbent"
                        )
                        continue
                    row["effective_parameter_movement_admissible"] = True
                    moved_eligible.append((row, decision, moved_subset))
                moved_eligible.sort(
                    key=lambda item: pg_outer.accepted_selection_key({
                        "row": item[0], "decision": item[1],
                    }),
                    reverse=True,
                )
                accepted = bool(moved_eligible)
                chosen = None
                if accepted:
                    chosen, _chosen_decision, shrink_active_parameters = (
                        moved_eligible[0]
                    )
                    selected_ranking = _mapping(
                        chosen["candidate_ranking"], "selected candidate ranking"
                    )
                    params_by_scene[scene] = dict(chosen["params"])
                    incumbents[scene] = {
                        key: copy.deepcopy(chosen[key])
                        for key in ("db_qps", "p95_ms", "p99_ms", "resource_means")
                    }
                    ranges_by_scene[scene] = alg.shrink_ranges(
                        ranges_by_scene[scene], params_by_scene[scene],
                        shrink_active_parameters,
                    )
                    if args.interference_override_pilot:
                        state["pilot_diagnostic_acceptances"][scene] += 1
                    else:
                        state["accepted_rounds"][scene] += 1
                        signature = alg.optimizer_params_key(
                            params_by_scene[scene]
                        )
                        if signature not in state[
                            "previously_accepted_parameter_signatures"
                        ][scene]:
                            state[
                                "previously_accepted_parameter_signatures"
                            ][scene].append(signature)
                    replayed[scene].clear()
                else:
                    state["rejected_outer_attempts"][scene] += 1
                    valid_ranks = {int(row["rank"]) for row in rows}
                    replayed[scene].update(
                        alg.optimizer_params_key(row["optimized_params"])
                        for row in rankings[scene]
                        if int(row["rank"]) in valid_ranks
                    )
                if args.interference_override_pilot:
                    rejection_progress = {
                        "policy_id": rejection_cycle.POLICY_ID,
                        "pilot_state_mutation_prohibited": True,
                        "forced_incumbent_shrink": False,
                        "consecutive_rejections_after": state[
                            "consecutive_failed_rounds"
                        ][scene],
                    }
                else:
                    ranges_by_scene[scene], rejection_progress = (
                        rejection_cycle.advance_rejection_cycle(
                            accepted=accepted,
                            consecutive_rejections=int(
                                state["consecutive_failed_rounds"][scene]
                            ),
                            ranges=ranges_by_scene[scene],
                            incumbent_params=params_by_scene[scene],
                            active_params=active_by_scene[scene],
                        )
                    )
                    state["consecutive_failed_rounds"][scene] = int(
                        rejection_progress["consecutive_rejections_after"]
                    )
                    if rejection_progress["forced_incumbent_shrink"]:
                        replayed[scene].clear()
                        state.setdefault(
                            "incumbent_centered_forced_shrinks", {}
                        ).setdefault(scene, 0)
                        state["incumbent_centered_forced_shrinks"][scene] += 1
                convergence = rejection_cycle.convergence_document(
                    ranges=ranges_by_scene[scene],
                    initial_ranges=initial_ranges_by_scene[scene],
                    active_params=active_by_scene[scene],
                )
                if args.interference_override_pilot:
                    state["pilot_diagnostic_convergence"][scene] = convergence
                else:
                    state["interval_stable_rounds"][scene] = 0
                    state["stable_intervals"][scene] = None
                    state["converged"][scene] = bool(convergence["converged"])
                tier = {
                    "acceptance_tier": "fallback",
                    "consecutive_failed_rounds": int(
                        rejection_progress["consecutive_rejections_after"]
                    ),
                    "acceptance_tier_downgrade_active": False,
                }
                if not args.interference_override_pilot:
                    state["acceptance_tier"][scene] = "fallback"
                    state["consecutive_failed_rounds"][scene] = tier[
                        "consecutive_failed_rounds"
                    ]
                state.setdefault("candidate_replay_dedup", {})[scene] = {
                    **replay_dedup[scene], "retained_for_next_outer": len(replayed[scene]),
                }
                alg.atomic(output / f"outer_{outer_attempt:03d}" / f"{scene}.decision.json", {
                    "schema_version": "mysql_four_scene_algorithm_c_outer_decision_boundary_resume_v2",
                    "scene": scene, "outer_attempt": outer_attempt,
                    "accepted": accepted, "evaluations": rows,
                    "tier_progress": tier,
                    "accepted_rounds": state["accepted_rounds"][scene],
                    "rejected_outer_attempts": state["rejected_outer_attempts"][scene],
                    "interval_convergence": convergence,
                    "rejection_cycle": rejection_progress,
                    "formal_evidence_eligible": not args.interference_override_pilot,
                    "accepted_state_credit_increment": (
                        0 if args.interference_override_pilot else int(accepted)
                    ),
                    "mysql_effective_parameter_movement_required": True,
                    "no_move_accepted_candidates_excluded": sum(
                        1 for row in rows
                        if row.get("effective_parameter_movement_admissible") is False
                    ),
                })
            state["outer_attempt"] = outer_attempt
            state["params_by_scene"] = copy.deepcopy(params_by_scene)
            state["ranges_by_scene"] = copy.deepcopy(ranges_by_scene)
            state["incumbents"] = copy.deepcopy(incumbents)
            state["stage"] = (
                "PILOT_OUTER_COMPLETE"
                if args.interference_override_pilot else "FORMAL_OUTER_RUNNING"
            )
            alg.atomic(state_path, state)
            if partial_this_outer is not None:
                formal_partial = None
            if args.interference_override_pilot:
                break

        if args.interference_override_pilot:
            state["status"] = "PILOT_COMPLETE"
        else:
            state["status"] = (
                "COMPLETE" if all(state["converged"].values()) else "NON_CONVERGED"
            )
        state["completion_reason"] = (
            "all_active_log_domain_widths_reached_1pct"
            if state["status"] == "COMPLETE"
            else "candidate_space_or_max_rounds_exhausted"
        )
        state["post_search_stability_confirmation"] = False
        state["stage"] = "FINISHED"
        state["completed_at"] = alg.now()
        alg.atomic(state_path, state)
        return 0 if state["status"] in {"COMPLETE", "PILOT_COMPLETE"} else 2
    except BaseException as error:
        state["status"] = (
            "PILOT_FAILED_CLOSED"
            if args.interference_override_pilot else "FAILED_CLOSED"
        )
        state["stage"] = "FAILED"
        state["failed_at"] = alg.now()
        state["error"] = f"{type(error).__name__}: {error}"
        alg.atomic(state_path, state)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
