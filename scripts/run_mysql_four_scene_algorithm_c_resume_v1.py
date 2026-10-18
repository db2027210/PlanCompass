#!/usr/bin/env python3
"""Resume four-scene Algorithm C from the sealed completed outer-1 checkpoint.

The convergence rule is unchanged: accepted points shrink each positive
parameter interval in log space, every active interval must reach ``<=1%``,
and two consecutive accepted optima must be stable inside that interval.
This controller separates the ten accepted shrink rounds from a bounded ten
rejected-round retry allowance and keeps converged scenes as incumbent
companions so every batch still executes the complete four-scene contract.
"""

from __future__ import annotations

import argparse
import copy
import fcntl
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from common_outer_loop import advance_acceptance_tier, decide_validation_acceptance
from scripts import run_mysql_four_scene_algorithm_c_v2 as alg


SCENES = alg.SCENES
TARGET_ACCEPTED_ROUNDS = 10
MAXIMUM_REJECTED_OUTER_ATTEMPTS_PER_SCENE = 10
CHECKPOINT_SCHEMA = "mysql_algorithm_c_outer1_checkpoint_v1"
STATE_SCHEMA = "mysql_four_scene_algorithm_c_checkpoint_resume_state_v1"
FROZEN_WORKERS_PER_SCENE = 2


