#!/usr/bin/env python3
"""Freeze a zero-credit two-to-four-worker JOB round-11 boundary."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config/formal/frozen_mysql_algorithm_a_job4_round11_20260825_v1"
SNAPSHOT = CONFIG / "resume_predecessor_snapshot.json"
CONTRACT = CONFIG / "resume_contract.json"
AUTHORITY = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v99_to_a_external_diagnostic_resume_20260823_job_dynamic_slots_v1"
    / "job_memory.transition_authority.json"
)
ORIGINAL_BOUNDARY = AUTHORITY.with_name("job_memory.predecessor_boundary_snapshot.json")
FREEZE = (
    ROOT / "config/formal"
    / "frozen_mysql_algorithm_a_job_external_diagnostic_slots_20260823_v1"
    / "job_four_worker_resource_successor.json"
)


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-state", type=Path, required=True)
    args = parser.parse_args()
    source_path = args.source_state.resolve()
    if source_path.is_symlink() or not source_path.is_file():
        raise RuntimeError("regular source state required")
    source = load(source_path)
    checkpoint = source.get("round_checkpoint") or {}
    baselines = source.get("baseline_by_worker") or {}
    if (
        source.get("algorithm") != "A"
        or source.get("async_scene") != "job_memory"
        or source.get("formal_scope") != "mysql_only"
        or source.get("cross_engine_comparison_prohibited") is not True
        or int(source.get("accepted") or 0) != 6
        or float(source.get("normalized_width") or 0) != 0.015625
        or int(source.get("round_index") or 0) != 11
        or int(checkpoint.get("round_index") or 0) != 11
        or list(checkpoint.get("attempted_ranks") or [])
        or list(checkpoint.get("valid_ranks") or [])
        or list(checkpoint.get("waves") or [])
        or set(baselines) != {"job_w1", "job_w2"}
    ):
        raise RuntimeError("source is not the zero-credit accepted-6 JOB round-11 boundary")

    snapshot = copy.deepcopy(source)
    expanded = copy.deepcopy(baselines)
    mapping = {"job_w1": "job_w1", "job_w2": "job_w2", "job_w3": "job_w1", "job_w4": "job_w2"}
    for target, origin in (("job_w3", "job_w1"), ("job_w4", "job_w2")):
        row = copy.deepcopy(expanded[origin])
        row["worker_id"] = target
        row["baseline_reuse_without_recalibration"] = {
            "source_worker_id": origin,
            "target_worker_id": target,
            "same_frozen_cpu_memory_swap_io_process_envelope": True,
            "metrics_replayed": False,
        }
        expanded[target] = row
    snapshot["run_id"] = "v153_job4_round11_transition_snapshot"
    snapshot["physical_replay_parallelism"] = 4
    snapshot["baseline_by_worker"] = expanded
    snapshot["status"] = "TECHNICAL_BOUNDARY_ARCHIVED"
    snapshot["stage"] = "ROUND_011_PRECONDITION_ZERO_CREDIT_WORKER_TRANSITION"
    snapshot["round_checkpoint"] = {
        "round_index": 11,
        "accepted_index": 6,
        "status": "restart_from_rank_one_after_worker_transition",
        "attempted_ranks": [], "valid_ranks": [], "technical_ranks": [], "waves": [],
    }
    snapshot["job4_round11_handoff"] = {
        "source_state_path": str(source_path),
        "source_state_sha256": sha(source_path),
        "accepted_count_preserved": 6,
        "normalized_width_preserved": 0.015625,
        "interrupted_precondition_credit": 0,
        "candidate_measurements_completed": 0,
        "slot_expansion_recalibration_repeated": False,
        "baseline_mapping": mapping,
    }
    atomic(SNAPSHOT, snapshot)

    bindings = {
        "controller": ROOT / "scripts/run_mysql_algorithm_a_job4_round11_dynamic_resume_v1.py",
        "snapshot_builder": Path(__file__).resolve(),
        "algorithm_a_controller": ROOT / "scripts/run_mysql_algorithm_a_async_scene_v3.py",
        "same_cohort_resume": ROOT / "scripts/run_mysql_algorithm_a_same_cohort_codefix_resume_v1.py",
        "job_measurement": ROOT / "scripts/mysql_job_buffer_pool_preconditioned_measurement_v10.py",
        "job_measurement_contract": ROOT / "config/formal/frozen_mysql_job_cache_state_20260823_v10/cache_state_contract.json",
        "dynamic_noninterference_gate": ROOT / "scripts/mysql_cross_project_wave_coordination_v4.py",
        "full_parallel_preflight": ROOT / "scripts/preflight_mysql_four_scene_v7.py",
        "transition_authority": AUTHORITY,
        "resource_freeze": FREEZE,
        "original_boundary": ORIGINAL_BOUNDARY,
        "source_state": source_path,
    }
    contract = {
        "schema_version": "mysql_algorithm_a_job4_round11_resume_v1",
        "status": "FROZEN_RUNTIME_VALIDATED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "algorithm": "A", "scene": "job_memory",
        "preserved_accepted_count": 6,
        "preserved_normalized_width": 0.015625,
        "normalized_width_threshold": 0.01,
        "resumed_round_index": 11,
        "physical_replay_parallelism": 4,
        "slot_expansion_recalibration_repeated": False,
        "interrupted_precondition_credit": 0,
        "candidate_measurements_completed_before_transition": 0,
        "candidate_set_changed": False,
        "outer_loop_semantics_changed": False,
        "resource_envelope_changed": False,
        "cross_scene_completion_dependency": False,
        "full_job_113_once_per_measurement": True,
        "per_suite_hard_timeout_seconds": 900,
        "worker_start_stagger_seconds": 0,
        "baseline_mapping": mapping,
        "predecessor_snapshot_path": relative(SNAPSHOT),
        "predecessor_snapshot_sha256": sha(SNAPSHOT),
        "transition_authority_path": relative(AUTHORITY),
        "transition_authority_sha256": sha(AUTHORITY),
        "source_bindings": {
            name: {"path": relative(path), "sha256": sha(path)}
            for name, path in bindings.items()
        },
    }
    atomic(CONTRACT, contract)
    print(json.dumps({
        "snapshot": str(SNAPSHOT), "snapshot_sha256": sha(SNAPSHOT),
        "contract": str(CONTRACT), "contract_sha256": sha(CONTRACT),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
