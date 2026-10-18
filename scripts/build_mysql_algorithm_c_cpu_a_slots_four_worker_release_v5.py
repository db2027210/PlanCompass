#!/usr/bin/env python3
"""Freeze v80: add PG-equivalent current-interval plan coverage to MySQL C."""

from __future__ import annotations

import json
from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from scripts import build_mysql_algorithm_c_cpu_a_slots_four_worker_release_v3 as base


ROOT = base.ROOT
SOURCE = ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_cpu_a_slots_20260828_v31"
OUTPUT = ROOT / "config/formal/frozen_mysql_four_scene_algorithm_c_cpu_a_slots_20260828_v36"
PG_ALGORITHM_C = Path(
    "/home/anonymous/program/resource_adaptive/shell/run_pg_algorithm_c_scene.py"
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
        "controller": base.binding("scripts/run_mysql_algorithm_c_async_scene_v80.py"),
        "launcher": base.binding("scripts/launch_mysql_algorithm_c_async_v80.py"),
        "lane_paired_boundary_runtime": base.binding(
            "scripts/run_mysql_four_scene_algorithm_c_boundary_resume_v35.py"
        ),
        "current_interval_coverage_generator": base.binding(
            "scripts/mysql_algorithm_c_global_coverage_v1.py"
        ),
        "pg_algorithm_c_reference": {
            "path": str(PG_ALGORITHM_C),
            "sha256": base.sha(PG_ALGORITHM_C),
            "read_only_reference": True,
        },
        "release_builder": base.binding(
            "scripts/build_mysql_algorithm_c_cpu_a_slots_four_worker_release_v5.py"
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
        "release_version": "v80",
        "created_at": base.now(),
        "status": "FROZEN_RUNTIME_VALIDATED",
        "parent_contract": base.binding(str(parent_path.relative_to(ROOT))),
        "current_interval_plan_coverage": {
            "schema_version": "mysql_algorithm_c_current_interval_plan_coverage_v1",
            "pg_semantic_reference": str(PG_ALGORITHM_C),
            "trigger_after_consecutive_rejections": 1,
            "coverage_precedes_second_rejection_shrink": True,
            "sample_count": 256,
            "sparse_fractions": [0.20, 0.35, 0.65, 0.80],
            "pair_fractions": [0.25, 0.75],
            "low_discrepancy_design": "halton",
            "current_formal_interval_only": True,
            "full_plan_distinct_admission": True,
            "admitted_physical_top_k": 5,
            "qps_used_for_generation_or_ranking": False,
            "external_algorithm_evidence_used": False,
            "algorithm_a_candidate_injection": False,
            "candidate_space_exhaustion_requires_coverage": True,
        },
        "runtime_alias_binding": {
            "all_loaded_legacy_boundary_runtime_aliases_rebound": True,
            "v4_original_callbacks_rebound": True,
            "stale_v2_main_reachable": False,
            "candidate_or_outer_policy_changed": True,
            "workload_or_resource_freeze_changed": False,
        },
        "rerun_policy": {
            "algorithm_a_changed": False,
            "algorithm_b_required": False,
            "algorithm_c_scenes": [
                "job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io"
            ],
            "old_algorithm_c_results_formal_eligible": False,
            "fresh_r0_required": False,
            "resume_from_last_valid_completed_round": True,
            "discard_only_incomplete_or_invalid_shrink_round": True,
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
        "release_version": "v80",
        "global_contract": str(contract_path),
        "global_sha256": base.sha(contract_path),
        "parent_sha256": base.sha(parent_path),
        "optimization_sha256": base.sha(optimization_path),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