def _path(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular non-symlink file required: {path}")
    return path


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{label} JSON object required")
    return dict(value)


def _scene_mapping(value: Any, label: str) -> dict[str, Any]:
    result = _mapping(value, label)
    if set(result) != set(SCENES):
        raise RuntimeError(f"{label} scene set differs")
    return result


def _verify_bound_file(path_value: Any, digest_value: Any, label: str) -> Path:
    path = _path(str(path_value))
    if str(digest_value) != alg.sha(path):
        raise RuntimeError(f"{label} sha256 differs")
    return path


def _accepted_evaluation(decision: Mapping[str, Any]) -> dict[str, Any] | None:
    accepted = [
        _mapping(row, "outer evaluation")
        for row in (decision.get("evaluations") or [])
        if isinstance(row, Mapping)
        and isinstance(row.get("outer_decision"), Mapping)
        and row["outer_decision"].get("accepted") is True
    ]
    accepted.sort(key=lambda row: int(row["rank"]))
    return accepted[0] if accepted else None


def load_checkpoint(
    checkpoint_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate and reconstruct the authoritative state after outer attempt 1."""
    checkpoint = alg.load(checkpoint_path)
    if (
        checkpoint.get("schema_version") != CHECKPOINT_SCHEMA
        or checkpoint.get("status") != "PREPARED"
        or checkpoint.get("formal_scope") != "mysql_only"
        or checkpoint.get("cross_engine_comparison_prohibited") is not True
        or checkpoint.get("completed_outer_attempts") != 1
        or checkpoint.get("workload_started") is not False
        or checkpoint.get("fresh_r0_complete") is not False
        or checkpoint.get("formal_outer_started") is not False
    ):
        raise RuntimeError("outer-1 checkpoint authority differs")

    freeze_path = _verify_bound_file(
        checkpoint.get("freeze_path"), checkpoint.get("freeze_sha256"), "checkpoint freeze"
    )
    if alg.sha(freeze_path) != alg.sha(alg.FREEZE):
        raise RuntimeError("checkpoint freeze is not the active v15 freeze")
    predecessor_state_path = _verify_bound_file(
        checkpoint.get("predecessor_state_path"),
        checkpoint.get("predecessor_state_sha256"),
        "predecessor state",
    )
    predecessor_state = alg.load(predecessor_state_path)
    accepted_rounds = {
        scene: int(value)
        for scene, value in _scene_mapping(
            checkpoint.get("accepted_rounds"), "checkpoint accepted rounds"
        ).items()
    }
    if (
        predecessor_state.get("status") != "RUNNING"
        or predecessor_state.get("stage") != "OUTER_002_PLAN_PHYSICS_RANKING"
        or predecessor_state.get("outer_attempt") != 1
        or _scene_mapping(
            predecessor_state.get("accepted_rounds"), "predecessor accepted rounds"
        ) != accepted_rounds
        or any(
            bool(value)
            for value in _scene_mapping(
                predecessor_state.get("converged"), "predecessor convergence"
            ).values()
        )
    ):
        raise RuntimeError("predecessor stopped-state authority differs")

    baseline_attempts = predecessor_state.get("formal_baseline_attempts") or []
    if not isinstance(baseline_attempts, list) or not baseline_attempts:
        raise RuntimeError("predecessor baseline authority is absent")
    passing_baselines = [
        row for row in baseline_attempts
        if isinstance(row, Mapping) and row.get("status") == "PASS"
    ]
    if len(passing_baselines) != 1:
        raise RuntimeError("exactly one passing predecessor baseline required")
    baseline_path = _verify_bound_file(
        passing_baselines[0].get("batch_result_path"),
        passing_baselines[0].get("batch_result_sha256"),
        "formal baseline batch",
    )
    baseline = alg.load(baseline_path)
    baseline_measurements = _mapping(
        baseline.get("measurements"), "formal baseline measurements"
    )
    if (
        baseline.get("status") != "PASS"
        or set(baseline_measurements) != set(alg.WORKER_IDS)
        or any(
            not isinstance(row, Mapping) or row.get("hard_gate_passed") is not True
            for row in baseline_measurements.values()
        )
    ):
        raise RuntimeError("formal baseline hard-gate authority differs")

    decision_paths = _scene_mapping(
        checkpoint.get("decision_paths"), "checkpoint decision paths"
    )
    decision_hashes = _scene_mapping(
        checkpoint.get("decision_sha256"), "checkpoint decision hashes"
    )
    decisions: dict[str, dict[str, Any]] = {}
    for scene in SCENES:
        path = _verify_bound_file(
            decision_paths[scene], decision_hashes[scene], f"{scene} outer-1 decision"
        )
        decision = alg.load(path)
        if decision.get("scene") != scene or decision.get("outer_attempt") != 1:
            raise RuntimeError(f"{scene} outer-1 decision identity differs")
        decisions[scene] = decision

    batch_paths = checkpoint.get("batch_result_paths") or []
    batch_hashes = _mapping(
        checkpoint.get("batch_result_sha256"), "checkpoint batch hashes"
    )
    if not isinstance(batch_paths, list) or len(batch_paths) != 3:
        raise RuntimeError("three completed outer-1 batches required")
    for index, value in enumerate(batch_paths, 1):
        path = _verify_bound_file(
            value, batch_hashes.get(str(value)), f"outer-1 batch {index}"
        )
        if alg.load(path).get("status") != "PASS":
            raise RuntimeError(f"outer-1 batch {index} did not pass")

    incumbents: dict[str, dict[str, Any]] = {}
    params_by_scene = {scene: dict(alg.DEFAULTS) for scene in SCENES}
    ranges_by_scene: dict[str, dict[str, Any]] = {}
    active_by_scene: dict[str, list[str]] = {}
    replayed: dict[str, set[str]] = {scene: set() for scene in SCENES}
    rejected_attempts: dict[str, int] = {}
    acceptance_tier: dict[str, str] = {}
    consecutive_failed: dict[str, int] = {}
    interval_stable_rounds: dict[str, int] = {}
    stable_intervals: dict[str, Any] = {}
    converged: dict[str, bool] = {}

    for scene in SCENES:
        rows = [
            _mapping(row, "baseline worker measurement")
            for row in baseline_measurements.values()
            if isinstance(row, Mapping) and row.get("scene") == scene
        ]
        if len(rows) != 2:
            raise RuntimeError(f"{scene} requires two passing baseline workers")
        incumbents[scene] = {
            "db_qps": sum(float(row["db_qps"]) for row in rows) / 2.0,
            "p95_ms": sum(float(row["p95_ms"]) for row in rows) / 2.0,
            "p99_ms": sum(float(row["p99_ms"]) for row in rows) / 2.0,
            "resource_means": {
                resource: sum(
                    float(_mapping(row["resource_means"], "resource means")[resource])
                    for row in rows
                ) / 2.0
                for resource in ("cpu", "memory", "io")
            },
        }
        table = alg.load(alg.TABLE_ROOT / alg.TABLES[scene])
        active_by_scene[scene] = list(table["parameter_space"]["active_params"])
        ranges_by_scene[scene] = copy.deepcopy(table["parameter_space"]["ranges"])

        decision = decisions[scene]
        accepted = decision.get("accepted") is True
        chosen = _accepted_evaluation(decision)
        if accepted != (chosen is not None):
            raise RuntimeError(f"{scene} accepted decision/evaluation differs")
        if accepted:
            params_by_scene[scene] = {
                name: float(value)
                for name, value in _mapping(chosen["params"], "chosen params").items()
            }
            incumbents[scene] = {
                key: copy.deepcopy(chosen[key])
                for key in ("db_qps", "p95_ms", "p99_ms", "resource_means")
            }
            ranges_by_scene[scene] = alg.shrink_ranges(
                ranges_by_scene[scene], params_by_scene[scene], active_by_scene[scene]
            )
        else:
            replayed[scene].update(
                alg.optimizer_params_key(_mapping(row["params"], "evaluated params"))
                for row in (decision.get("evaluations") or [])
                if isinstance(row, Mapping)
            )
        tier = _mapping(decision.get("tier_progress"), "tier progress")
        convergence = _mapping(
            decision.get("interval_convergence"), "interval convergence"
        )
        if int(decision.get("accepted_rounds", -1)) != accepted_rounds[scene]:
            raise RuntimeError(f"{scene} accepted-round checkpoint differs")
        acceptance_tier[scene] = str(tier["acceptance_tier"])
        consecutive_failed[scene] = int(tier["consecutive_failed_rounds"])
        interval_stable_rounds[scene] = int(convergence["stable_rounds"])
        stable_intervals[scene] = copy.deepcopy(convergence.get("stable_interval"))
        converged[scene] = bool(convergence["converged"])
        rejected_attempts[scene] = 1 - accepted_rounds[scene]

    reconstructed = {
        "predecessor_state": predecessor_state,
        "predecessor_state_path": predecessor_state_path,
        "baseline_path": baseline_path,
        "decisions": decisions,
        "incumbents": incumbents,
        "params_by_scene": params_by_scene,
        "ranges_by_scene": ranges_by_scene,
        "active_by_scene": active_by_scene,
        "replayed_param_keys_by_scene": replayed,
        "accepted_rounds": accepted_rounds,
        "rejected_outer_attempts": rejected_attempts,
        "acceptance_tier": acceptance_tier,
        "consecutive_failed_rounds": consecutive_failed,
        "interval_stable_rounds": interval_stable_rounds,
        "stable_intervals": stable_intervals,
        "converged": converged,
    }
    return checkpoint, reconstructed


def build_batch_assignments(
    *, rankings: Mapping[str, list[dict[str, Any]]],
    params_by_scene: Mapping[str, Mapping[str, float]],
    workers: Mapping[str, Mapping[str, Any]], ranks: tuple[int, ...],
) -> tuple[dict[str, dict[str, Any]], set[str]]:
    """Assign candidates plus incumbent companions for every frozen scene."""
    assignments: dict[str, dict[str, Any]] = {}
    companion_workers: set[str] = set()
    for scene in SCENES:
        scene_workers = sorted(
            worker_id for worker_id, worker in workers.items()
            if worker["scene"] == scene
        )
        if len(scene_workers) != FROZEN_WORKERS_PER_SCENE:
            raise RuntimeError(f"{scene} frozen worker cardinality differs")
        for slot, rank in enumerate(ranks):
            rows = rankings.get(scene) or []
            if 1 <= rank <= len(rows):
                assignment = {
                    "rank": rank,
                    "params": copy.deepcopy(rows[rank - 1]["optimized_params"]),
                }
            else:
                assignment = {
                    "rank": 0,
                    "params": copy.deepcopy(dict(params_by_scene[scene])),
                }
                companion_workers.add(scene_workers[slot])
            assignments[scene_workers[slot]] = assignment
    return assignments, companion_workers


def _configure_live_authority(
    runtime_preflight: Path, prepared_binding: Path, benchmark_accounts: Path,
) -> None:
    alg.r0mod.PREFLIGHT = runtime_preflight
    alg.r0mod.RUNTIME_PREFLIGHT = runtime_preflight
    alg.r0mod.PREPARED = prepared_binding
    alg.r0mod.BENCHMARK_ACCOUNTS = benchmark_accounts


def _validate_resume_lineage(
    checkpoint_path: Path, checkpoint: Mapping[str, Any],
    runtime_preflight: Path, prepared_binding: Path, benchmark_accounts: Path,
) -> None:
    preflight = alg.load(runtime_preflight)
    prepared = alg.load(prepared_binding)
    accounts = alg.load(benchmark_accounts)
    if (
        preflight.get("status") != "PASS"
        or float(preflight.get("observed_seconds", 0)) < 60.0
        or preflight.get("formal_launch_authorized") is not True
        or preflight.get("freeze_sha256") != checkpoint.get("freeze_sha256")
        or prepared.get("status") != "PREPARED"
        or prepared.get("freeze_sha256") != checkpoint.get("freeze_sha256")
        or prepared.get("preflight_sha256") != alg.sha(runtime_preflight)
        or prepared.get("xp_reset_prepared_sha256") != alg.sha(checkpoint_path)
        or accounts.get("status") != "PASS"
        or accounts.get("prepared_binding_sha256") != alg.sha(prepared_binding)
        or accounts.get("freeze_sha256") != checkpoint.get("freeze_sha256")
        or accounts.get("passwords_emitted") is not False
    ):
        raise RuntimeError("live resume lineage differs")


def _state_document(
    *, args: argparse.Namespace, checkpoint_path: Path,
    checkpoint: Mapping[str, Any], reconstructed: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": STATE_SCHEMA,
        "status": "RUNNING",
        "stage": "VALIDATING_RESUME_AUTHORITY",
        "started_at": alg.now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "active_scenes": list(SCENES),
        "pid": os.getpid(),
        "outer_attempt": 1,
        "completed_predecessor_outer_attempts": 1,
        "accepted_rounds": copy.deepcopy(reconstructed["accepted_rounds"]),
        "rejected_outer_attempts": copy.deepcopy(
            reconstructed["rejected_outer_attempts"]
        ),
        "acceptance_tier": copy.deepcopy(reconstructed["acceptance_tier"]),
        "consecutive_failed_rounds": copy.deepcopy(
            reconstructed["consecutive_failed_rounds"]
        ),
        "acceptance_tier_downgrade_after_rounds": 2,
        "converged": copy.deepcopy(reconstructed["converged"]),
        "interval_stable_rounds": copy.deepcopy(
            reconstructed["interval_stable_rounds"]
        ),
        "stable_intervals": copy.deepcopy(reconstructed["stable_intervals"]),
        "convergence_rule": {
            "metric": "upper_divided_by_lower",
            "maximum_interval_ratio": alg.CONVERGENCE_MAX_INTERVAL_RATIO,
            "maximum_relative_width": alg.CONVERGENCE_MAX_INTERVAL_RATIO - 1.0,
            "required_consecutive_stable_accepted_rounds":
                alg.CONVERGENCE_REQUIRED_STABLE_ROUNDS,
            "rejected_outer_round_resets_stability_streak": True,
            "all_active_parameters_must_satisfy_interval": True,
        },
        "attempt_budget": {
            "target_accepted_rounds_per_scene": TARGET_ACCEPTED_ROUNDS,
            "maximum_rejected_outer_attempts_per_scene":
                MAXIMUM_REJECTED_OUTER_ATTEMPTS_PER_SCENE,
            "maximum_total_attempts_per_scene":
                TARGET_ACCEPTED_ROUNDS
                + MAXIMUM_REJECTED_OUTER_ATTEMPTS_PER_SCENE,
            "accepted_and_rejected_budgets_are_separate": True,
        },
        "fixed_top_k": 5,
        "converged_scene_execution": "incumbent_companion_full_workload",
        "checkpoint_path": str(checkpoint_path),
        "checkpoint_sha256": alg.sha(checkpoint_path),
        "predecessor_state_path": str(reconstructed["predecessor_state_path"]),
        "predecessor_state_sha256": checkpoint["predecessor_state_sha256"],
        "baseline_path": str(reconstructed["baseline_path"]),
        "baseline_sha256": alg.sha(reconstructed["baseline_path"]),
        "runtime_preflight_path": str(args.runtime_preflight),
        "runtime_preflight_sha256": alg.sha(args.runtime_preflight),
        "prepared_binding_path": str(args.prepared_binding),
        "prepared_binding_sha256": alg.sha(args.prepared_binding),
        "benchmark_accounts_path": str(args.benchmark_accounts),
        "benchmark_accounts_sha256": alg.sha(args.benchmark_accounts),
        "freeze_path": str(alg.FREEZE),
        "freeze_sha256": alg.sha(alg.FREEZE),
        "resource_model_contract_path": str(alg.RESOURCE_MODEL_CONTRACT),
        "resource_model_contract_sha256": alg.sha(alg.RESOURCE_MODEL_CONTRACT),
        "resource_model_source_sha256": alg.sha(alg.RESOURCE_MODEL_SOURCE),
        "controller_source_path": str(Path(__file__).resolve()),
        "controller_source_sha256": alg.sha(Path(__file__).resolve()),
        "formal_outer_started": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--checkpoint", required=True, type=_path)
    parser.add_argument("--runtime-preflight", required=True, type=_path)
    parser.add_argument("--prepared-binding", required=True, type=_path)
    parser.add_argument("--benchmark-accounts", required=True, type=_path)
    parser.add_argument("--validate-checkpoint-only", action="store_true")
    parser.add_argument("--validate-authority-only", action="store_true")
    args = parser.parse_args()
    if re.fullmatch(r"[A-Za-z0-9_.-]{8,128}", args.run_id) is None:
        parser.error("unsafe run-id")

    checkpoint, restored = load_checkpoint(args.checkpoint)
    if args.validate_checkpoint_only:
        print(json.dumps({
            "status": "PASS",
            "checkpoint": str(args.checkpoint),
            "accepted_rounds": restored["accepted_rounds"],
            "rejected_outer_attempts": restored["rejected_outer_attempts"],
        }, sort_keys=True))
        return 0

    _configure_live_authority(
        args.runtime_preflight, args.prepared_binding, args.benchmark_accounts
    )
    _validate_resume_lineage(
        args.checkpoint, checkpoint, args.runtime_preflight,
        args.prepared_binding, args.benchmark_accounts,
    )
    if args.validate_authority_only:
        freeze, workers, point_gate = alg.r0mod._validate_authority()
        print(json.dumps({
            "status": "PASS",
            "freeze_sha256": alg.sha(alg.FREEZE),
            "worker_count": len(workers),
            "point_gate_passed": point_gate.get("passed") is True,
            "active_scenes": freeze.get("active_scenes"),
        }, sort_keys=True))
        return 0

    output = ROOT / "history/mysql_four_scene_20260809" / args.run_id
    if output.exists():
        raise RuntimeError(f"fresh formal output exists: {output}")
    output.mkdir(parents=True, mode=0o700)
    lock = Path("/tmp/mysql_four_scene_algorithm_c_v1.lock").open("w")
    try:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        raise RuntimeError(
            "another four-scene Algorithm C controller owns the lock"
        ) from error

    state_path = output / "run_state.json"
    state = _state_document(
        args=args, checkpoint_path=args.checkpoint,
        checkpoint=checkpoint, reconstructed=restored,
    )
    alg.atomic(state_path, state)
    alg.atomic(output / "checkpoint_adoption.json", {
        "schema_version": "mysql_algorithm_c_outer1_checkpoint_adoption_v1",
        "status": "PASS",
        "created_at": alg.now(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "checkpoint_path": str(args.checkpoint),
        "checkpoint_sha256": alg.sha(args.checkpoint),
        "completed_outer_attempts": 1,
        "accepted_rounds": restored["accepted_rounds"],
        "rejected_outer_attempts": restored["rejected_outer_attempts"],
        "baseline_path": str(restored["baseline_path"]),
        "baseline_sha256": alg.sha(restored["baseline_path"]),
        "decision_sha256": checkpoint["decision_sha256"],
        "fresh_baseline_replay_required": False,
    })

    incumbents = restored["incumbents"]
    params_by_scene = restored["params_by_scene"]
    ranges_by_scene = restored["ranges_by_scene"]
    active_by_scene = restored["active_by_scene"]
    replayed = restored["replayed_param_keys_by_scene"]
    outer_attempt = 1
    try:
        freeze, workers, point_gate = alg.r0mod._validate_authority()
        state["prestart_point_gate"] = point_gate
        state["stage"] = "RESUME_AUTHORITY_VALIDATED"
        alg.atomic(state_path, state)
        ranking_log_root = output / "plan_ranking_client_processes"
        ranking_log_root.mkdir(parents=True, mode=0o700)
        for worker_id, worker in workers.items():
            worker["formal_client_log"] = str(
                ranking_log_root / f"{worker_id}.jsonl"
            )
        resource_model_contract = alg.load(alg.RESOURCE_MODEL_CONTRACT)
        if (
            resource_model_contract.get("schema_version")
            != "mysql_algorithm_c_contextual_plan_physics_contract_v2"
            or resource_model_contract.get("status") != "FROZEN"
            or resource_model_contract.get("model", {}).get("sha256")
            != alg.sha(alg.RESOURCE_MODEL_SOURCE)
            or resource_model_contract.get("ranking", {}).get(
                "operator_count_hard_pruning"
            ) is not False
            or resource_model_contract.get("ranking", {}).get(
                "real_replay_required"
            ) is not True
        ):
            raise RuntimeError("contextual resource-model authority differs")

        while not all(state["converged"].values()):
            exhausted = [
                scene for scene in SCENES
                if not state["converged"][scene]
                and state["rejected_outer_attempts"][scene]
                >= MAXIMUM_REJECTED_OUTER_ATTEMPTS_PER_SCENE
            ]
            impossible = [
                scene for scene in SCENES
                if not state["converged"][scene]
                and state["accepted_rounds"][scene] >= TARGET_ACCEPTED_ROUNDS
            ]
            if exhausted or impossible:
                state["termination_audit"] = {
                    "rejected_retry_budget_exhausted": exhausted,
                    "accepted_target_reached_without_convergence": impossible,
                }
                break

            outer_attempt += 1
            state["stage"] = f"OUTER_{outer_attempt:03d}_PLAN_PHYSICS_RANKING"
            alg.atomic(state_path, state)
            active_scenes = [
                scene for scene in SCENES if not state["converged"][scene]
            ]
            rankings: dict[str, list[dict[str, Any]]] = {}
            all_rankings: dict[str, list[dict[str, Any]]] = {}
            replay_dedup: dict[str, dict[str, Any]] = {}
            for scene in active_scenes:
                representative_id = sorted(
                    worker_id for worker_id, worker in workers.items()
                    if worker["scene"] == scene
                )[0]
                all_rankings[scene] = alg.candidate_rankings(
                    scene, params_by_scene[scene], ranges_by_scene[scene],
                    incumbents[scene]["resource_means"], freeze=freeze,
                    worker=workers[representative_id],
                )
                rankings[scene], replay_dedup[scene] = (
                    alg.select_unreplayed_candidates(
                        all_rankings[scene], replayed[scene], top_k=5
                    )
                )
                if replay_dedup[scene]["cycle_reset"]:
                    replayed[scene].clear()
            alg.atomic(output / f"outer_{outer_attempt:03d}" / "predicted_rankings.json", {
                "schema_version": "mysql_four_scene_algorithm_c_rankings_resume_v1",
                "outer_attempt": outer_attempt,
                "all_nonempty_subsets": all_rankings,
                "selected_top_k": rankings,
                "replay_dedup": replay_dedup,
                "active_decision_scenes": active_scenes,
                "incumbent_companion_scenes": [
                    scene for scene in SCENES if state["converged"][scene]
                ],
                "fixed_top_k": 5,
                "qps_used_for_generation_or_ranking": False,
                "resource_model": "mysql_algorithm_c_contextual_plan_physics_soft_v2",
                "operator_count_hard_pruning": False,
                "real_replay_required": True,
            })
            evaluations: dict[str, list[dict[str, Any]]] = {
                scene: [] for scene in active_scenes
            }
            companion_audit: list[dict[str, Any]] = []
            for batch_index, ranks in enumerate(((1, 2), (3, 4), (5,)), 1):
                assignments, companion_workers = build_batch_assignments(
                    rankings=rankings, params_by_scene=params_by_scene,
                    workers=workers, ranks=ranks,
                )
                measured = alg.measure_batch(
                    freeze, workers, assignments,
                    output / f"outer_{outer_attempt:03d}" / f"top5_batch_{batch_index}",
                    f"outer_{outer_attempt:03d}_batch_{batch_index}",
                    state_path=state_path,
                    stage_prefix=f"OUTER_{outer_attempt:03d}_BATCH_{batch_index}",
                )
                for worker_id, row in measured.items():
                    scene = str(row["scene"])
                    if worker_id in companion_workers:
                        companion_audit.append({
                            "batch_index": batch_index,
                            "worker_id": worker_id,
                            "scene": scene,
                            "incumbent_params": copy.deepcopy(row["params"]),
                            "hard_gate_passed": row["hard_gate_passed"],
                            "coverage_complete": row["coverage_complete"],
                        })
                        if row["hard_gate_passed"] is not True:
                            raise RuntimeError(
                                f"{worker_id} incumbent companion hard gate failed"
                            )
                    else:
                        evaluations[scene].append(row)
            alg.atomic(
                output / f"outer_{outer_attempt:03d}" / "companion_audit.json",
                {
                    "schema_version": "mysql_algorithm_c_incumbent_companion_audit_v1",
                    "status": "PASS",
                    "outer_attempt": outer_attempt,
                    "rows": companion_audit,
                    "full_workload_contract_preserved": True,
                },
            )

            for scene in active_scenes:
                rows = evaluations[scene]
                eligible: list[tuple[dict[str, Any], Any]] = []
                for row in sorted(rows, key=lambda item: item["rank"]):
                    latency_gate = (
                        row["p95_ms"] <= incumbents[scene]["p95_ms"] * 1.10
                        and row["p99_ms"] <= incumbents[scene]["p99_ms"] * 1.10
                    )
                    decision = decide_validation_acceptance(
                        incumbents[scene], row,
                        target_resource=alg.TARGET[scene],
                        hard_gate_passed=row["hard_gate_passed"] and latency_gate,
                        utilization_key="resource_means",
                        acceptance_tier=state["acceptance_tier"][scene],
                    )
                    row["latency_gate_passed"] = latency_gate
                    row["outer_decision"] = decision.to_dict()
                    if decision.accepted:
                        eligible.append((row, decision))
                accepted = bool(eligible)
                chosen: dict[str, Any] | None = None
                if accepted:
                    chosen, _decision = eligible[0]
                    params_by_scene[scene] = dict(chosen["params"])
                    incumbents[scene] = {
                        key: copy.deepcopy(chosen[key])
                        for key in ("db_qps", "p95_ms", "p99_ms", "resource_means")
                    }
                    ranges_by_scene[scene] = alg.shrink_ranges(
                        ranges_by_scene[scene], params_by_scene[scene],
                        active_by_scene[scene],
                    )
                    state["accepted_rounds"][scene] += 1
                    replayed[scene].clear()
                else:
                    state["rejected_outer_attempts"][scene] += 1
                    replayed[scene].update(
                        alg.optimizer_params_key(row["optimized_params"])
                        for row in rankings[scene]
                    )
                convergence = alg.advance_interval_convergence(
                    ranges=ranges_by_scene[scene],
                    active=active_by_scene[scene],
                    candidate_params=(chosen["params"] if chosen else None),
                    accepted=accepted,
                    previous_stable_rounds=state["interval_stable_rounds"][scene],
                    previous_interval=state["stable_intervals"][scene],
                )
                state["interval_stable_rounds"][scene] = convergence["stable_rounds"]
                state["stable_intervals"][scene] = convergence["stable_interval"]
                state["converged"][scene] = convergence["converged"]
                tier = advance_acceptance_tier(
                    acceptance_tier=state["acceptance_tier"][scene],
                    consecutive_failed_rounds=
                        state["consecutive_failed_rounds"][scene],
                    accepted=accepted,
                    downgrade_after_rounds=2,
                )
                state["acceptance_tier"][scene] = tier.acceptance_tier
                state["consecutive_failed_rounds"][scene] = (
                    tier.consecutive_failed_rounds
                )
                state.setdefault("candidate_replay_dedup", {})[scene] = {
                    **replay_dedup[scene],
                    "retained_for_next_outer": len(replayed[scene]),
                }
                alg.atomic(
                    output / f"outer_{outer_attempt:03d}" / f"{scene}.decision.json",
                    {
                        "schema_version":
                            "mysql_four_scene_algorithm_c_outer_decision_resume_v1",
                        "scene": scene,
                        "outer_attempt": outer_attempt,
                        "accepted": accepted,
                        "evaluations": rows,
                        "tier_progress": tier.to_dict(),
                        "accepted_rounds": state["accepted_rounds"][scene],
                        "rejected_outer_attempts":
                            state["rejected_outer_attempts"][scene],
                        "interval_convergence": convergence,
                    },
                )
            state["outer_attempt"] = outer_attempt
            state["stage"] = "FORMAL_OUTER_RUNNING"
            alg.atomic(state_path, state)

        state["status"] = (
            "COMPLETE" if all(state["converged"].values()) else "NON_CONVERGED"
        )
        state["stage"] = "FINISHED"
        state["completed_at"] = alg.now()
        alg.atomic(state_path, state)
        return 0 if state["status"] == "COMPLETE" else 2
    except BaseException as error:
        state["status"] = "FAILED_CLOSED"
        state["stage"] = "FAILED"
        state["failed_at"] = alg.now()
        state["error"] = f"{type(error).__name__}: {error}"
        alg.atomic(state_path, state)
        raise


if __name__ == "__main__":
    raise SystemExit(main())
