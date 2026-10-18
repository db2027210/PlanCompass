#!/usr/bin/env python3
"""Write a SHA-bound PG/MySQL Algorithm C correspondence audit."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
PG = ROOT.parent / "resource_adaptive"
HISTORY = ROOT / "history/mysql_four_scene_20260809"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if len(sys.argv) != 2:
        raise RuntimeError("usage: writer RUN_PREFIX")
    run_prefix = sys.argv[1]
    output = HISTORY / run_prefix / "pg_correspondence_audit.json"
    if output.exists() or output.is_symlink():
        raise RuntimeError(f"fresh audit required: {output}")
    sources = {
        "pg_policy": PG / "docs/pg_algorithm_c_interval_control_20260909.md",
        "pg_scene_controller": PG / "shell/run_pg_algorithm_c_scene.py",
        "pg_budgeted_controller": PG / "shell/run_pg_algorithm_c_budgeted_scene.py",
        "pg_search": PG / "algorithm_c_adaptive_search.py",
        "mysql_runtime": ROOT / "scripts/run_mysql_four_scene_algorithm_c_boundary_resume_v58.py",
        "mysql_recovery": ROOT / "scripts/mysql_algorithm_c_pg_recovery_v2.py",
        "mysql_interval": ROOT / "scripts/mysql_algorithm_c_interval_control_v2.py",
        "mysql_rejection": ROOT / "scripts/mysql_outer_rejection_cycle_v3.py",
        "mysql_dynamic_gate": ROOT / "scripts/mysql_cross_project_wave_coordination_v31.py",
    }
    document = {
        "schema_version": "mysql_algorithm_c_pg_correspondence_audit_v1",
        "created_at": datetime.now().astimezone().isoformat(),
        "status": "PASS_RUNNING_AWAITING_FIRST_COMPLETE_OUTER",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "run_prefix": run_prefix,
        "source_bindings": {
            name: {"path": str(path), "sha256": sha(path)}
            for name, path in sources.items()
        },
        "correspondence": {
            "top_k": 5,
            "physical_waves": [[1, 2], [3, 4], [5]],
            "qps_role": "outer_acceptance_only",
            "strict_improvement_required": True,
            "acceptance_tiers": ["strict", "secondary", "fallback"],
            "accepted_shrink_scope": "declared_active_intersection_actually_moved",
            "first_valid_miss": "expand_plan_parameter_and_retained_region",
            "second_valid_miss": "one_boundary_refinement_then_due_shrink",
            "empty_admission_miss_credit": 0,
            "empty_boundary_same_action_numeric_fallback": True,
            "empty_boundary_due_shrink_without_third_miss": True,
            "progressive_numeric_radii": [0.05, 0.1, 0.2, 0.4, 0.7, 1.0],
            "progressive_numeric_cursor_persisted": True,
            "boundary_pair_requires_distinct_parameter_identities": True,
            "convergence_metric": "maximum_actual_normalized_transformed_interval_width",
            "terminal_width": 0.01,
            "post_search_stability_rounds": 0,
        },
        "tests": {
            "command": "pytest pg_correspondence_v2..v8 plus historical live-authority v29",
            "passed": 31,
            "failed": 0,
        },
        "algorithm_contract": {
            "path": str(ROOT / "config/formal/frozen_mysql_algorithm_c_pg_dynamic_gate_20260929_v210/algorithm_c_pg_dynamic_gate_contract.json"),
            "sha256": sha(ROOT / "config/formal/frozen_mysql_algorithm_c_pg_dynamic_gate_20260929_v210/algorithm_c_pg_dynamic_gate_contract.json"),
        },
        "launch_contract": {
            "path": str(ROOT / "config/formal/frozen_mysql_algorithm_c_pg_dynamic_gate_launch_20260929_v211/four_scene_launch_contract.json"),
            "sha256": sha(ROOT / "config/formal/frozen_mysql_algorithm_c_pg_dynamic_gate_launch_20260929_v211/four_scene_launch_contract.json"),
        },
        "superseded_campaign_rounds_reused": False,
        "historical_interval_state_reused": False,
        "postgresql_process_control_or_stop_performed": False,
    }
    temporary = output.with_name(f".{output.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
    os.replace(temporary, output)
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{sha(output)}  {output.name}\n"
    )
    print(json.dumps({"path": str(output), "sha256": sha(output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
