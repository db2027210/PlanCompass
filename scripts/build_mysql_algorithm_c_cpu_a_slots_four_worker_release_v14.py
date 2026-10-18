#!/usr/bin/env python3
"""Freeze C v89 exact IO post-fallback continuation."""

from __future__ import annotations

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import build_mysql_algorithm_c_cpu_a_slots_four_worker_release_v3 as base


SOURCE = ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_cpu_a_slots_20260828_v44"
OUTPUT = ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_cpu_a_slots_20260828_v45"
PARTIAL = ROOT / "history/mysql_four_scene_20260809/v1023_to_c_io_outer12_v84_reset_serial_resume_20260828_xp_io_xp_io"
BASELINE = ROOT / "history/mysql_four_scene_20260809/v1033_c_io_outer12_checkpoint_incumbent_baseline_20260828/checkpoint_incumbent_baseline.json"


def main() -> int:
    if OUTPUT.exists():
        raise RuntimeError(f"fresh release directory required: {OUTPUT}")
    OUTPUT.mkdir(parents=True, mode=0o700)
    optimization = base.load(SOURCE / "outer_runtime_optimization.json")
    optimization.update({"created_at": base.now(), "status": "FROZEN_RUNTIME_VALIDATED"})
    optimization_path = OUTPUT / "outer_runtime_optimization.json"
    base.atomic(optimization_path, optimization)
    updates = {
        "controller": base.binding("scripts/run_mysql_algorithm_c_async_scene_v89.py"),
        "launcher": base.binding("scripts/launch_mysql_algorithm_c_async_v89.py"),
        "release_builder": base.binding("scripts/build_mysql_algorithm_c_cpu_a_slots_four_worker_release_v14.py"),
        "checkpoint_incumbent_baseline_builder_v2": base.binding("scripts/build_mysql_algorithm_c_checkpoint_incumbent_baseline_v2.py"),
        "current_io_checkpoint_incumbent_baseline": base.binding(str(BASELINE.relative_to(ROOT))),
        "partial_io_state": base.binding(str((PARTIAL / "run_state.json").relative_to(ROOT))),
        "partial_io_ranking": base.binding(str((PARTIAL / "outer_013/predicted_rankings.json").relative_to(ROOT))),
        "partial_io_top5_wave1": base.binding(str((PARTIAL / "outer_013/top5_batch_1/batch_result.json").relative_to(ROOT))),
        "partial_io_top5_wave2": base.binding(str((PARTIAL / "outer_013/top5_batch_2/batch_result.json").relative_to(ROOT))),
        "partial_io_fallback_wave1": base.binding(str((PARTIAL / "outer_013/fallback_plan_change/wave_1/batch_result.json").relative_to(ROOT))),
        "partial_io_fallback_wave2": base.binding(str((PARTIAL / "outer_013/fallback_plan_change/wave_2/batch_result.json").relative_to(ROOT))),
        "partial_io_coverage_gate": base.binding(str((PARTIAL / "outer_013/current_interval_plan_coverage/wave_1_cross_project_wave_quiet_gate_attempt_001.json").relative_to(ROOT))),
        "outer_runtime_optimization": base.binding(str(optimization_path.relative_to(ROOT))),
    }
    parent = base.load(SOURCE / "algorithm_c_async_contract.json")
    parent.update({"created_at": base.now(), "status": "FROZEN_RUNTIME_VALIDATED"})
    parent_sources = base.refresh(parent.get("source_bindings") or {})
    parent_sources.update(updates)
    parent["source_bindings"] = parent_sources
    carries = dict(parent.get("partial_incumbent_carry_forward_baselines") or {})
    carries["hybench_xp_io"] = base.binding(str(BASELINE.relative_to(ROOT)))
    parent["partial_incumbent_carry_forward_baselines"] = carries
    parent_path = OUTPUT / "algorithm_c_async_contract.json"
    base.atomic(parent_path, parent)
    contract = base.load(SOURCE / "global_monotonic_incumbent_contract.json")
    contract.update({
        "release_version": "v89",
        "created_at": base.now(),
        "status": "FROZEN_RUNTIME_VALIDATED",
        "parent_contract": base.binding(str(parent_path.relative_to(ROOT))),
        "post_fallback_partial_boundary": {
            "scene": "hybench_xp_io", "outer_attempt": 13,
            "completed_top5_real_candidate_count": 1,
            "completed_fallback_real_candidate_count": 1,
            "coverage_gate_passed": True,
            "coverage_measurement_started": False,
            "new_physical_work_starts_at": "current_interval_plan_coverage",
            "accepted_or_rejected_credit_for_recovery": 0,
            "candidate_or_outer_policy_changed": False,
            "algorithm_a_input_used": False,
        },
        "completed_fallback_measurement_continuation": {
            "scene": "hybench_xp_io", "outer_attempt": 13,
            "source_release": "v84",
            "completed_top5_physically_replayed": False,
            "completed_fallback_physically_replayed": False,
            "candidate_vectors_reverified": True,
            "new_physical_work_starts_at": "current_interval_plan_coverage",
            "accepted_or_rejected_credit_for_recovery": 0,
            "candidate_or_outer_policy_changed": False,
            "algorithm_a_input_used": False,
        },
        "current_checkpoint_incumbent_baseline": {
            "scene": "hybench_xp_io", "outer_attempt": 13,
            "worker_count": 4, "source_accepted_outer": 11,
            "latest_completed_outer_was_rejected": True,
            "physical_incumbent_remeasurement": False,
            "accepted_or_rejected_credit": 0,
            "candidate_or_outer_policy_changed": False,
            "algorithm_a_input_used": False,
        },
    })
    sources = base.refresh(contract.get("source_bindings") or {})
    sources.update(updates)
    sources.update({
        "parent_contract": base.binding(str(parent_path.relative_to(ROOT))),
        "parent_optimization_contract": base.binding(str(optimization_path.relative_to(ROOT))),
    })
    contract["source_bindings"] = sources
    contract_path = OUTPUT / "global_monotonic_incumbent_contract.json"
    base.atomic(contract_path, contract)
    print(json.dumps({"status": "FROZEN_RUNTIME_VALIDATED", "release_version": "v89", "global_contract": str(contract_path), "global_sha256": base.sha(contract_path)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
