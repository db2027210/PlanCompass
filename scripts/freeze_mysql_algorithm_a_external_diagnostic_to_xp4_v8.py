#!/usr/bin/env python3
"""Freeze the v129 XP4 handoff with parallel scene-scoped resets."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import freeze_mysql_algorithm_a_external_diagnostic_to_xp4_v7 as v7


CONFIG = ROOT / "config/formal/frozen_mysql_algorithm_a_external_diagnostic_to_xp4_20260824_v8"
CONTRACT = CONFIG / "transition_contract.json"


def main() -> int:
    bindings = {
        "xp4_monitor": ROOT / "scripts/monitor_mysql_algorithm_a_external_diagnostic_to_xp4_v6.py",
        "xp4_monitor_base": ROOT / "scripts/monitor_mysql_algorithm_a_external_diagnostic_to_xp4_v5.py",
        "xp4_monitor_implementation": ROOT / "scripts/monitor_mysql_algorithm_a_job_to_xp_slots_v1.py",
        "xp4_executor": ROOT / "scripts/execute_mysql_algorithm_a_external_diagnostic_to_xp4_v5.py",
        "xp4_executor_base": ROOT / "scripts/execute_mysql_algorithm_a_external_diagnostic_to_xp4_v1.py",
        "xp4_resume": ROOT / "scripts/run_mysql_algorithm_a_job_to_xp_external_diagnostic_fast_reset_resume_v1.py",
        "xp_parallel_reset": ROOT / "scripts/mysql_async_xp_post_reset_settle_v2.py",
        "xp_settle_predecessor": ROOT / "scripts/mysql_async_xp_post_reset_settle_v1.py",
        "current_algorithm_a_controller": ROOT / "scripts/run_mysql_algorithm_a_async_scene_v3.py",
        "external_diagnostic_gate": ROOT / "scripts/mysql_external_workload_diagnostic_gate_v1.py",
        "upstream_job4_contract": v7.UPSTREAM,
        "current_job4_resume_contract": v7.JOB_RESUME,
        "current_xp_cpu_w1_recovery_contract": (
            ROOT / "config/formal/frozen_mysql_algorithm_a_xp_cpu_w1_reset_recovery_20260824_v1/transition_contract.json"
        ),
    }
    document = {
        "schema_version": "mysql_algorithm_a_job_to_xp_slot_contract_v1",
        "status": "FROZEN",
        "release_version": "external_diagnostic_xp4_v8_fast_reset",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "algorithm": "A",
        "contract": "mysql_algorithm_a_job_four_slots_to_xp_2_to_4_v1",
        "trigger_requires_job_complete_10": True,
        "tpch_complete_10_required": True,
        "both_memory_scenes_complete_before_handoff": True,
        "job_nonconvergence_does_not_release_slots": True,
        "job_run": "v129_to_a_job_valid_replica_refresh_pg_order_resume_20260824_attempt001_job",
        "xp_cpu_run": "v133_to_a_xp_cpu_w1_reset_external_diagnostic_resume_20260824_attempt001_xp_cpu",
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
        "measurement_duration_policy_unchanged": True,
        "candidate_set_changed": False,
        "outer_loop_semantics_changed": False,
        "worker_reset_parallelism_after_handoff": 4,
        "worker_reset_serialization_removed": True,
        "cross_scene_reset_lock_used": False,
        "shared_nvme_lease_used": False,
        "post_reset_syncfs_retained": True,
        "target_cgroup_quiet_gate_retained": True,
        "cpu_pinning_retained": True,
        "numa_exclusive_scheduler_lock_required": False,
        "effective_numa_and_memory_values_read_back": True,
        "rolling_dynamic_noninterference_gate_retained": False,
        "external_workloads_diagnostic_only": True,
        "external_workload_wait_required": False,
        "external_process_control_or_stop_prohibited": True,
        "postgresql_process_control_or_stop_prohibited": True,
        "source_resource_freeze": {
            "path": v7.relative(v7.SOURCE_FREEZE), "sha256": v7.sha(v7.SOURCE_FREEZE),
        },
        "upstream_job4_transition_contract": {
            "path": v7.relative(v7.UPSTREAM), "sha256": v7.sha(v7.UPSTREAM),
        },
        "current_job4_resume_contract": {
            "path": v7.relative(v7.JOB_RESUME), "sha256": v7.sha(v7.JOB_RESUME),
        },
        "source_bindings": {
            name: {"path": v7.relative(path), "sha256": v7.sha(path)}
            for name, path in bindings.items()
        },
    }
    CONFIG.mkdir(parents=True, exist_ok=True)
    temporary = CONTRACT.with_name(f".{CONTRACT.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, CONTRACT)
    print(json.dumps({"contract": str(CONTRACT), "sha256": v7.sha(CONTRACT)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
