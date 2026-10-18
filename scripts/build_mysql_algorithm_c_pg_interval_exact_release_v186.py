#!/usr/bin/env python3
"""Seal the PostgreSQL-exact MySQL Algorithm C interval successor."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PG_ROOT = ROOT.parent / "resource_adaptive"
PARENT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_paper_baseline_20260923_v156/"
    "algorithm_c_paper_baseline_contract.json"
)
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_interval_exact_20260929_v186/"
    "algorithm_c_pg_interval_exact_contract.json"
)
SOURCES = {
    "release_builder": Path(__file__).resolve(),
    "mysql_controller": ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v96.py",
    "mysql_runtime": ROOT / "scripts/run_mysql_four_scene_algorithm_c_boundary_resume_v54.py",
    "mysql_outer_policy": ROOT / "scripts/mysql_algorithm_c_pg_outer_policy_v1.py",
    "mysql_interval_regions": ROOT / "scripts/mysql_algorithm_c_interval_control_v2.py",
    "mysql_rejection_cycle": ROOT / "scripts/mysql_outer_rejection_cycle_v3.py",
    "correspondence_test": ROOT / "tests/test_mysql_algorithm_c_pg_correspondence_v2.py",
    "postgresql_adaptive_policy": PG_ROOT / "algorithm_c_adaptive_search.py",
    "postgresql_adaptive_controller": PG_ROOT / "shell/run_pg_algorithm_c_budgeted_scene.py",
    "postgresql_outer_controller": PG_ROOT / "shell/run_pg_algorithm_c_scene.py",
    "postgresql_region_policy": PG_ROOT / "algorithm_c_budget_policy.py",
    "postgresql_binary_interval": PG_ROOT / "common_outer_loop.py",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if CONTRACT.exists() or CONTRACT.parent.exists():
        raise RuntimeError("v186 release already exists")
    parent = json.loads(PARENT.read_text(encoding="utf-8"))
    if (
        parent.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or parent.get("formal_scope") != "mysql_only"
        or parent.get("cross_engine_comparison_prohibited") is not True
    ):
        raise RuntimeError("v186 parent resource release differs")
    bindings = {}
    for name, path in SOURCES.items():
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"regular source required: {path}")
        try:
            bound = str(path.relative_to(ROOT))
        except ValueError:
            bound = str(path)
        bindings[name] = {"path": bound, "sha256": sha(path)}
    value = {
        "schema_version": "mysql_algorithm_c_pg_interval_exact_release_v1",
        "release_version": "v186-pg-interval-exact-fresh-search",
        "status": "FROZEN_RUNTIME_VALIDATED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "parent_resource_release": {
            "path": str(PARENT.relative_to(ROOT)),
            "sha256": sha(PARENT),
        },
        "source_bindings": bindings,
        "fresh_search_required": True,
        "historical_interval_state_reuse_prohibited": True,
        "old_completed_rounds_are_historical_only": True,
        "old_results_may_not_be_merged_with_v186": True,
        "pg_loop_correspondence": {
            "semantic_miss_unit": "one_complete_technically_valid_candidate_batch",
            "first_complete_valid_miss": "expand_next_batch",
            "second_complete_valid_miss": "keep_transformed_half_containing_incumbent",
            "accepted_shrink": "declared_active_subset_intersect_moved_subset",
            "acceptance_resets_miss_counter": True,
            "maximum_actual_normalized_width_threshold": 0.01,
            "post_search_stability_confirmation": False,
            "fixed_round_or_wall_stop": False,
            "empty_or_technical_batch_miss_credit": 0,
        },
        "retained_region_correspondence": {
            "complement_partition": "non_overlapping_ordered_slabs",
            "queued_and_visited_identity_deduplication": True,
            "selection_priority": "resource_potential_then_oldest_creation",
            "reopened_width_counts_toward_actual_width": True,
        },
        "acceptance_correspondence": {
            "tiers": ["strict", "secondary", "fallback"],
            "qps_reference": "canonical_baseline_then_last_accepted_incumbent",
            "strict_positive_qps_gain_threshold": 0.0,
            "qps_used_for_inner_scoring": False,
            "non_target_bottleneck_threshold": 0.8,
        },
        "resource_and_workload_changes": False,
        "candidate_generation_model_changes": False,
        "postgresql_process_control_or_stop_performed": False,
    }
    CONTRACT.parent.mkdir(parents=True, mode=0o700)
    CONTRACT.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    CONTRACT.with_suffix(CONTRACT.suffix + ".sha256").write_text(
        f"{sha(CONTRACT)}  {CONTRACT.name}\n",
        encoding="ascii",
    )
    print(json.dumps({"contract": str(CONTRACT), "sha256": sha(CONTRACT)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
