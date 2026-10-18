#!/usr/bin/env python3
"""Freeze C v86 completed-fallback continuation."""

from __future__ import annotations

import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from scripts import build_mysql_algorithm_c_cpu_a_slots_four_worker_release_v3 as base


ROOT = base.ROOT
SOURCE = ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_cpu_a_slots_20260828_v41"
OUTPUT = ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_cpu_a_slots_20260828_v42"
PARTIAL = (
    ROOT / "history/mysql_four_scene_20260809"
    / "v1022_to_c_cpu_outer17_v84_reset_serial_resume_20260828_xp_cpu_xp_cpu"
)


def main() -> int:
    if OUTPUT.exists():
        raise RuntimeError(f"fresh release directory required: {OUTPUT}")
    OUTPUT.mkdir(parents=True, mode=0o700)
    optimization = base.load(SOURCE / "outer_runtime_optimization.json")
    optimization.update({"created_at": base.now(), "status": "FROZEN_RUNTIME_VALIDATED"})
    optimization_path = OUTPUT / "outer_runtime_optimization.json"
    base.atomic(optimization_path, optimization)
    updates = {
        "controller": base.binding("scripts/run_mysql_algorithm_c_async_scene_v86.py"),
        "launcher": base.binding("scripts/launch_mysql_algorithm_c_async_v86.py"),
        "release_builder": base.binding(
            "scripts/build_mysql_algorithm_c_cpu_a_slots_four_worker_release_v11.py"
        ),
        "outer_runtime_optimization": base.binding(
            str(optimization_path.relative_to(ROOT))
        ),
        "partial_cpu_state": base.binding(str((PARTIAL / "run_state.json").relative_to(ROOT))),
        "partial_cpu_fallback_wave1": base.binding(str((PARTIAL / "outer_018/fallback_plan_change/wave_1/batch_result.json").relative_to(ROOT))),
        "partial_cpu_fallback_wave2": base.binding(str((PARTIAL / "outer_018/fallback_plan_change/wave_2/batch_result.json").relative_to(ROOT))),
    }
    parent = base.load(SOURCE / "algorithm_c_async_contract.json")
    parent.update({"created_at": base.now(), "status": "FROZEN_RUNTIME_VALIDATED"})
    parent_sources = base.refresh(parent.get("source_bindings") or {})
    parent_sources.update(updates)
    parent["source_bindings"] = parent_sources
    parent_path = OUTPUT / "algorithm_c_async_contract.json"
    base.atomic(parent_path, parent)
    contract = base.load(SOURCE / "global_monotonic_incumbent_contract.json")
    contract.update({
        "release_version": "v86",
        "created_at": base.now(),
        "status": "FROZEN_RUNTIME_VALIDATED",
        "parent_contract": base.binding(str(parent_path.relative_to(ROOT))),
        "completed_fallback_measurement_continuation": {
            "scene": "hybench_xp_cpu",
            "outer_attempt": 18,
            "source_release": "v84",
            "completed_top5_physically_replayed": False,
            "completed_fallback_physically_replayed": False,
            "candidate_vectors_reverified": True,
            "new_physical_work_starts_at": "current_interval_plan_coverage",
            "accepted_or_rejected_credit_for_recovery": 0,
            "candidate_or_outer_policy_changed": False,
            "algorithm_a_input_used": False,
        },
    })
    sources = base.refresh(contract.get("source_bindings") or {})
    sources.update(updates)
    sources.update({
        "parent_contract": base.binding(str(parent_path.relative_to(ROOT))),
        "parent_optimization_contract": base.binding(
            str(optimization_path.relative_to(ROOT))
        ),
    })
    contract["source_bindings"] = sources
    contract_path = OUTPUT / "global_monotonic_incumbent_contract.json"
    base.atomic(contract_path, contract)
    print(json.dumps({
        "status": "FROZEN_RUNTIME_VALIDATED",
        "release_version": "v86",
        "global_contract": str(contract_path),
        "global_sha256": base.sha(contract_path),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
