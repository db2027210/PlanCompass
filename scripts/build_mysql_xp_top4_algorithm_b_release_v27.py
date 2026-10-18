#!/usr/bin/env python3
"""Freeze XP Top4-B v37 with two-reject incumbent shrink."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_xp_top4_algorithm_b_release_v1 as builder
from scripts import mysql_outer_qps_strict_progress_v1 as qps_policy
from scripts import mysql_outer_rejection_cycle_v1 as rejection_cycle


builder.OUTPUT = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_b_20260826_v37"
builder.CONTROLLER = ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v30.py"
builder.COORDINATION = builder.OUTPUT / "coordination_contract.json"
builder.CONTRACT = builder.OUTPUT / "algorithm_b_top4_contract.json"
builder.BOOTSTRAP_VERSION = "v37"
builder.SOURCES.update({
    "controller": builder.CONTROLLER,
    "controller_predecessor": ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v29.py",
    "scene_launcher": ROOT / "scripts/launch_mysql_xp_top4_algorithm_b_scene_v19.py",
    "qps_only_outer_policy": ROOT / "scripts/mysql_outer_qps_strict_progress_v1.py",
    "outer_rejection_cycle_policy": ROOT / "scripts/mysql_outer_rejection_cycle_v1.py",
    "top4_runtime_source": ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v1.py",
    "four_lane_scheduler": ROOT / "scripts/mysql_algorithm_b_fresh_parallel_4x_scheduler_v26.py",
    "dynamic_noninterference_gate": ROOT / "scripts/mysql_cross_project_wave_coordination_v8.py",
    "scene_scoped_post_reset_settle": ROOT / "scripts/mysql_xp_post_reset_scene_scoped_settle_v1.py",
    "xp_four_worker_resource_freeze": ROOT / "config/formal/frozen_mysql_xp_cpu_disjoint_slots_20260826_v4/resource_successor.json",
    "current_authority_refresh": ROOT / "scripts/refresh_mysql_xp_combined_v4_live_authority_v2.py",
    "workload_adapter_300s_drain": ROOT / "mysql_workload_adapter_v2.py",
    "hybench_adaptive_runner_300s": ROOT / "config/formal/frozen_mysql_async_outer_20260820_v38/runtime/hybench-adaptive-runner-v4.jar",
})


def main() -> int:
    result = builder.main()
    coordination = builder.load(builder.COORDINATION)
    coordination.update({
        "maximum_quiet_wait_seconds": 180.0,
        "dynamic_gate_release": "v63",
        "parallel_expansion_maximum_workers_per_scene": 4,
        "maximum_concurrent_xp_mysql_workers": 8,
    })
    builder.atomic(builder.COORDINATION, coordination)
    contract = builder.load(builder.CONTRACT)
    contract.update({
        "outer_acceptance_policy": qps_policy.policy_contract(),
        "outer_rejection_cycle_policy": rejection_cycle.policy_contract(),
        "convergence_metric": "maximum_log_domain_width_relative_to_frozen_initial",
        "terminal_normalized_width": 0.01,
        "accepted_round_credit_from_forced_shrink": False,
        "previous_strategy_results_eligible_for_v37_credit": False,
        "algorithm_c_v61_sequence_gate_required": True,
        "inner_candidate_generation_changed": False,
        "inner_candidate_ranking_changed": False,
        "algorithm_a_changed": False,
        "candidate_batches": [[1, 2, 3, 4], [5]],
        "physical_replay_parallelism": 4,
        "graceful_quiescence_seconds": 300,
        "post_search_stability_confirmation": False,
        "maximum_concurrent_xp_mysql_workers": 8,
    })
    contract["source_bindings"]["coordination_contract"] = builder.binding(
        builder.COORDINATION
    )
    builder.atomic(builder.CONTRACT, contract)
    for suffix in ("cpu", "io"):
        path = (
            builder.HISTORY
            / f"v161_to_b_top4_fresh_bootstrap_20260826_v37_{suffix}"
            / "bootstrap_manifest.json"
        )
        manifest = builder.load(path)
        manifest["release_contract_sha256"] = builder.sha(builder.CONTRACT)
        builder.atomic(path, manifest)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
