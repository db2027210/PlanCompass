#!/usr/bin/env python3
"""Freeze the rank-12 JOB partial continuation and v12 timer correction."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
MEASUREMENT_CONFIG = (
    ROOT / "config/formal/frozen_mysql_job_cache_state_20260824_v12"
)
MEASUREMENT_CONTRACT = MEASUREMENT_CONFIG / "cache_state_contract.json"
RESUME_CONFIG = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_job_partial_suite_timer_resume_20260824_v1"
)
RESUME_CONTRACT = RESUME_CONFIG / "resume_contract.json"
PREDECESSOR = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v113_to_a_job4_no_recalibration_stagger_resume_20260824_attempt001_job"
    / "algorithm_a_state.json"
)
ORIGINAL_BOUNDARY = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v99_to_a_external_diagnostic_resume_20260823_job_dynamic_slots_v1"
    / "job_memory.predecessor_boundary_snapshot.json"
)
AUTHORITY = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v99_to_a_external_diagnostic_resume_20260823_job_dynamic_slots_v1"
    / "job_memory.transition_authority.json"
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
    predecessor = load(PREDECESSOR)
    checkpoint = predecessor.get("round_checkpoint") or {}
    if (
        predecessor.get("algorithm") != "A"
        or predecessor.get("async_scene") != "job_memory"
        or predecessor.get("formal_scope") != "mysql_only"
        or predecessor.get("cross_engine_comparison_prohibited") is not True
        or int(predecessor.get("accepted") or 0) != 2
        or int(predecessor.get("round_index") or 0) != 3
        or int(predecessor.get("physical_replay_parallelism") or 0) != 4
        or checkpoint.get("attempted_ranks") != list(range(1, 13))
        or [row.get("ranks") for row in checkpoint.get("waves") or []]
        != [[1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12]]
    ):
        raise RuntimeError("JOB v113 rank-12 predecessor differs")

    wave3 = (
        PREDECESSOR.parent / "round_003/wave_003/attempt_01/batch_result.json"
    )
    evidence = load(wave3)
    row = (evidence.get("measurements") or {}).get("job_w3") or {}
    deadline_rows = (
        (evidence.get("scored_suite_hard_timeout") or {}).get("worker_suites") or []
    )
    deadline = next(
        (item for item in deadline_rows if "/pair_3/job" in str(item.get("output_dir") or "")),
        None,
    )
    if (
        row.get("rank") != 11
        or row.get("coverage_complete") is not True
        or row.get("hard_gate_passed") is not False
        or not isinstance(deadline, dict)
        or deadline.get("deadline_triggered") is not False
        or float(deadline.get("elapsed_seconds") or 0) > 900
    ):
        raise RuntimeError("JOB rank-11 per-suite timer evidence differs")

    measurement_bindings = {
        "measurement_v12": ROOT / "scripts/mysql_job_buffer_pool_preconditioned_measurement_v12.py",
        "measurement_v11": ROOT / "scripts/mysql_job_buffer_pool_preconditioned_measurement_v11.py",
        "measurement_v11_contract": (
            ROOT / "config/formal/frozen_mysql_job_cache_state_20260824_v11"
            / "cache_state_contract.json"
        ),
    }
    atomic(MEASUREMENT_CONTRACT, {
        "schema_version": "mysql_job_cache_state_contract_v12",
        "release_version": "v12",
        "status": "FROZEN_RUNTIME_VALIDATED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene": "job_memory",
        "full_job_113_once_per_measurement": True,
        "physical_replay_parallelism": 4,
        "worker_start_stagger_seconds": 90,
        "per_suite_hard_timeout_seconds": 900,
        "per_suite_deadline_begins_after_start_offset": True,
        "batch_start_offset_excluded_from_suite_elapsed": True,
        "deadline_triggered_suite_can_be_reclassified_valid": False,
        "incomplete_suite_can_be_reclassified_valid": False,
        "candidate_set_changed": False,
        "outer_loop_semantics_changed": False,
        "resource_envelope_changed": False,
        "observed_false_technical_evidence": {
            "batch_result_path": relative(wave3),
            "batch_result_sha256": sha(wave3),
            "worker_id": "job_w3",
            "rank": 11,
            "batch_elapsed_seconds": row["resource_stability"]["elapsed_seconds"],
            "per_suite_elapsed_seconds": deadline["elapsed_seconds"],
            "deadline_triggered": False,
            "coverage_complete": True,
        },
        "source_bindings": {
            name: {"path": relative(path), "sha256": sha(path)}
            for name, path in measurement_bindings.items()
        },
    })

    bindings = {
        "controller": ROOT / "scripts/run_mysql_algorithm_a_job_partial_per_suite_timer_resume_v1.py",
        "freezer": Path(__file__).resolve(),
        "measurement_v12": ROOT / "scripts/mysql_job_buffer_pool_preconditioned_measurement_v12.py",
        "measurement_v12_contract": MEASUREMENT_CONTRACT,
        "partial_round_controller": ROOT / "scripts/run_mysql_algorithm_a_partial_round_codefix_resume_v2.py",
        "partial_round_contract": (
            ROOT / "config/formal/frozen_mysql_algorithm_a_partial_round_resume_20260822_v1"
            / "partial_round_resume_contract.json"
        ),
        "algorithm_a_controller": ROOT / "scripts/run_mysql_algorithm_a_async_scene_v3.py",
        "external_diagnostic_gate": ROOT / "scripts/mysql_external_workload_diagnostic_gate_v1.py",
        "transition_authority": AUTHORITY,
        "original_boundary": ORIGINAL_BOUNDARY,
        "predecessor_state": PREDECESSOR,
        "predecessor_wave3": wave3,
    }
    atomic(RESUME_CONTRACT, {
        "schema_version": "mysql_algorithm_a_job_partial_suite_timer_resume_v1",
        "status": "FROZEN_RUNTIME_VALIDATED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "algorithm": "A",
        "scene": "job_memory",
        "accepted_target": 10,
        "preserved_accepted_count": 2,
        "physical_replay_parallelism": 4,
        "interrupted_round_index": 3,
        "cached_attempted_ranks": list(range(1, 13)),
        "new_physical_work_starts_at_rank": 13,
        "cached_prefix_physically_replayed": False,
        "cached_prefix_reclassified_with_v12": True,
        "slot_expansion_recalibration_repeated": False,
        "candidate_set_changed": False,
        "outer_loop_semantics_changed": False,
        "resource_envelope_changed": False,
        "external_workloads_diagnostic_only": True,
        "external_workload_wait_required": False,
        "external_process_control_or_stop_prohibited": True,
        "predecessor_state_path": relative(PREDECESSOR),
        "predecessor_state_sha256": sha(PREDECESSOR),
        "transition_authority_path": relative(AUTHORITY),
        "transition_authority_sha256": sha(AUTHORITY),
        "source_bindings": {
            name: {"path": relative(path), "sha256": sha(path)}
            for name, path in bindings.items()
        },
    })
    print(json.dumps({
        "measurement_contract": str(MEASUREMENT_CONTRACT),
        "measurement_contract_sha256": sha(MEASUREMENT_CONTRACT),
        "resume_contract": str(RESUME_CONTRACT),
        "resume_contract_sha256": sha(RESUME_CONTRACT),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
