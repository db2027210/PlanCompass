#!/usr/bin/env python3
"""Run one fresh MySQL Algorithm A scene with PostgreSQL A semantics."""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
from pathlib import Path
import time
from typing import Any, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts import mysql_algorithm_a_full_factorial_v1 as factorial
from scripts import mysql_algorithm_b_pg_outer_policy_v36 as pg_outer
from scripts import run_mysql_algorithm_c_async_scene_v49 as c49
import mysql_workload_adapter_v2 as workload_adapter_v2
from scripts import mysql_cgroup_resource_readback_v2 as cgroup_readback_v2


runtime = c49.runtime
alg = runtime.alg
METHOD = factorial.METHOD
SEARCH_CONTRACT = factorial.SEARCH_CONTRACT
TARGET_ACCEPTED = 10
NORMALIZED_WIDTH_THRESHOLD = 0.01
REPLAY_PARALLELISM = 2
MAX_TECHNICAL_RETRIES_PER_WAVE = 3
MAX_REJECTED_OUTER_ATTEMPTS = 10
INCUMBENT_REFRESH_AFTER_CONSECUTIVE_REJECTIONS = (
    pg_outer.INCUMBENT_REFRESH_AFTER_CONSECUTIVE_REJECTIONS
)
SCENES = tuple(alg.SCENES)
SCENE_SUFFIX = {
    "job_memory": "job",
    "tpch_sf4_memory": "tpch",
    "hybench_xp_cpu": "xp_cpu",
    "hybench_xp_io": "xp_io",
}
A_CONTRACT = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_async_20260822_v3"
    / "algorithm_a_contract.json"
)
HYBENCH_ADAPTIVE_RUNNER = (
    ROOT / "config/formal/frozen_mysql_async_outer_20260820_v38/runtime"
    / "hybench-adaptive-runner-v4.jar"
)


def regular_file(value: str) -> Path:
    path = Path(value).expanduser().resolve()
    if not path.is_file() or path.is_symlink():
        raise argparse.ArgumentTypeError(f"regular non-symlink file required: {path}")
    return path


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    alg.atomic(path, value)


def event(log_path: Path, name: str, **fields: Any) -> None:
    payload = {
        "event": name,
        "time": alg.now(),
        "algorithm": "A",
        "method": METHOD,
        "candidate_search_contract": SEARCH_CONTRACT,
        **copy.deepcopy(fields),
    }
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def scene_parameter_space(scene: str) -> tuple[list[str], dict[str, Any]]:
    table = alg.load(alg.TABLE_ROOT / alg.TABLES[scene])
    space = table.get("parameter_space") or {}
    active = [str(name) for name in (space.get("active_params") or [])]
    ranges = copy.deepcopy(space.get("ranges") or {})
    if (
        not active
        or len(active) != len(set(active))
        or set(ranges) != set(active)
        or any(name not in alg.DEFAULTS for name in active)
    ):
        raise RuntimeError(f"invalid MySQL Algorithm A parameter space: {scene}")
    return active, ranges


