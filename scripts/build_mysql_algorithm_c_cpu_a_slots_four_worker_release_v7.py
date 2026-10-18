#!/usr/bin/env python3
"""Freeze v82: serialize only one-worker reset management transactions."""

from __future__ import annotations

import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from scripts import build_mysql_algorithm_c_cpu_a_slots_four_worker_release_v3 as base


ROOT = base.ROOT
SOURCE = ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_cpu_a_slots_20260828_v37"
OUTPUT = ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_cpu_a_slots_20260828_v38"


def main() -> int:
    if OUTPUT.exists():
        raise RuntimeError(f"fresh release directory required: {OUTPUT}")
    OUTPUT.mkdir(parents=True, mode=0o700)
    optimization = base.load(SOURCE / "outer_runtime_optimization.json")
    optimization.update({"created_at": base.now(), "status": "FROZEN_RUNTIME_VALIDATED"})
    optimization_path = OUTPUT / "outer_runtime_optimization.json"
    base.atomic(optimization_path, optimization)

    updates = {
        "controller": base.binding("scripts/run_mysql_algorithm_c_async_scene_v82.py"),
        "launcher": base.binding("scripts/launch_mysql_algorithm_c_async_v82.py"),
        "mixed_worker_reset": base.binding(
            "scripts/reset_mysql_xp_data_planes_mixed_four_worker_v3.py"
        ),
        "release_builder": base.binding(
            "scripts/build_mysql_algorithm_c_cpu_a_slots_four_worker_release_v7.py"
        ),
        "outer_runtime_optimization": base.binding(
            str(optimization_path.relative_to(ROOT))
        ),
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
        "release_version": "v82",
        "created_at": base.now(),
        "status": "FROZEN_RUNTIME_VALIDATED",
        "parent_contract": base.binding(str(parent_path.relative_to(ROOT))),
        "worker_reset_management_serialization": {
            "schema_version": "mysql_xp_worker_reset_management_mutex_v1",
            "scope": "single_worker_data_plane_replacement_only",
            "scene_wave_or_measurement_serialized": False,
            "four_scene_completion_dependency": False,
            "candidate_or_outer_policy_changed": False,
            "workload_or_resource_freeze_changed": False,
            "reason": "prevent concurrent Docker/metadata management timeout",
        },
        "algorithm_a_independent_check_boundary": {
            "algorithm_a_role": "independent_posthoc_check_only",
            "algorithm_a_feedback_to_b_or_c": False,
            "algorithm_a_candidate_qps_range_or_ranking_prior_used_by_b_or_c": False,
            "algorithm_a_candidates_used_by_b_or_c": False,
            "algorithm_a_qps_used_by_b_or_c": False,
            "algorithm_a_parameter_ranges_used_by_b_or_c": False,
            "algorithm_a_search_direction_used_by_b_or_c": False,
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
        "release_version": "v82",
        "global_contract": str(contract_path),
        "global_sha256": base.sha(contract_path),
        "parent_sha256": base.sha(parent_path),
        "optimization_sha256": base.sha(optimization_path),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
