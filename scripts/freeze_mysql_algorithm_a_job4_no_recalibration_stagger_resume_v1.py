#!/usr/bin/env python3
"""Freeze the failed-transition JOB4 continuation without recalibration."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONFIG = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_a_job4_no_recalibration_stagger_resume_20260824_v1"
)
SNAPSHOT = CONFIG / "resume_predecessor_snapshot.json"
CONTRACT = CONFIG / "resume_contract.json"
ORIGINAL = (
    ROOT
    / "history/mysql_four_scene_20260809/v99_to_a_external_diagnostic_resume_20260823_job_dynamic_slots_v1"
    / "job_memory.predecessor_boundary_snapshot.json"
)
AUTHORITY = (
    ROOT
    / "history/mysql_four_scene_20260809/v99_to_a_external_diagnostic_resume_20260823_job_dynamic_slots_v1"
    / "job_memory.transition_authority.json"
)
FAILED = (
    ROOT
    / "history/mysql_four_scene_20260809/v103_to_a_job4_external_diagnostic_resume_20260823_attempt001_job"
    / "algorithm_a_state.json"
)
FREEZE = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_a_job_external_diagnostic_slots_20260823_v1"
    / "job_four_worker_resource_successor.json"
)
MEASUREMENT_CONTRACT = (
    ROOT / "config/formal/frozen_mysql_job_cache_state_20260824_v11"
    / "cache_state_contract.json"
)


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    original = load(ORIGINAL)
    failed = load(FAILED)
    if (
        original.get("transition_boundary_snapshot") is not True
        or int(original.get("accepted") or 0) != 2
        or int(original.get("round_index") or 0) != 3
        or set((original.get("baseline_by_worker") or {})) != {"job_w1", "job_w2"}
        or failed.get("status") != "FAILED_CLOSED"
        or failed.get("stage") != "FAILED"
        or int(failed.get("accepted") or 0) != 2
        or int(failed.get("physical_replay_parallelism") or 0) != 4
    ):
        raise RuntimeError("JOB4 failed-transition sources differ")

    measurement_bindings = {
        "measurement_v11": ROOT / "scripts/mysql_job_buffer_pool_preconditioned_measurement_v11.py",
        "measurement_v10": ROOT / "scripts/mysql_job_buffer_pool_preconditioned_measurement_v10.py",
        "measurement_v10_contract": (
            ROOT / "config/formal/frozen_mysql_job_cache_state_20260823_v10"
            / "cache_state_contract.json"
        ),
    }
    atomic(MEASUREMENT_CONTRACT, {
        "schema_version": "mysql_job_cache_state_contract_v11",
        "release_version": "v11",
        "status": "FROZEN_RUNTIME_VALIDATED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene": "job_memory",
        "worker_start_stagger_seconds": 90.0,
        "stagger_order": ["job_w1", "job_w2", "job_w3", "job_w4"],
        "full_job_113_once_per_measurement": True,
        "per_suite_hard_timeout_seconds": 900,
        "per_suite_deadline_begins_after_start_offset": True,
        "physical_replay_parallelism": 4,
        "candidate_set_changed": False,
        "outer_loop_semantics_changed": False,
        "resource_envelope_changed": False,
        "source_bindings": {
            name: {"path": relative(path), "sha256": sha(path)}
            for name, path in measurement_bindings.items()
        },
    })

    snapshot = copy.deepcopy(original)
    baselines = copy.deepcopy(original["baseline_by_worker"])
    for target, source in (("job_w3", "job_w1"), ("job_w4", "job_w2")):
        row = copy.deepcopy(baselines[source])
        row["worker_id"] = target
        row["baseline_reuse_without_recalibration"] = {
            "source_worker_id": source,
            "target_worker_id": target,
            "same_frozen_cpu_memory_swap_io_process_envelope": True,
            "numa_exclusive_scheduler_lock_required": False,
            "metrics_replayed": False,
        }
        baselines[target] = row
    snapshot.update({
        "run_id": "v113_job4_no_recalibration_resume_predecessor",
        "status": "FAILED_CLOSED",
        "stage": "JOB4_TRANSITION_BASELINE_TECHNICAL_FAILURE_ARCHIVED",
        "physical_replay_parallelism": 4,
        "baseline_by_worker": baselines,
        "round_checkpoint": {
            "round_index": 3,
            "accepted_index": 2,
            "status": "restart_from_rank_one_after_transition_technical_failure",
            "attempted_ranks": [],
            "valid_ranks": [],
            "technical_ranks": [],
            "waves": [],
        },
        "transition_boundary_snapshot": False,
        "job4_no_recalibration_handoff": {
            "original_boundary_path": str(ORIGINAL),
            "original_boundary_sha256": sha(ORIGINAL),
            "failed_transition_state_path": str(FAILED),
            "failed_transition_state_sha256": sha(FAILED),
            "transition_failure_credit": 0,
            "accepted_count_preserved": 2,
            "interrupted_round_restarts_from_rank_one": True,
            "slot_expansion_recalibration_repeated": False,
            "baseline_mapping": {"job_w1": "job_w1", "job_w2": "job_w2", "job_w3": "job_w1", "job_w4": "job_w2"},
        },
    })
    atomic(SNAPSHOT, snapshot)

    bindings = {
        "controller": ROOT / "scripts/run_mysql_algorithm_a_job4_no_recalibration_external_diagnostic_resume_v1.py",
        "snapshot_freezer": Path(__file__).resolve(),
        "staggered_measurement": ROOT / "scripts/mysql_job_buffer_pool_preconditioned_measurement_v11.py",
        "staggered_measurement_contract": MEASUREMENT_CONTRACT,
        "measurement_v10": ROOT / "scripts/mysql_job_buffer_pool_preconditioned_measurement_v10.py",
        "same_cohort_resume": ROOT / "scripts/run_mysql_algorithm_a_same_cohort_codefix_resume_v1.py",
        "current_algorithm_a_controller": ROOT / "scripts/run_mysql_algorithm_a_async_scene_v3.py",
        "external_diagnostic_gate": ROOT / "scripts/mysql_external_workload_diagnostic_gate_v1.py",
        "transition_authority": AUTHORITY,
        "resource_freeze": FREEZE,
        "original_boundary": ORIGINAL,
        "failed_transition_state": FAILED,
    }
    contract = {
        "schema_version": "mysql_algorithm_a_job4_no_recalibration_stagger_resume_v1",
        "status": "FROZEN_RUNTIME_VALIDATED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "algorithm": "A",
        "scene": "job_memory",
        "accepted_target": 10,
        "preserved_accepted_count": 2,
        "physical_replay_parallelism": 4,
        "slot_expansion_recalibration_repeated": False,
        "interrupted_round_restarts_from_rank_one": True,
        "candidate_set_changed": False,
        "outer_loop_semantics_changed": False,
        "resource_envelope_changed": False,
        "full_job_113_once_per_measurement": True,
        "per_suite_hard_timeout_seconds": 900,
        "suite_start_stagger_seconds": 90,
        "external_workloads_diagnostic_only": True,
        "external_workload_wait_required": False,
        "external_process_control_or_stop_prohibited": True,
        "mysql_fixed_cpu_affinity_retained": True,
        "numa_exclusive_scheduler_lock_required": False,
        "baseline_mapping": {"job_w1": "job_w1", "job_w2": "job_w2", "job_w3": "job_w1", "job_w4": "job_w2"},
        "predecessor_snapshot_path": relative(SNAPSHOT),
        "predecessor_snapshot_sha256": sha(SNAPSHOT),
        "transition_authority_path": relative(AUTHORITY),
        "transition_authority_sha256": sha(AUTHORITY),
        "original_boundary_path": relative(ORIGINAL),
        "original_boundary_sha256": sha(ORIGINAL),
        "source_bindings": {
            name: {"path": relative(path), "sha256": sha(path)}
            for name, path in bindings.items()
        },
    }
    atomic(CONTRACT, contract)
    print(json.dumps({
        "snapshot": str(SNAPSHOT), "snapshot_sha256": sha(SNAPSHOT),
        "contract": str(CONTRACT), "contract_sha256": sha(CONTRACT),
        "measurement_contract": str(MEASUREMENT_CONTRACT),
        "measurement_contract_sha256": sha(MEASUREMENT_CONTRACT),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
