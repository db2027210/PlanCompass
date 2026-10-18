#!/usr/bin/env python3
"""Freeze v16 IO C with stability-or-cap completion semantics."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_algorithm_c_top4_io_xp4_recovery_release_v2 as predecessor


predecessor.OUTPUT = (
    ROOT / "config/formal/frozen_mysql_algorithm_c_top4_io_xp4_recovery_20260826_v16"
)
predecessor.RUNTIME = ROOT / "scripts/run_mysql_four_scene_algorithm_c_xp4_runtime_v6.py"
predecessor.CONTROLLER = ROOT / "scripts/run_mysql_algorithm_c_async_scene_xp4_v9.py"


def main() -> int:
    result = predecessor.main()
    optimization_path = predecessor.OUTPUT / "optimization_contract.json"
    optimization = predecessor.release.load(optimization_path)
    optimization["active_parameter_sets"] = {
        "hybench_xp_cpu": [
            "row_evaluate_cost", "key_compare_cost",
            "disk_temptable_row_cost", "io_block_read_cost",
        ],
        "hybench_xp_io": [
            "io_block_read_cost", "disk_temptable_row_cost",
            "disk_temptable_create_cost", "memory_temptable_row_cost",
        ],
    }
    optimization["top4_operator_table_projection"] = {
        "status": "FROZEN_RUNTIME_VALIDATED",
        "ranking_formula_changed": False,
        "physics_coefficients_changed": False,
        "undeclared_parameter_candidates_prohibited": True,
    }
    optimization["xp_adaptive_measurement"].update({
        "completion_policy": "stable_after_300s_or_complete_at_900s_v1",
        "cap_completion_is_not_stability": True,
        "coverage_and_nonstability_hard_gates_required_at_cap": True,
    })
    predecessor.release.atomic(optimization_path, optimization)
    coordination_path = predecessor.OUTPUT / "coordination_contract.json"
    coordination = predecessor.release.load(coordination_path)
    executable = predecessor.RUNTIME_PREDECESSOR
    coordination["source_bindings"]["boundary_resume_controller_path"] = str(
        executable.relative_to(ROOT)
    )
    coordination["source_bindings"]["boundary_resume_controller_sha256"] = (
        predecessor.release.sha(executable)
    )
    coordination["runtime_successor_wrapper"] = predecessor.bind(predecessor.RUNTIME)
    predecessor.release.atomic(coordination_path, coordination)
    for name in ("algorithm_c_xp4_async_contract.json", "transition_contract.json"):
        path = predecessor.OUTPUT / name
        document = predecessor.release.load(path)
        document["active_parameter_sets"] = optimization["active_parameter_sets"]
        document["xp_completion_policy"] = optimization["xp_adaptive_measurement"]
        document["source_bindings"]["optimization_contract"] = predecessor.bind(
            optimization_path
        )
        document["source_bindings"]["coordination_contract"] = predecessor.bind(
            coordination_path
        )
        predecessor.release.atomic(path, document)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
