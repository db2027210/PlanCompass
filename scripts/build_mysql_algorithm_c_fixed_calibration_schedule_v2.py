#!/usr/bin/env python3
"""Freeze deterministic 7-train/3-holdout replay order and Docker slots."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
V1_POLICY = (
    ROOT / "config/formal/frozen_mysql_algorithm_c_fast_calibration_20260812_v1"
    / "calibration_release_policy.json"
)
CHECKPOINT = (
    ROOT / "history/mysql_four_scene_20260809"
    / "algorithm_c_outer4_checkpoint_v2_optimized_20260812/prepared_binding.json"
)
RESET_SOURCE = ROOT / "scripts/reset_mysql_xp_data_planes_v5.py"
MEASUREMENT_SOURCE = ROOT / "scripts/run_mysql_four_scene_algorithm_c_v2.py"
WORKLOAD_SOURCE = ROOT / "mysql_benchmark_command.py"
OUTPUT_ROOT = (
    ROOT / "config/formal/frozen_mysql_algorithm_c_fast_calibration_20260812_v2"
)
SCHEDULE_OUTPUT = OUTPUT_ROOT / "fixed_replay_schedule.json"
POLICY_OUTPUT = OUTPUT_ROOT / "calibration_release_policy.json"

SCENES = ["job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io"]
SLOT_WORKERS = {
    "A": ["job_w1", "tpch_w1", "xp_cpu_w1", "xp_io_w1"],
    "B": ["job_w2", "tpch_w2", "xp_cpu_w2", "xp_io_w2"],
}
IMAGE = "mysql:8.0"
IMAGE_ID = "sha256:b3197d59eb3b270cf21bc1166fa726f9c511438416b421504068301df90c3172"
TPC_H_LANES = [
    [9],
    [22, 16, 12, 8, 15],
    [17, 6, 20, 4, 3],
    [19, 18, 13, 10, 21],
    [2, 11, 14, 5, 7, 1],
]
SEQUENCE = [
    {"wave": 1, "slot_A": "T01", "slot_B": "T02"},
    {"wave": 2, "slot_A": "T03", "slot_B": "T04"},
    {"wave": 3, "slot_A": "T05", "slot_B": "T06"},
    {"wave": 4, "slot_A": "T07", "slot_B": "H01"},
    {"wave": 5, "slot_A": "H02", "slot_B": "H03"},
]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def binding(path: Path) -> dict[str, str]:
    return {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}


def matrix_rank(rows: list[list[float]], tolerance: float = 1e-12) -> int:
    matrix = [list(map(float, row)) for row in rows]
    if not matrix:
        return 0
    columns = len(matrix[0])
    if any(len(row) != columns for row in matrix):
        raise RuntimeError("ragged design matrix")
    rank = 0
    for column in range(columns):
        pivot = max(range(rank, len(matrix)), key=lambda row: abs(matrix[row][column]))
        if abs(matrix[pivot][column]) <= tolerance:
            continue
        matrix[rank], matrix[pivot] = matrix[pivot], matrix[rank]
        pivot_value = matrix[rank][column]
        matrix[rank] = [value / pivot_value for value in matrix[rank]]
        for row in range(len(matrix)):
            if row == rank:
                continue
            factor = matrix[row][column]
            if abs(factor) <= tolerance:
                continue
            matrix[row] = [
                value - factor * pivot_component
                for value, pivot_component in zip(matrix[row], matrix[rank])
            ]
        rank += 1
        if rank == len(matrix) or rank == columns:
            break
    return rank


def materialize(lower: float, upper: float, normalized: float) -> float:
    if not (0.0 <= normalized <= 1.0) or lower <= 0.0 or upper <= lower:
        raise RuntimeError("invalid log-space calibration bound")
    return math.exp(math.log(lower) + normalized * (math.log(upper) - math.log(lower)))


def main() -> int:
    inputs = [V1_POLICY, CHECKPOINT, RESET_SOURCE, MEASUREMENT_SOURCE, WORKLOAD_SOURCE]
    for path in inputs:
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"regular frozen input required: {path}")
    if OUTPUT_ROOT.exists():
        raise RuntimeError(f"refusing to replace frozen output: {OUTPUT_ROOT}")

    v1 = load(V1_POLICY)
    checkpoint = load(CHECKPOINT)
    state = checkpoint.get("reconstructed_state") or {}
    active_by_scene = state.get("active_by_scene") or {}
    params_by_scene = state.get("params_by_scene") or {}
    ranges_by_scene = state.get("ranges_by_scene") or {}
    if (
        v1.get("schema_version")
        != "mysql_algorithm_c_fast_calibration_release_policy_v1"
        or v1.get("status") != "FROZEN"
        or checkpoint.get("status") != "PREPARED"
        or checkpoint.get("formal_scope") != "mysql_only"
        or set(active_by_scene) != set(SCENES)
        or set(params_by_scene) != set(SCENES)
        or set(ranges_by_scene) != set(SCENES)
        or len(active_by_scene["hybench_xp_cpu"]) != 7
        or len(active_by_scene["hybench_xp_io"]) != 7
        or len(active_by_scene["job_memory"]) != 4
        or len(active_by_scene["tpch_sf4_memory"]) != 4
    ):
        raise RuntimeError("fixed calibration predecessor differs")

    # The response is a delta from the sealed incumbent, so no separately
    # fitted intercept is needed.  The 0.75 diagonal / 0.25 off-diagonal
    # matrix is well conditioned enough for this small deterministic design.
    train_vectors = {
        f"T{index + 1:02d}": [
            0.75 if index == column else 0.25 for column in range(7)
        ]
        for index in range(7)
    }
    holdout_vectors = {
        "H01": [0.50] * 7,
        "H02": [0.35 if column % 2 == 0 else 0.65 for column in range(7)],
        "H03": [0.65 if column % 2 == 0 else 0.35 for column in range(7)],
    }
    vectors = {**train_vectors, **holdout_vectors}

    rank_audit: dict[str, Any] = {}
    materialized: dict[str, Any] = {}
    for scene in SCENES:
        active = list(active_by_scene[scene])
        dimension = len(active)
        design = [train_vectors[f"T{index:02d}"][:dimension] for index in range(1, 8)]
        rank = matrix_rank(design)
        if rank != dimension:
            raise RuntimeError(f"training design is not full rank for {scene}")
        rank_audit[scene] = {
            "active_parameters": active,
            "active_dimension": dimension,
            "training_rows": 7,
            "matrix_rank": rank,
            "full_column_rank": True,
            "intercept": "sealed_incumbent_delta_zero_not_separately_fitted",
        }
        materialized[scene] = {}
        for point_id, vector in vectors.items():
            params = dict(params_by_scene[scene])
            normalized_by_param: dict[str, float] = {}
            for column, name in enumerate(active):
                normalized = float(vector[column])
                bounds = ranges_by_scene[scene][name]
                params[name] = materialize(
                    float(bounds["lower"]), float(bounds["upper"]), normalized
                )
                normalized_by_param[name] = normalized
            materialized[scene][point_id] = {
                "role": "training" if point_id.startswith("T") else "holdout",
                "normalized_active_parameters": normalized_by_param,
                "optimizer_parameters": params,
                "optimizer_parameters_sha256": canonical_sha(params),
            }

    worker_envelope = {
        row["worker_id"]: row
        for row in v1["release_reuse"]["compatibility_payload"]["resource_envelope"]
    }
    expected_workers = set(SLOT_WORKERS["A"] + SLOT_WORKERS["B"])
    if set(worker_envelope) != expected_workers:
        raise RuntimeError("worker envelope differs from fixed Docker slots")

    schedule_core = {
        "point_order": [f"T{index:02d}" for index in range(1, 8)]
        + [f"H{index:02d}" for index in range(1, 4)],
        "waves": SEQUENCE,
        "slots": {
            slot: {
                "workers": workers,
                "candidate_slot_is_stable_across_all_waves": True,
                "resource_envelope": {
                    worker: worker_envelope[worker] for worker in workers
                },
            }
            for slot, workers in SLOT_WORKERS.items()
        },
        "normalized_training_vectors": train_vectors,
        "normalized_holdout_vectors": holdout_vectors,
        "materialized_points_by_scene": materialized,
    }
    schedule = {
        "schema_version": "mysql_algorithm_c_fixed_calibration_replay_schedule_v2",
        "status": "FROZEN",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "authority": "user_fixed_sequence_and_docker_behavior_2026-08-12",
        "active_scenes": SCENES,
        "predecessor_policy": binding(V1_POLICY),
        "checkpoint_parameter_space": binding(CHECKPOINT),
        "schedule": schedule_core,
        "schedule_sha256": canonical_sha(schedule_core),
        "rank_audit": rank_audit,
        "holdout_isolation": {
            "fit_input_allowlist": [f"T{index:02d}" for index in range(1, 8)],
            "fit_input_denylist": [f"H{index:02d}" for index in range(1, 4)],
            "H01_measured_concurrently_with_T07": True,
            "holdout_bytes_must_not_be_read_before_training_model_is_sealed": True,
            "training_model_sha256_required_before_holdout_scoring": True,
        },
        "docker_behavior": {
            "image": IMAGE,
            "image_id": IMAGE_ID,
            "physical_snapshot_binding_sha256": (
                v1["release_reuse"]["compatibility_payload"]
                ["data_snapshot_identity"]["binding_sha256"]
            ),
            "reset_source": binding(RESET_SOURCE),
            "measurement_source": binding(MEASUREMENT_SOURCE),
            "workload_scheduler_source": binding(WORKLOAD_SOURCE),
            "candidate_parameters_applied_in_worker_id_order": True,
            "two_phase_start_barrier_required": True,
            "maximum_recorded_start_skew_seconds": 2.0,
            "container_replacement_between_points": "XP_only_using_sealed_reset;_JOB_TPCH_shared_frozen_database",
            "readback_before_each_wave_required": [
                "image_id",
                "container_id",
                "mysql_version",
                "optimizer_parameters",
                "cpu_quota_and_cpuset",
                "cpuset_mems",
                "memory_and_swap",
                "io_read_write_limits",
                "pids_limit",
                "innodb_buffer_pool",
                "datadir_snapshot_lineage",
            ],
            "readback_mismatch_action": "fail_closed_before_workload_start",
        },
        "workload_schedule": {
            "wave_parallel_tasks": [
                "slot_A_JOB_TPCH_pair",
                "slot_B_JOB_TPCH_pair",
                "slot_A_XP_CPU",
                "slot_B_XP_CPU",
                "slot_A_XP_IO",
                "slot_B_XP_IO",
            ],
            "job": {
                "sql_set": "113/113 exactly once",
                "clients": 16,
                "heavy_threshold_seconds": 30,
                "heavy_pool_workers": 4,
                "maximum_concurrent_heavy": 4,
                "heavy_order": "longest_estimated_first",
                "heavy_start_stagger_seconds": 1,
                "short_pool_workers": 12,
                "short_order": "shortest_estimated_first",
            },
            "tpch_sf4": {
                "sql_set": "Q1-Q22 exactly once",
                "lanes": TPC_H_LANES,
                "terminals_per_lane": 1,
                "lane_submission_order": [1, 2, 3, 4, 5],
            },
            "job_and_tpch_start_concurrently_within_each_slot": True,
            "native_xp": {
                "duration_seconds": 600,
                "clients": 8,
                "iq_selection": "native_uniform_random_IQ1_IQ7",
                "at_mix": "35/25/15/15/7/3",
                "xap_xtp_ratio": "1:1",
                "fixed_query_sequence_override_prohibited": True,
                "fixed_ap_query_ids_weights_or_sequence_prohibited": True,
                "rng_seed_may_be_fixed_only_if_native_runtime_exposes_it_without_mix_override": True,
            },
            "wall_timeout_seconds": 900,
        },
        "execution_contract": {
            "executor_must_consume_this_exact_schedule_sha256": True,
            "dynamic_point_generation_or_reordering_prohibited": True,
            "worker_slot_substitution_prohibited": True,
            "partial_wave_credit_prohibited": True,
            "technical_failure_must_be_isolated_and_replayed_in_same_slot": True,
            "ssd_pollution_and_dynamic_non_interference_gates_unchanged": True,
        },
    }

    policy = dict(v1)
    policy.update({
        "schema_version": "mysql_algorithm_c_fast_calibration_release_policy_v2",
        "created_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "authority": "user_fixed_sequence_and_docker_behavior_2026-08-12",
        "predecessor": binding(V1_POLICY),
        "fixed_replay_schedule": {
            "path": str(SCHEDULE_OUTPUT.relative_to(ROOT)),
            "sha256": "PENDING_UNTIL_SCHEDULE_WRITE",
            "schedule_sha256": schedule["schedule_sha256"],
        },
        "fresh_calibration": {
            **v1["fresh_calibration"],
            "fixed_sequence_required": True,
            "fixed_docker_slot_behavior_required": True,
            "response_model": "incumbent_relative_delta_without_separate_intercept",
            "per_scene_full_rank_audit": rank_audit,
        },
    })

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=False)
    SCHEDULE_OUTPUT.write_text(
        json.dumps(schedule, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    SCHEDULE_OUTPUT.with_name(SCHEDULE_OUTPUT.name + ".sha256").write_text(
        f"{sha(SCHEDULE_OUTPUT)}  {SCHEDULE_OUTPUT.name}\n", encoding="ascii"
    )
    policy["fixed_replay_schedule"]["sha256"] = sha(SCHEDULE_OUTPUT)
    POLICY_OUTPUT.write_text(
        json.dumps(policy, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    POLICY_OUTPUT.with_name(POLICY_OUTPUT.name + ".sha256").write_text(
        f"{sha(POLICY_OUTPUT)}  {POLICY_OUTPUT.name}\n", encoding="ascii"
    )
    print(SCHEDULE_OUTPUT)
    print(POLICY_OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
