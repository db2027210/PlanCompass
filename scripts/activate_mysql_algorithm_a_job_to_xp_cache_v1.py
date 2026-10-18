#!/usr/bin/env python3
"""Arm the v41 JOB-to-XP handoff after the cache-fixed JOB expansion."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in os.sys.path:
    os.sys.path.insert(0, str(ROOT))

from scripts import activate_mysql_algorithm_a_job_to_xp_after_expand_v1 as legacy


HISTORY = ROOT / "history/mysql_four_scene_20260809"
LOG_ROOT = HISTORY / "formal_launch_logs"
CAMPAIGN = "v41_to_a_cache_fixed_20260823"

legacy.CAMPAIGN = CAMPAIGN
legacy.EXPANSION_RESULT = (
    LOG_ROOT / "v41_to_a_cache_fixed_tpch_to_job4_handoff.json"
)
legacy.SOURCE_FREEZE = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_job_cache_dynamic_slots_20260823_v1"
    / "job_four_worker_resource_successor.json"
)
legacy.CONTRACT_DIR = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_job_to_xp_slots_20260823_v3"
)
legacy.CONTRACT_PATH = legacy.CONTRACT_DIR / "transition_contract.json"
legacy.MONITOR = ROOT / "scripts/monitor_mysql_algorithm_a_job_to_xp_slots_cache_v1.py"
legacy.MONITOR_STATE = LOG_ROOT / f"{CAMPAIGN}.job_to_xp_v3.monitor.json"
legacy.MONITOR_UNIT = "mysql-alga-v41-job-to-xp-monitor-v3.service"


def seal_contract(expansion: Mapping[str, Any]) -> dict[str, Any]:
    if not legacy.SOURCE_FREEZE.is_file():
        raise RuntimeError("cache-fixed four-worker JOB resource successor is absent")
    source_sha = legacy.sha(legacy.SOURCE_FREEZE)
    if expansion.get("successor_freeze_sha256") != source_sha:
        raise RuntimeError("cache-fixed JOB expansion resource successor differs")
    bindings = {
        "slot_planner": "scripts/mysql_algorithm_a_job_to_xp_slots_v1.py",
        "provisioner_adapter": "scripts/provision_mysql_algorithm_a_job_to_xp_workers_v1.py",
        "resume_adapter": "scripts/run_mysql_algorithm_a_job_to_xp_resume_v1.py",
        "resume_current_controller": "scripts/run_mysql_algorithm_a_job_to_xp_resume_v2.py",
        "scene_executor_base": "scripts/execute_mysql_algorithm_a_job_to_xp_scene_v1.py",
        "scene_executor": "scripts/execute_mysql_algorithm_a_job_to_xp_scene_cache_v1.py",
        "monitor_base": "scripts/monitor_mysql_algorithm_a_job_to_xp_slots_v1.py",
        "monitor": "scripts/monitor_mysql_algorithm_a_job_to_xp_slots_cache_v1.py",
        "activation": "scripts/activate_mysql_algorithm_a_job_to_xp_cache_v1.py",
        "physical_xp_provisioner": "scripts/provision_mysql_algorithm_a_xp_slot_workers_v1.py",
        "live_xp_authority_helper": "scripts/execute_mysql_algorithm_a_xp_slot_transition_v1.py",
        "algorithm_a_controller": "scripts/run_mysql_algorithm_a_async_scene_v3.py",
        "v65_coordination_installer": "scripts/run_mysql_algorithm_c_async_scene_v49.py",
    }
    value = {
        "schema_version": "mysql_algorithm_a_job_to_xp_slot_contract_v1",
        "status": "FROZEN", "release_version": "v3_cache_fixed",
        "created_at": legacy.now(), "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "algorithm": "A", "campaign_id": CAMPAIGN,
        "contract": "mysql_algorithm_a_job_four_slots_to_xp_2_to_4_v1",
        "trigger_requires_job_complete_10": True,
        "tpch_complete_required_before_job_expansion": True,
        "job_nonconvergence_does_not_release_slots": True,
        "xp_parallelism": {"before": 2, "after": 4},
        "released_slot_mapping": {
            "job_w1": "xp_cpu_w3", "job_w2": "xp_cpu_w4",
            "job_w3": "xp_io_w3", "job_w4": "xp_io_w4",
        },
        "per_scene_independent_transition": True,
        "xp_cpu_does_not_wait_for_xp_io": True,
        "xp_io_does_not_wait_for_xp_cpu": True,
        "already_terminal_xp_is_not_restarted": True,
        "round_boundary_required": True,
        "partial_next_round_archived_and_not_credited": True,
        "mixed_worker_cohort_round_prohibited": True,
        "accepted_ranges_incumbent_history_preserved": True,
        "canonical_baseline_preserved": True,
        "fresh_baseline_repetitions_per_worker": 2,
        "incumbent_replay_required": True,
        "post_convergence_stability_required": False,
        "native_xp_workload_unchanged": True,
        "native_xp_constraints": {
            "iq_selection": "IQ1-IQ7_uniform_random",
            "at_percentages": "35/25/15/15/7/3",
            "xap_xtp_ratio_unchanged": True,
            "workload_mix_override_prohibited": True,
        },
        "source_resource_freeze": {
            "path": str(legacy.SOURCE_FREEZE.relative_to(ROOT)),
            "sha256": source_sha,
        },
        "predecessor_algorithm_a_contract": {
            "path": "config/formal/frozen_mysql_algorithm_a_async_20260822_v4/algorithm_a_contract.json",
            "sha256": legacy.sha(
                ROOT / "config/formal/frozen_mysql_algorithm_a_async_20260822_v4"
                / "algorithm_a_contract.json"
            ),
        },
        "source_bindings": {
            name: {"path": path, "sha256": legacy.sha(ROOT / path)}
            for name, path in bindings.items()
        },
        "job_expansion_result": {
            "path": str(legacy.EXPANSION_RESULT),
            "sha256": legacy.sha(legacy.EXPANSION_RESULT),
        },
    }
    if legacy.CONTRACT_PATH.is_file():
        prior = legacy.load(legacy.CONTRACT_PATH)
        if (
            prior.get("status") != "FROZEN"
            or (prior.get("source_resource_freeze") or {}).get("sha256") != source_sha
            or prior.get("source_bindings") != value["source_bindings"]
        ):
            raise RuntimeError("existing cache-fixed JOB-to-XP contract differs")
        return prior
    legacy.atomic(legacy.CONTRACT_PATH, value)
    return value


legacy.seal_contract = seal_contract


if __name__ == "__main__":
    raise SystemExit(legacy.main())