def validate_a_contract(scene: str, active: Sequence[str]) -> dict[str, Any]:
    if not A_CONTRACT.is_file() or A_CONTRACT.is_symlink():
        raise RuntimeError(f"frozen MySQL Algorithm A contract absent: {A_CONTRACT}")
    contract = alg.load(A_CONTRACT)
    scene_contract = (contract.get("scenes") or {}).get(scene) or {}
    coordination = contract.get("coordination") or {}
    resource_authority = contract.get("runtime_resource_freeze_authority") or {}
    if (
        contract.get("release_version") != "v3"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("algorithm") != "A"
        or contract.get("method") != METHOD
        or contract.get("candidate_search_contract") != SEARCH_CONTRACT
        or contract.get("accepted_target") != TARGET_ACCEPTED
        or contract.get("normalized_width_threshold")
        != NORMALIZED_WIDTH_THRESHOLD
        or contract.get("maximum_rejected_outer_attempts")
        != MAX_REJECTED_OUTER_ATTEMPTS
        or contract.get("physical_replay_parallelism_per_scene")
        != REPLAY_PARALLELISM
        or contract.get("four_scene_wave_barrier") is not False
        or contract.get("per_scene_outer_state_machine") is not True
        or contract.get("post_convergence_stability_required") is not False
        or contract.get("post_target_physical_replays") != 0
        or (contract.get("postgresql_algorithm_a_flow") or {}).get(
            "qps_reference_mode"
        ) != "last_accepted"
        or (contract.get("postgresql_algorithm_a_flow") or {}).get(
            "first_round_qps_reference"
        ) != "canonical_baseline"
        or (contract.get("postgresql_algorithm_a_flow") or {}).get(
            "recovery_or_worker_transition_reference"
        ) != "freshly_replayed_incumbent_validation"
        or (contract.get("postgresql_algorithm_a_flow") or {}).get(
            "incumbent_reference_refresh_after_consecutive_rejections"
        ) != INCUMBENT_REFRESH_AFTER_CONSECUTIVE_REJECTIONS
        or scene_contract.get("active_parameters") != list(active)
        or int(scene_contract.get("candidates_per_round") or 0)
        != 1 << len(active)
        or coordination.get("resource_isolation_contract")
        != str(c49.COORDINATION_CONTRACT.relative_to(ROOT))
        or coordination.get("resource_isolation_contract_sha256")
        != alg.sha(c49.COORDINATION_CONTRACT)
        or resource_authority.get("source")
        != "runtime_preflight.freeze_path_and_sha256"
        or resource_authority.get("historical_v15_default_allowed") is not False
        or resource_authority.get("algorithm_c_equivalent_binding") is not True
        or resource_authority.get("scoped_r0_authority_per_scene") is not True
        or resource_authority.get("algorithm_flow_changed") is not True
    ):
        raise RuntimeError("frozen MySQL Algorithm A semantic contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(binding["path"])
        if (
            not path.is_file() or path.is_symlink()
            or alg.sha(path) != str(binding["sha256"])
        ):
            raise RuntimeError(f"MySQL Algorithm A source binding differs: {path}")
    return contract


def aggregate_baseline(rows: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    if len(rows) != REPLAY_PARALLELISM:
        raise RuntimeError("Algorithm A baseline requires exactly two workers")
    values = list(rows.values())
    if not all(pg_outer.physical_valid(row) for row in values):
        raise RuntimeError("Algorithm A baseline physical hard gate failed")
    return {
        "db_qps": sum(float(row["db_qps"]) for row in values) / len(values),
        "p95_ms": sum(float(row["p95_ms"]) for row in values) / len(values),
        "p99_ms": sum(float(row["p99_ms"]) for row in values) / len(values),
        "resource_means": {
            name: sum(float(row["resource_means"][name]) for row in values)
            / len(values)
            for name in ("cpu", "memory", "io")
        },
        "hard_gate_passed": True,
        "coverage_complete": True,
        "label": "mysql_algorithm_a_canonical_baseline",
    }


def normalize_to_worker_baseline(
    row: Mapping[str, Any],
    worker_baseline: Mapping[str, Any],
    canonical: Mapping[str, Any],
) -> dict[str, Any]:
    normalized = copy.deepcopy(dict(row))
    worker_qps = float(worker_baseline.get("db_qps") or 0.0)
    canonical_qps = float(canonical.get("db_qps") or 0.0)
    if worker_qps <= 0.0 or canonical_qps <= 0.0:
        raise RuntimeError("Algorithm A same-worker baseline QPS is non-positive")
    normalized["db_qps"] = (
        canonical_qps * float(row["db_qps"]) / worker_qps
    )
    normalized["resource_means"] = {}
    for name in ("cpu", "memory", "io"):
        worker_value = float(worker_baseline["resource_means"][name])
        if worker_value <= 0.0:
            raise RuntimeError(f"Algorithm A worker baseline is non-positive: {name}")
        normalized["resource_means"][name] = (
            float(canonical["resource_means"][name])
            * float(row["resource_means"][name]) / worker_value
        )
    normalized["same_worker_normalization"] = {
        "worker_id": row.get("worker_id"),
        "raw_db_qps": row.get("db_qps"),
        "raw_resource_means": copy.deepcopy(row.get("resource_means")),
        "worker_baseline_db_qps": worker_baseline.get("db_qps"),
        "worker_baseline_resource_means": copy.deepcopy(
            worker_baseline.get("resource_means")
        ),
        "canonical_baseline_db_qps": canonical.get("db_qps"),
        "canonical_baseline_resource_means": copy.deepcopy(
            canonical.get("resource_means")
        ),
    }
    return normalized


def bind_runtime_resource_freeze(
    runtime_preflight: Path,
) -> tuple[dict[str, Any], Path, str]:
    """Bind the resource successor sealed by the live runtime preflight.

    Algorithm C performs this binding before invoking the shared R0 authority
    validator.  Algorithm A consumes the same live authority chain and must do
    the same instead of leaving the shared module on its historical v15
    default.
    """
    preflight = alg.load(runtime_preflight)
    raw_path = preflight.get("freeze_path")
    expected_sha = str(preflight.get("freeze_sha256") or "")
    if not raw_path or not expected_sha:
        raise RuntimeError("Algorithm A runtime preflight freeze binding absent")
    unresolved = Path(str(raw_path)).expanduser()
    if not unresolved.is_absolute():
        unresolved = ROOT / unresolved
    if (
        not unresolved.is_file()
        or unresolved.is_symlink()
        or alg.sha(unresolved) != expected_sha
    ):
        raise RuntimeError("Algorithm A runtime preflight freeze binding differs")
    bound_freeze = unresolved.resolve()
    freeze = alg.load(bound_freeze)
    schema = str(freeze.get("schema_version") or "")
    if not schema.startswith(
        "mysql_disk2_four_scene_two_worker_effective_resource_successor_v"
    ):
        raise RuntimeError("Algorithm A runtime resource freeze schema differs")
    alg.FREEZE = bound_freeze
    alg.r0mod.FREEZE = bound_freeze
    alg.r0mod.FREEZE_SCHEMA = schema
    return preflight, bound_freeze, schema


def validate_fresh_authority(
    *, runtime_preflight: Path, prepared_binding: Path,
    benchmark_accounts: Path, fresh_r0: Path, scene: str,
) -> tuple[dict[str, Any], dict[str, dict[str, Any]], dict[str, Any]]:
    c49.validate_contract()
    coordination_contract = c49.validate_v65_wave_coordination_contract()
    c49.install()
    alg.run_hybench = workload_adapter_v2.run_hybench
    alg.HYBENCH_ADAPTIVE_RUNNER_JAR = HYBENCH_ADAPTIVE_RUNNER
    alg.reset.prep._kernel_resource_readback = cgroup_readback_v2.kernel_resource_readback
    runtime.INDEPENDENT_SCENE_EXECUTION = True
    runtime.SCENES = (scene,)
    preflight, bound_freeze, bound_freeze_schema = bind_runtime_resource_freeze(
        runtime_preflight,
    )
    resource_authority = (
        alg.load(A_CONTRACT).get("runtime_resource_freeze_authority") or {}
    )
    expected_freeze = (
        ROOT / str(resource_authority.get("path") or "")
    ).resolve()
    if (
        bound_freeze != expected_freeze
        or alg.sha(bound_freeze) != resource_authority.get("sha256")
        or bound_freeze_schema != resource_authority.get("schema_version")
    ):
        raise RuntimeError("Algorithm A frozen resource authority differs")
    runtime._configure_live_authority(
        runtime_preflight, prepared_binding, benchmark_accounts,
    )
    prepared = alg.load(prepared_binding)
    accounts = alg.load(benchmark_accounts)
    r0 = alg.load(fresh_r0)
    if (
        preflight.get("status") != "PASS"
        or preflight.get("formal_launch_authorized") is not True
        or preflight.get("formal_scope") != "mysql_only"
        or preflight.get("cross_engine_comparison_prohibited") is not True
        or float(preflight.get("observed_seconds") or 0.0) < 60.0
        or any((preflight.get("accepted_rounds") or {}).values())
        or prepared.get("status") != "PREPARED"
        or prepared.get("formal_scope") != "mysql_only"
        or prepared.get("cross_engine_comparison_prohibited") is not True
        or prepared.get("freeze_sha256") != preflight.get("freeze_sha256")
        or prepared.get("preflight_sha256") != alg.sha(runtime_preflight)
        or prepared.get("workload_started") is not False
        or accounts.get("status") != "PASS"
        or accounts.get("prepared_binding_sha256") != alg.sha(prepared_binding)
        or accounts.get("freeze_sha256") != preflight.get("freeze_sha256")
        or accounts.get("passwords_emitted") is not False
        or r0.get("status") != "PASS"
        or r0.get("fresh_r0_complete") is not True
        or r0.get("formal_outer_launch_authorized") is not True
        or r0.get("formal_outer_started") is not False
        or r0.get("formal_scope") != "mysql_only"
        or r0.get("cross_engine_comparison_prohibited") is not True
        or r0.get("authority_scene_scope") != scene
        or r0.get("other_scene_completion_required") is not False
        or r0.get("freeze_sha256") != preflight.get("freeze_sha256")
        or r0.get("preflight_sha256") != alg.sha(runtime_preflight)
        or r0.get("prepared_binding_sha256") != alg.sha(prepared_binding)
        or r0.get("benchmark_accounts_sha256") != alg.sha(benchmark_accounts)
        or any((r0.get("accepted_rounds") or {}).values())
        or (r0.get("target_bottleneck_gate") or {}).get("passed") is not True
        or r0.get("failures") not in ([], None)
        or r0.get("client_isolation_failures") not in ([], None)
    ):
        raise RuntimeError("fresh MySQL Algorithm A authority differs")
    previous_scope = alg.r0mod.AUTHORITY_SCENE_SCOPE
    alg.r0mod.AUTHORITY_SCENE_SCOPE = scene
    try:
        freeze, all_workers, point_gate = alg.r0mod._validate_authority()
    finally:
        alg.r0mod.AUTHORITY_SCENE_SCOPE = previous_scope
    workers = {
        worker_id: copy.deepcopy(worker)
        for worker_id, worker in all_workers.items()
        if worker.get("scene") == scene
    }
    if (
        len(workers) != REPLAY_PARALLELISM
        or set(workers) != {
            row.get("worker_id") for row in (prepared.get("workers") or [])
            if row.get("scene") == scene
        }
        or alg.sha(alg.FREEZE) != preflight.get("freeze_sha256")
    ):
        raise RuntimeError("MySQL Algorithm A scene worker authority differs")
    return freeze, workers, {
        "coordination_contract": coordination_contract,
        "prestart_point_gate": point_gate,
        "resource_freeze_path": str(bound_freeze),
        "resource_freeze_sha256": alg.sha(bound_freeze),
        "resource_freeze_schema": bound_freeze_schema,
        "preflight_sha256": alg.sha(runtime_preflight),
        "prepared_binding_sha256": alg.sha(prepared_binding),
        "benchmark_accounts_sha256": alg.sha(benchmark_accounts),
        "fresh_r0_sha256": alg.sha(fresh_r0),
    }


def clean_restore(workers: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    readbacks = {
        worker_id: alg.apply_costs(worker, alg.DEFAULTS)
        for worker_id, worker in workers.items()
    }
    return {
        "status": "PASS",
        "restored_params": copy.deepcopy(alg.DEFAULTS),
        "worker_readbacks": readbacks,
        "post_search_stability_measurements": 0,
    }


def fresh_state(
    *, run_id: str, scene: str, active: Sequence[str],
    ranges: Mapping[str, Any], authority: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "mysql_algorithm_a_async_scene_state_v1",
        "status": "RUNNING",
        "stage": "VALIDATING_FRESH_AUTHORITY",
        "algorithm": "A",
        "method": METHOD,
        "candidate_search_contract": SEARCH_CONTRACT,
        "run_id": run_id,
        "async_scene": scene,
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "four_scene_wave_barrier": False,
        "other_scene_completion_required": False,
        "per_scene_outer_state_machine": True,
        "scene_may_advance_immediately": True,
        "active_parameters": list(active),
        "candidate_count_per_round": 1 << len(active),
        "physical_replay_parallelism": REPLAY_PARALLELISM,
        "complete_round_requires_every_parameter_vector_attempted": True,
        "prediction_used_for_selection": False,
        "prediction_used_for_ordering": False,
        "qps_used_for_inner_scoring": False,
        "qps_reference_mode": "last_accepted",
        "first_round_qps_reference": "canonical_baseline",
        "recovery_or_worker_transition_reference": (
            "freshly_replayed_incumbent_validation"
        ),
        "accepted": 0,
        "accepted_target": TARGET_ACCEPTED,
        "round_index": 0,
        "rejected_rounds": 0,
        "consecutive_rejected_rounds": 0,
        "incumbent_reference_refresh_count": 0,
        "incumbent_reference_refresh_after_consecutive_rejections": (
            INCUMBENT_REFRESH_AFTER_CONSECUTIVE_REJECTIONS
        ),
        "maximum_rejected_outer_attempts": MAX_REJECTED_OUTER_ATTEMPTS,
        "incumbent_params": copy.deepcopy(alg.DEFAULTS),
        "ranges": copy.deepcopy(dict(ranges)),
        "canonical_baseline": None,
        "baseline_by_worker": {},
        "incumbent_validation": None,
        "incumbent_reference_source": None,
        "last_incumbent_reference_refresh": None,
        "round_checkpoint": None,
        "accepted_history": [],
        "normalized_width": 1.0,
        "normalized_width_threshold": NORMALIZED_WIDTH_THRESHOLD,
        "converged": False,
        "post_convergence_stability_required": False,
        "post_target_physical_replays": 0,
        "authority": copy.deepcopy(dict(authority)),
        "started_at": alg.now(),
    }


def measure(
    *, freeze: Mapping[str, Any], workers: dict[str, dict[str, Any]],
    assignments: Mapping[str, Mapping[str, Any]], output: Path,
    reset_token: str, state_path: Path, state: dict[str, Any],
    stage_prefix: str, run_id: str, contract: Mapping[str, Any],
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    return runtime._measure_batch_with_fair_coordination(
        freeze=freeze,
        workers=workers,
        assignments=assignments,
        output=output,
        reset_token=reset_token,
        state_path=state_path,
        state=state,
        stage_prefix=stage_prefix,
        run_id=run_id,
        contract=contract,
        interference_override_pilot=False,
    )


def aggregate_incumbent_reference(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Aggregate a complete same-incumbent replay into the next QPS reference."""
    if not rows or not all(pg_outer.physical_valid(row) for row in rows):
        raise RuntimeError("Algorithm A incumbent reference replay hard gate failed")
    return {
        "db_qps": sum(float(row["db_qps"]) for row in rows) / len(rows),
        "p95_ms": sum(float(row["p95_ms"]) for row in rows) / len(rows),
        "p99_ms": sum(float(row["p99_ms"]) for row in rows) / len(rows),
        "resource_means": {
            name: sum(float(row["resource_means"][name]) for row in rows)
            / len(rows)
            for name in ("cpu", "memory", "io")
        },
        "hard_gate_passed": True,
        "coverage_complete": True,
        "label": "mysql_algorithm_a_refreshed_incumbent_reference",
    }


def incumbent_reference_refresh_due(state: Mapping[str, Any]) -> bool:
    """Refresh only after repeated semantic rejection, never on every round."""
    return (
        int(state.get("consecutive_rejected_rounds") or 0)
        >= INCUMBENT_REFRESH_AFTER_CONSECUTIVE_REJECTIONS
        and int(state.get("rejected_rounds") or 0)
        < MAX_REJECTED_OUTER_ATTEMPTS
    )


def run(args: argparse.Namespace) -> int:
    scene = str(args.scene)
    suffix = SCENE_SUFFIX[scene]
    if not args.run_id.endswith("_" + suffix):
        raise RuntimeError(f"Algorithm A run id must end with _{suffix}")
    output = args.output_dir.resolve()
    state_path = output / "algorithm_a_state.json"
    log_path = output / "algorithm_a.jsonl"
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"fresh Algorithm A output required: {output}")
    active, ranges = scene_parameter_space(scene)
    validate_a_contract(scene, active)
    output.mkdir(parents=True, mode=0o700)
    provisional = fresh_state(
        run_id=args.run_id, scene=scene, active=active, ranges=ranges,
        authority={},
    )
    atomic(state_path, provisional)
    try:
        freeze, workers, authority = validate_fresh_authority(
            runtime_preflight=args.runtime_preflight,
            prepared_binding=args.prepared_binding,
            benchmark_accounts=args.benchmark_accounts,
            fresh_r0=args.fresh_r0,
            scene=scene,
        )
        state = alg.load(state_path)
        state["authority"] = authority
        state["stage"] = "FORMAL_BASELINE_MEASURING"
        atomic(state_path, state)
        worker_ids = sorted(workers)
        baseline_assignments = {
            worker_id: {"rank": index + 1, "params": copy.deepcopy(alg.DEFAULTS)}
            for index, worker_id in enumerate(worker_ids)
        }
        baseline_rows, state = measure(
            freeze=freeze,
            workers=workers,
            assignments=baseline_assignments,
            output=output / "formal_baseline",
            reset_token=f"{args.run_id}_formal_baseline",
            state_path=state_path,
            state=state,
            stage_prefix="FORMAL_BASELINE",
            run_id=args.run_id,
            contract=authority["coordination_contract"],
        )
        state = alg.load(state_path)
        canonical = aggregate_baseline(baseline_rows)
        state["baseline_by_worker"] = copy.deepcopy(baseline_rows)
        state["canonical_baseline"] = canonical
        state["incumbent_validation"] = copy.deepcopy(canonical)
        state["incumbent_reference_source"] = "canonical_baseline"
        state["formal_started"] = True
        state["formal_started_at"] = alg.now()
        atomic(state_path, state)
        event(
            log_path, "algorithm_a_baseline_complete", scene=scene,
            worker_count=len(baseline_rows), canonical_baseline=canonical,
        )

        while int(state["accepted"]) < TARGET_ACCEPTED:
            state = alg.load(state_path)
            round_index = int(state["round_index"]) + 1
            state["round_index"] = round_index
            state["stage"] = f"ROUND_{round_index:03d}_MYSQL_PREDICTION_AUDIT"
            state["round_checkpoint"] = {
                "round_index": round_index,
                "accepted_index": int(state["accepted"]),
                "status": "prediction_audit",
                "attempted_ranks": [],
                "valid_ranks": [],
                "technical_ranks": [],
                "waves": [],
            }
            atomic(state_path, state)
            candidates = factorial.full_factorial_parameter_vectors(
                state["ranges"], active, state["incumbent_params"],
            )
            representative = workers[worker_ids[0]]
            candidates = factorial.attach_mysql_prediction_audit(
                scene,
                candidates,
                state["incumbent_params"],
                state["incumbent_validation"]["resource_means"],
                freeze=freeze,
                worker=representative,
            )
            candidate_path = output / f"round_{round_index:03d}" / "candidates.json"
            atomic(candidate_path, {
                "schema_version": "mysql_algorithm_a_full_factorial_candidates_v1",
                "algorithm": "A",
                "method": METHOD,
                "scene": scene,
                "round_index": round_index,
                "accepted_index": int(state["accepted"]),
                "active_parameters": list(active),
                "candidate_count": len(candidates),
                "expected_candidate_count": 1 << len(active),
                "canonical_rank_order_preserved": True,
                "prediction_used_for_selection": False,
                "prediction_used_for_ordering": False,
                "complete_cartesian_product_physically_required": True,
                "candidates": candidates,
            })
            state = alg.load(state_path)
            state["round_checkpoint"].update({
                "status": "physical_replay",
                "candidate_path": str(candidate_path),
                "candidate_sha256": alg.sha(candidate_path),
                "candidate_count": len(candidates),
            })
            atomic(state_path, state)
            event(
                log_path, "algorithm_a_full_factorial_generated",
                scene=scene, round_index=round_index,
                accepted_index=int(state["accepted"]),
                candidate_count=len(candidates),
                prediction_used_for_selection=False,
                prediction_used_for_ordering=False,
                physical_workload_invocations=0,
            )

            valid_rows: list[dict[str, Any]] = []
            waves = factorial.physical_waves(
                len(candidates), REPLAY_PARALLELISM,
            )
            for wave_index, ranks in enumerate(waves, start=1):
                assignments = {
                    worker_id: {
                        "rank": rank,
                        "params": copy.deepcopy(
                            candidates[rank - 1]["optimized_params"]
                        ),
                    }
                    for worker_id, rank in zip(worker_ids, ranks, strict=False)
                }
                terminal_document: dict[str, Any] | None = None
                measured: dict[str, dict[str, Any]] = {}
                terminal_attempt = 0
                errors: list[str] = []
                for attempt in range(1, MAX_TECHNICAL_RETRIES_PER_WAVE + 1):
                    terminal_attempt = attempt
                    attempt_root = (
                        output / f"round_{round_index:03d}"
                        / f"wave_{wave_index:03d}" / f"attempt_{attempt:02d}"
                    )
                    state = alg.load(state_path)
                    state["stage"] = (
                        f"ROUND_{round_index:03d}_WAVE_{wave_index:03d}_MEASURING"
                    )
                    atomic(state_path, state)
                    try:
                        measured, state = measure(
                            freeze=freeze,
                            workers=workers,
                            assignments=assignments,
                            output=attempt_root,
                            reset_token=(
                                f"{args.run_id}_round_{round_index:03d}_"
                                f"wave_{wave_index:03d}_attempt_{attempt:02d}"
                            ),
                            state_path=state_path,
                            state=state,
                            stage_prefix=(
                                f"ROUND_{round_index:03d}_"
                                f"WAVE_{wave_index:03d}_ATTEMPT_{attempt:02d}"
                            ),
                            run_id=args.run_id,
                            contract=authority["coordination_contract"],
                        )
                    except BaseException as error:
                        errors.append(f"{type(error).__name__}: {error}")
                        batch_path = attempt_root / "batch_result.json"
                        if batch_path.is_file():
                            terminal_document = alg.load(batch_path)
                            break
                        if attempt >= MAX_TECHNICAL_RETRIES_PER_WAVE:
                            state = alg.load(state_path)
                            state["status"] = "TECHNICAL_FAILURE_STOPPED_CLEAN"
                            state["stage"] = "ALGORITHM_A_INCOMPLETE_WAVE_TECHNICAL"
                            state["error"] = errors[-1]
                            state["clean_restore"] = clean_restore(workers)
                            atomic(state_path, state)
                            event(
                                log_path, "algorithm_a_incomplete_wave_technical",
                                scene=scene, round_index=round_index,
                                wave_index=wave_index, ranks=ranks,
                                errors=errors, accepted_unchanged=True,
                                incumbent_unchanged=True, ranges_unchanged=True,
                            )
                            return 2
                        continue
                    terminal_document = alg.load(attempt_root / "batch_result.json")
                    break
                if terminal_document is None:
                    raise RuntimeError("Algorithm A terminal wave evidence is missing")
                wave_passed = bool(
                    terminal_document.get("status") == "PASS"
                    and terminal_document.get("failures") in ([], None)
                    and set(measured) == set(assignments)
                    and all(pg_outer.physical_valid(row) for row in measured.values())
                )
                valid_ranks: list[int] = []
                if wave_passed:
                    for worker_id, row in measured.items():
                        rank = int(row["rank"])
                        normalized = normalize_to_worker_baseline(
                            row, baseline_rows[worker_id], canonical,
                        )
                        normalized["worker_id"] = worker_id
                        normalized["candidate_ranking"] = copy.deepcopy(
                            candidates[rank - 1]
                        )
                        valid_rows.append(normalized)
                        valid_ranks.append(rank)
                state = alg.load(state_path)
                checkpoint = state["round_checkpoint"]
                checkpoint["attempted_ranks"].extend(ranks)
                checkpoint["valid_ranks"].extend(valid_ranks)
                checkpoint["technical_ranks"].extend(
                    rank for rank in ranks if rank not in valid_ranks
                )
                checkpoint["waves"].append({
                    "wave_index": wave_index,
                    "ranks": list(ranks),
                    "attempt": terminal_attempt,
                    "status": "VALID" if wave_passed else "TECHNICAL",
                    "batch_result_path": str(
                        output / f"round_{round_index:03d}"
                        / f"wave_{wave_index:03d}"
                        / f"attempt_{terminal_attempt:02d}" / "batch_result.json"
                    ),
                    "errors": errors,
                })
                checkpoint["status"] = "physical_replay"
                state["stage"] = (
                    f"ROUND_{round_index:03d}_WAVE_{wave_index:03d}_COMPLETE"
                )
                atomic(state_path, state)
                event(
                    log_path, "algorithm_a_physical_wave_complete",
                    scene=scene, round_index=round_index,
                    wave_index=wave_index, ranks=ranks,
                    valid_ranks=valid_ranks,
                    technical_ranks=[rank for rank in ranks if rank not in valid_ranks],
                )

            state = alg.load(state_path)
            checkpoint = state["round_checkpoint"]
            expected_ranks = list(range(1, len(candidates) + 1))
            if sorted(checkpoint["attempted_ranks"]) != expected_ranks:
                raise RuntimeError("Algorithm A did not physically attempt every rank")
            checkpoint["status"] = "complete_factorial_outer_decision"
            state["stage"] = f"ROUND_{round_index:03d}_OUTER_DECISION"
            atomic(state_path, state)
            accepted, evaluations = pg_outer.evaluate_batch(
                valid_rows,
                state["incumbent_validation"],
                canonical_baseline=canonical,
                target=alg.TARGET[scene],
            )
            decision_path = output / f"round_{round_index:03d}" / "outer_decision.json"
            atomic(decision_path, {
                "schema_version": "mysql_algorithm_a_complete_factorial_decision_v1",
                "scene": scene,
                "round_index": round_index,
                "attempted_candidate_count": len(expected_ranks),
                "valid_candidate_count": len(valid_rows),
                "technical_candidate_count": len(expected_ranks) - len(valid_rows),
                "all_parameter_vectors_physically_attempted": True,
                "prediction_used_for_selection": False,
                "prediction_used_for_ordering": False,
                "qps_reference_mode": "last_accepted",
                "qps_reference_source": state.get(
                    "incumbent_reference_source"
                ),
                "qps_reference": copy.deepcopy(
                    state["incumbent_validation"]
                ),
                "evaluations": evaluations,
                "accepted": accepted,
            })
            if accepted is None:
                state = alg.load(state_path)
                state["rejected_rounds"] = int(state["rejected_rounds"]) + 1
                state["consecutive_rejected_rounds"] = (
                    int(state.get("consecutive_rejected_rounds") or 0) + 1
                )
                state["round_checkpoint"] = None
                rejection = {
                    "reason": "complete_factorial_has_no_fallback_acceptable_candidate",
                    "round_index": round_index,
                    "accepted": int(state["accepted"]),
                    "complete_factorial_evidence_required": True,
                    "all_parameter_vectors_physically_attempted": True,
                    "accepted_unchanged": True,
                    "incumbent_unchanged": True,
                    "ranges_unchanged": True,
                    "candidate_cache_state_not_assumed_equivalent": True,
                    "new_physical_replay_required": True,
                    "consecutive_rejected_rounds": int(
                        state["consecutive_rejected_rounds"]
                    ),
                    "incumbent_reference_refresh_triggered": False,
                }
                state.setdefault("rejected_history", []).append(rejection)
                if int(state["rejected_rounds"]) >= MAX_REJECTED_OUTER_ATTEMPTS:
                    state["status"] = "NONCONVERGENCE_CLEAN"
                    state["stage"] = "ALGORITHM_A_REJECTION_BUDGET_EXHAUSTED"
                    state["nonconvergence"] = {
                        **rejection,
                        "maximum_rejected_outer_attempts":
                            MAX_REJECTED_OUTER_ATTEMPTS,
                    }
                    state["clean_restore"] = clean_restore(workers)
                    atomic(state_path, state)
                    event(
                        log_path, "algorithm_a_nonconvergence_clean_stop",
                        scene=scene, **state["nonconvergence"],
                    )
                    return 0
                refresh_due = incumbent_reference_refresh_due(state)
                state["stage"] = (
                    f"ROUND_{round_index:03d}_INCUMBENT_REFERENCE_REFRESH_PENDING"
                    if refresh_due else
                    f"ROUND_{round_index:03d}_REJECTED_CONTINUING"
                )
                atomic(state_path, state)
                event(
                    log_path, "algorithm_a_round_rejected_continuing",
                    scene=scene, **rejection,
                    rejected_rounds=int(state["rejected_rounds"]),
                    maximum_rejected_outer_attempts=MAX_REJECTED_OUTER_ATTEMPTS,
                    incumbent_reference_refresh_due=refresh_due,
                )
                if refresh_due:
                    refresh_index = int(
                        state.get("incumbent_reference_refresh_count") or 0
                    ) + 1
                    incumbent_assignments = {
                        worker_id: {
                            "rank": index + 1,
                            "params": copy.deepcopy(state["incumbent_params"]),
                        }
                        for index, worker_id in enumerate(worker_ids)
                    }
                    refreshed_rows: dict[str, dict[str, Any]] = {}
                    refresh_errors: list[str] = []
                    terminal_attempt = 0
                    for attempt in range(
                        1, MAX_TECHNICAL_RETRIES_PER_WAVE + 1
                    ):
                        terminal_attempt = attempt
                        state = alg.load(state_path)
                        state["stage"] = (
                            f"ROUND_{round_index:03d}_"
                            f"INCUMBENT_REFERENCE_REFRESH_{refresh_index:02d}_"
                            f"ATTEMPT_{attempt:02d}"
                        )
                        atomic(state_path, state)
                        try:
                            measured_rows, state = measure(
                                freeze=freeze,
                                workers=workers,
                                assignments=incumbent_assignments,
                                output=(
                                    output / f"round_{round_index:03d}"
                                    / "incumbent_reference_refresh"
                                    / f"refresh_{refresh_index:02d}"
                                    / f"attempt_{attempt:02d}"
                                ),
                                reset_token=(
                                    f"{args.run_id}_round_{round_index:03d}_"
                                    f"incumbent_refresh_{refresh_index:02d}_"
                                    f"attempt_{attempt:02d}"
                                ),
                                state_path=state_path,
                                state=state,
                                stage_prefix=(
                                    f"ROUND_{round_index:03d}_"
                                    f"INCUMBENT_REFRESH_{refresh_index:02d}_"
                                    f"ATTEMPT_{attempt:02d}"
                                ),
                                run_id=args.run_id,
                                contract=authority["coordination_contract"],
                            )
                            if (
                                set(measured_rows) == set(workers)
                                and all(
                                    pg_outer.physical_valid(row)
                                    for row in measured_rows.values()
                                )
                            ):
                                refreshed_rows = measured_rows
                                break
                            refresh_errors.append(
                                "incomplete_or_invalid_incumbent_replay"
                            )
                        except BaseException as error:
                            refresh_errors.append(
                                f"{type(error).__name__}: {error}"
                            )
                    if not refreshed_rows:
                        state = alg.load(state_path)
                        state["status"] = "TECHNICAL_FAILURE_STOPPED_CLEAN"
                        state["stage"] = (
                            "ALGORITHM_A_INCUMBENT_REFERENCE_REFRESH_TECHNICAL"
                        )
                        state["error"] = refresh_errors[-1]
                        state["clean_restore"] = clean_restore(workers)
                        atomic(state_path, state)
                        event(
                            log_path,
                            "algorithm_a_incumbent_reference_refresh_failed",
                            scene=scene,
                            round_index=round_index,
                            refresh_index=refresh_index,
                            errors=refresh_errors,
                        )
                        return 2
                    normalized_refresh_rows = [
                        normalize_to_worker_baseline(
                            refreshed_rows[worker_id],
                            baseline_rows[worker_id],
                            canonical,
                        )
                        for worker_id in worker_ids
                    ]
                    refreshed_reference = aggregate_incumbent_reference(
                        normalized_refresh_rows
                    )
                    state = alg.load(state_path)
                    previous_reference = copy.deepcopy(
                        state["incumbent_validation"]
                    )
                    refresh_path = (
                        output / f"round_{round_index:03d}"
                        / "incumbent_reference_refresh"
                        / f"refresh_{refresh_index:02d}.json"
                    )
                    atomic(refresh_path, {
                        "schema_version": (
                            "mysql_algorithm_a_incumbent_reference_refresh_v1"
                        ),
                        "scene": scene,
                        "round_index": round_index,
                        "refresh_index": refresh_index,
                        "trigger_consecutive_rejections": (
                            INCUMBENT_REFRESH_AFTER_CONSECUTIVE_REJECTIONS
                        ),
                        "accepted_unchanged": True,
                        "ranges_unchanged": True,
                        "incumbent_params_unchanged": True,
                        "previous_reference": previous_reference,
                        "refreshed_reference": refreshed_reference,
                        "raw_rows": refreshed_rows,
                        "normalized_rows": normalized_refresh_rows,
                        "terminal_attempt": terminal_attempt,
                        "technical_errors": refresh_errors,
                    })
                    state["incumbent_validation"] = copy.deepcopy(
                        refreshed_reference
                    )
                    state["incumbent_reference_source"] = (
                        "replayed_after_consecutive_rejections"
                    )
                    state["incumbent_reference_refresh_count"] = refresh_index
                    state["consecutive_rejected_rounds"] = 0
                    state["last_incumbent_reference_refresh"] = {
                        "round_index": round_index,
                        "refresh_index": refresh_index,
                        "path": str(refresh_path),
                        "sha256": alg.sha(refresh_path),
                        "previous_db_qps": previous_reference["db_qps"],
                        "refreshed_db_qps": refreshed_reference["db_qps"],
                    }
                    state["rejected_history"][-1][
                        "incumbent_reference_refresh_triggered"
                    ] = True
                    state["stage"] = (
                        f"ROUND_{round_index:03d}_"
                        "INCUMBENT_REFERENCE_REFRESHED_CONTINUING"
                    )
                    atomic(state_path, state)
                    event(
                        log_path,
                        "algorithm_a_incumbent_reference_refreshed",
                        scene=scene,
                        round_index=round_index,
                        refresh_index=refresh_index,
                        previous_db_qps=previous_reference["db_qps"],
                        refreshed_db_qps=refreshed_reference["db_qps"],
                        accepted_unchanged=True,
                        ranges_unchanged=True,
                    )
                continue

            state = alg.load(state_path)
            chosen = accepted["row"]
            ranking = chosen["candidate_ranking"]
            old_ranges = copy.deepcopy(state["ranges"])
            new_ranges = alg.shrink_ranges(
                old_ranges, ranking["optimized_params"], list(active),
            )
            state["accepted"] = int(state["accepted"]) + 1
            state["consecutive_rejected_rounds"] = 0
            state["incumbent_params"] = copy.deepcopy(
                ranking["optimized_params"]
            )
            state["incumbent_validation"] = {
                key: copy.deepcopy(chosen[key])
                for key in (
                    "db_qps", "p95_ms", "p99_ms", "resource_means",
                    "hard_gate_passed", "coverage_complete",
                )
            }
            state["incumbent_reference_source"] = "last_accepted_round"
            state["ranges"] = new_ranges
            state["normalized_width"] = 0.5 ** int(state["accepted"])
            state["accepted_history"].append({
                "accepted": int(state["accepted"]),
                "round_index": round_index,
                "rank": int(chosen["rank"]),
                "params": copy.deepcopy(ranking["optimized_params"]),
                "factor_levels": copy.deepcopy(
                    ranking["algorithm_a_factor_levels"]
                ),
                "outer_decision": copy.deepcopy(accepted["decision"]),
                "old_ranges": old_ranges,
                "new_ranges": copy.deepcopy(new_ranges),
                "decision_path": str(decision_path),
                "decision_sha256": alg.sha(decision_path),
            })
            state["round_checkpoint"] = None
            state["stage"] = f"ROUND_{round_index:03d}_ACCEPTED"
            atomic(state_path, state)
            event(
                log_path, "algorithm_a_round_accepted_complete_factorial",
                scene=scene, round_index=round_index,
                accepted=int(state["accepted"]),
                selected_rank=int(chosen["rank"]),
                attempted_candidate_count=len(expected_ranks),
                valid_candidate_count=len(valid_rows),
                all_active_parameters_shrunk=True,
            )

        state = alg.load(state_path)
        expected_width = 0.5 ** TARGET_ACCEPTED
        if (
            int(state["accepted"]) != TARGET_ACCEPTED
            or not math.isclose(
                float(state["normalized_width"]), expected_width,
                rel_tol=0.0, abs_tol=1e-12,
            )
            or expected_width > NORMALIZED_WIDTH_THRESHOLD
        ):
            raise RuntimeError("Algorithm A accepted target/width proof differs")
        state["clean_restore"] = clean_restore(workers)
        state["status"] = "COMPLETE"
        state["stage"] = "COMPLETE"
        state["converged"] = True
        state["completed_at"] = alg.now()
        state["completion_contract"] = {
            "accepted_target_reached": True,
            "accepted": TARGET_ACCEPTED,
            "normalized_width": expected_width,
            "normalized_width_threshold": NORMALIZED_WIDTH_THRESHOLD,
            "post_convergence_stability_required": False,
            "post_target_physical_replays": 0,
            "postgresql_algorithm_a_flow_matched": True,
            "mysql_interfaces_and_prediction_used": True,
        }
        atomic(state_path, state)
        final_path = output / "final_result.json"
        atomic(final_path, {
            "schema_version": "mysql_algorithm_a_async_scene_result_v1",
            "status": "PASS",
            "formal_scope": "mysql_only",
            "cross_engine_comparison_prohibited": True,
            "algorithm": "A",
            "method": METHOD,
            "scene": scene,
            "accepted": TARGET_ACCEPTED,
            "normalized_width": expected_width,
            "post_convergence_stability_required": False,
            "post_target_physical_replays": 0,
            "incumbent_params": state["incumbent_params"],
            "ranges": state["ranges"],
            "state_path": str(state_path),
            "state_sha256": alg.sha(state_path),
        })
        event(
            log_path, "algorithm_a_complete", scene=scene,
            accepted=TARGET_ACCEPTED, normalized_width=expected_width,
            post_target_physical_replays=0,
        )
        return 0
    except BaseException as error:
        state = alg.load(state_path)
        if state.get("status") == "RUNNING":
            state["status"] = "FAILED_CLOSED"
            state["stage"] = "FAILED"
            state["error"] = f"{type(error).__name__}: {error}"
            state["failed_at"] = alg.now()
            atomic(state_path, state)
        event(
            log_path, "algorithm_a_failed_closed", scene=scene,
            error=f"{type(error).__name__}: {error}",
        )
        raise


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--scene", choices=SCENES, required=True)
    parser.add_argument("--runtime-preflight", type=regular_file, required=True)
    parser.add_argument("--prepared-binding", type=regular_file, required=True)
    parser.add_argument("--benchmark-accounts", type=regular_file, required=True)
    parser.add_argument("--fresh-r0", type=regular_file, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
