#!/usr/bin/env python3
"""Freeze the fresh-search Algorithm C interval-control successor."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PG_ROOT = ROOT.parent / "resource_adaptive"
PARENT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260907_v119/"
    "algorithm_c_pg_latest_outer_contract.json"
)
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_interval_control_20260920_v120/"
    "algorithm_c_interval_contract.json"
)
SOURCES = {
    "mysql_release_builder": ROOT / "scripts/build_mysql_algorithm_c_interval_release_v120.py",
    "mysql_controller": ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v30.py",
    "mysql_runtime": ROOT / "scripts/run_mysql_four_scene_algorithm_c_boundary_resume_v46.py",
    "mysql_outer_policy": ROOT / "scripts/mysql_algorithm_c_pg_outer_policy_v1.py",
    "mysql_interval_control": ROOT / "scripts/mysql_algorithm_c_interval_control_v1.py",
    "mysql_rejection_cycle": ROOT / "scripts/mysql_outer_rejection_cycle_v2.py",
    "postgresql_adaptive_policy": PG_ROOT / "algorithm_c_adaptive_search.py",
    "postgresql_adaptive_controller": PG_ROOT / "shell/run_pg_algorithm_c_budgeted_scene.py",
    "postgresql_outer_policy": PG_ROOT / "shell/run_pg_algorithm_c_scene.py",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if CONTRACT.exists():
        raise RuntimeError("v120 release is already sealed")
    parent = json.loads(PARENT.read_text(encoding="utf-8"))
    if parent.get("formal_scope") != "mysql_only":
        raise RuntimeError("v119 parent scope differs")
    bindings = {}
    for name, path in SOURCES.items():
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"missing regular source: {path}")
        try:
            bound_path = str(path.relative_to(ROOT))
        except ValueError:
            bound_path = str(path)
        bindings[name] = {
            "path": bound_path,
            "sha256": sha(path),
        }
    release = {
        "schema_version": "mysql_algorithm_c_interval_control_release_v1",
        "release_version": "v120-actual-interval-two-valid-misses",
        "status": "FROZEN_RUNTIME_VALIDATED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "parent_contract": {"path": str(PARENT.relative_to(ROOT)), "sha256": sha(PARENT)},
        "source_bindings": bindings,
        "outer_flow": {
            "semantic_batch": "one_complete_valid_top5",
            "physical_waves": [[1, 2], [3, 4], [5]],
            "first_valid_miss": "expand_next_candidate_batch",
            "second_valid_miss": "bisect_unchanged_incumbent",
            "empty_or_technical_batch_miss_credit": 0,
            "accepted_shrink": "declared_and_moved_dimensions_only",
            "acceptance_tiers": ["strict", "secondary", "fallback"],
            "qps_reference": "canonical_baseline_then_last_accepted_incumbent",
            "strict_qps_increase_threshold": 0.0,
            "qps_used_for_inner_scoring": False,
            "first_miss_expansion_domain": "current_formal_interval_only",
            "terminal_width": "actual_maximum_initial_normalized_log_width",
            "terminal_normalized_width": 0.01,
            "fixed_replay_or_wall_stop": False,
            "fixed_outer_round_stop": False,
            "post_search_stability_confirmation": False,
        },
        "parallel_execution": parent["parallel_execution"],
        "mysql_interfaces": parent["mysql_interfaces"],
        "resource_and_workload_freeze": {
            "optimization_path": parent["source_bindings"]["client_primary_optimization"],
            "coordination_path": parent["source_bindings"]["kernel_coordination_contract"],
            "workload_changes": False,
            "resource_envelope_changes": False,
        },
        "historical_checkpoint_reuse_prohibited": True,
    }
    CONTRACT.parent.mkdir(parents=True, exist_ok=False)
    CONTRACT.write_text(json.dumps(release, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    CONTRACT.with_suffix(CONTRACT.suffix + ".sha256").write_text(
        sha(CONTRACT) + "  " + CONTRACT.name + "\n", encoding="utf-8"
    )
    print(json.dumps({"contract": str(CONTRACT), "sha256": sha(CONTRACT)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
