#!/usr/bin/env python3
"""Freeze complete current-PG Algorithm C recovery correspondence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PG_ROOT = ROOT.parent / "resource_adaptive"
PARENT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_expansion_commit_20260929_v199/"
    "algorithm_c_pg_expansion_commit_contract.json"
)
RELEASE = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_complete_recovery_20260929_v202"
)
CONTRACT = RELEASE / "algorithm_c_pg_complete_recovery_contract.json"
SOURCES = {
    "release_builder": Path(__file__).resolve(),
    "controller": ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v103.py",
    "runtime": ROOT / "scripts/run_mysql_four_scene_algorithm_c_boundary_resume_v56.py",
    "recovery_policy": ROOT / "scripts/mysql_algorithm_c_pg_recovery_v1.py",
    "interval_regions": ROOT / "scripts/mysql_algorithm_c_interval_control_v2.py",
    "rejection_cycle": ROOT / "scripts/mysql_outer_rejection_cycle_v3.py",
    "outer_policy": ROOT / "scripts/mysql_algorithm_c_pg_outer_policy_v1.py",
    "correspondence_test": ROOT / "tests/test_mysql_algorithm_c_pg_correspondence_v4.py",
    "parent_algorithm_contract": PARENT,
    "pg_adaptive_policy": PG_ROOT / "algorithm_c_adaptive_search.py",
    "pg_region_policy": PG_ROOT / "algorithm_c_budget_policy.py",
    "pg_runtime": PG_ROOT / "shell/run_pg_algorithm_c_budgeted_scene.py",
    "pg_scene_runtime": PG_ROOT / "shell/run_pg_algorithm_c_scene.py",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if RELEASE.exists():
        raise RuntimeError("v202 release already exists")
    parent = json.loads(PARENT.read_text(encoding="utf-8"))
    if (
        parent.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or parent.get("fresh_search_required") is not True
    ):
        raise RuntimeError("v202 parent differs")
    bindings = {}
    for name, path in SOURCES.items():
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"regular source required: {path}")
        bindings[name] = {
            "path": str(
                path if path.is_absolute() and not path.is_relative_to(ROOT)
                else path.relative_to(ROOT)
            ),
            "sha256": sha(path),
        }
    value = {
        "schema_version": "mysql_algorithm_c_pg_complete_recovery_release_v1",
        "release_version": "v202-pg-complete-recovery",
        "status": "FROZEN_RUNTIME_VALIDATED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "fresh_search_required": True,
        "superseded_v196_v198_v201_rounds_reusable": False,
        "parent_algorithm_contract": {
            "path": str(PARENT.relative_to(ROOT)),
            "sha256": sha(PARENT),
        },
        "source_bindings": bindings,
        "pg_search_correspondence": {
            "complete_technically_valid_batch_is_counter_unit": True,
            "technical_or_incomplete_batch_counts_as_miss": False,
            "first_valid_miss_expands_next_batch": True,
            "rejected_expansion_restores_parent": True,
            "accepted_expansion_keeps_binary_shrink": True,
            "measured_outer_bracket_before_due_shrink": True,
            "maximum_outer_bracket_attempts_per_interval": 1,
            "second_valid_miss_bisects_without_bracket": True,
            "due_shrink_anchor": "unchanged_last_accepted_incumbent",
            "forced_shrink_grants_accepted_credit": False,
            "accepted_candidate_shrink": "declared_active_subset_intersect_moved_subset",
            "empty_admission_counts_as_miss": False,
            "empty_admission_declares_convergence": False,
            "progressive_numeric_radii": [0.05, 0.10, 0.20, 0.40, 0.70, 1.00],
            "numeric_exhaustion_fails_closed": True,
            "retained_region_priority": "resource_potential_then_oldest_creation",
            "terminal_normalized_width": 0.01,
            "post_search_stability_confirmation": False,
            "qps_used_for_inner_scoring": False,
            "qps_used_for_outer_interval_control": True,
        },
        "workload_changed": False,
        "resource_envelope_changed": False,
        "search_state_transition_corrected": True,
        "postgresql_process_control_or_stop_performed": False,
    }
    RELEASE.mkdir(parents=True, mode=0o700)
    CONTRACT.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    CONTRACT.with_suffix(CONTRACT.suffix + ".sha256").write_text(
        f"{sha(CONTRACT)}  {CONTRACT.name}\n"
    )
    print(json.dumps({"contract": str(CONTRACT), "sha256": sha(CONTRACT)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
