#!/usr/bin/env python3
"""Freeze the v113-aware JOB-to-XP4 completion monitor contract."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CONFIG = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_a_external_diagnostic_to_xp4_20260824_v5"
)
CONTRACT = CONFIG / "transition_contract.json"
SOURCE_FREEZE = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_a_job_external_diagnostic_slots_20260823_v1"
    / "job_four_worker_resource_successor.json"
)
UPSTREAM = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_a_job_external_diagnostic_slots_20260823_v1"
    / "transition_contract.json"
)
JOB_RESUME = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_a_job4_no_recalibration_stagger_resume_20260824_v1"
    / "resume_contract.json"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def relative(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    os.replace(temporary, path)


def main() -> int:
    bindings = {
        "xp4_monitor": ROOT / "scripts/monitor_mysql_algorithm_a_external_diagnostic_to_xp4_v3.py",
        "xp4_monitor_base": ROOT / "scripts/monitor_mysql_algorithm_a_all_scene_to_xp4_v5.py",
        "xp4_monitor_implementation": ROOT / "scripts/monitor_mysql_algorithm_a_job_to_xp_slots_v1.py",
        "xp4_executor": ROOT / "scripts/execute_mysql_algorithm_a_external_diagnostic_to_xp4_v2.py",
        "xp4_executor_external_base": ROOT / "scripts/execute_mysql_algorithm_a_external_diagnostic_to_xp4_v1.py",
        "xp4_executor_base": ROOT / "scripts/execute_mysql_algorithm_a_job_to_xp_scene_v1.py",
        "xp4_resume": ROOT / "scripts/run_mysql_algorithm_a_job_to_xp_external_diagnostic_resume_v1.py",
        "xp4_resume_base": ROOT / "scripts/run_mysql_algorithm_a_job_to_xp_resume_v1.py",
        "current_algorithm_a_controller": ROOT / "scripts/run_mysql_algorithm_a_async_scene_v3.py",
        "external_diagnostic_gate": ROOT / "scripts/mysql_external_workload_diagnostic_gate_v1.py",
        "upstream_job4_contract": UPSTREAM,
        "current_job4_resume_contract": JOB_RESUME,
        "xp_io_w1_reset_recovery_contract": (
            ROOT
            / "config/formal/frozen_mysql_algorithm_a_xp_io_w1_reset_recovery_20260824_v1"
            / "transition_contract.json"
        ),
    }
    contract = {
        "schema_version": "mysql_algorithm_a_job_to_xp_slot_contract_v1",
        "status": "FROZEN",
        "release_version": "external_diagnostic_xp4_v5",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "algorithm": "A",
        "contract": "mysql_algorithm_a_job_four_slots_to_xp_2_to_4_v1",
        "trigger_requires_job_complete_10": True,
        "tpch_complete_10_required": True,
        "both_memory_scenes_complete_before_handoff": True,
        "job_nonconvergence_does_not_release_slots": True,
        "job_run": "v113_to_a_job4_no_recalibration_stagger_resume_20260824_attempt001_job",
        "xp_parallelism": {"before": 2, "after": 4},
        "released_slot_mapping": {
            "job_w1": "xp_cpu_w3", "job_w2": "xp_cpu_w4",
            "job_w3": "xp_io_w3", "job_w4": "xp_io_w4",
        },
        "per_scene_independent_transition": True,
        "xp_cpu_does_not_wait_for_xp_io": True,
        "xp_io_does_not_wait_for_xp_cpu": True,
        "round_boundary_required": True,
        "partial_next_round_archived_and_not_credited": True,
        "mixed_worker_cohort_round_prohibited": True,
        "accepted_ranges_incumbent_history_preserved": True,
        "historical_accepted_rounds_preserved": True,
        "canonical_baseline_preserved": True,
        "fresh_baseline_repetitions_per_worker": 2,
        "incumbent_replay_required": True,
        "resource_recalibration_required": False,
        "post_convergence_stability_required": False,
        "native_xp_workload_unchanged": True,
        "cpu_pinning_retained": True,
        "numa_exclusive_scheduler_lock_required": False,
        "effective_numa_and_memory_values_read_back": True,
        "rolling_dynamic_noninterference_gate_retained": False,
        "external_workloads_diagnostic_only": True,
        "external_workload_wait_required": False,
        "external_process_control_or_stop_prohibited": True,
        "postgresql_process_control_or_stop_prohibited": True,
        "source_resource_freeze": {
            "path": relative(SOURCE_FREEZE), "sha256": sha(SOURCE_FREEZE),
        },
        "upstream_job4_transition_contract": {
            "path": relative(UPSTREAM), "sha256": sha(UPSTREAM),
        },
        "current_job4_resume_contract": {
            "path": relative(JOB_RESUME), "sha256": sha(JOB_RESUME),
        },
        "source_bindings": {
            name: {"path": relative(path), "sha256": sha(path)}
            for name, path in bindings.items()
        },
    }
    atomic(CONTRACT, contract)
    print(json.dumps({"contract": str(CONTRACT), "sha256": sha(CONTRACT)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
