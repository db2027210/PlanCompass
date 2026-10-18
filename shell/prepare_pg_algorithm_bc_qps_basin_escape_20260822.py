#!/usr/bin/env python3
"""Prepare isolated B/C campaigns for the cached-QPS basin-escape repair."""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path
from typing import Any, Dict

from prepare_pg_algorithm_bc_last_accepted_20260822 import (
    B_SOURCE,
    C_SOURCE,
    PROJECT,
    atomic_json,
    prepare_one,
    read_json,
    sha256,
)


C_DESTINATION = Path(
    "/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/"
    "20260822_algorithm_c_last_accepted_qps_basin_escape_clean_r68"
)
B_DESTINATION = Path(
    "/disk1/resource_adaptive/pg_algorithm_b_four_grid_20260814/"
    "20260822_algorithm_b_last_accepted_qps_basin_escape_clean_r4"
)
C_FAILED_CAMPAIGN = Path(
    "/disk1/resource_adaptive/pg_algorithm_c_four_experience_sgd_20260809/"
    "20260822_algorithm_c_last_accepted_qps_clean_r67"
)
C_CONTROLLER = PROJECT / "shell/run_pg_algorithm_c_scene.py"
B_CONTROLLER = PROJECT / "shell/run_pg_algorithm_b_scene.py"
TEST = PROJECT / "shell/test_algorithm_c_formal_gate_repairs.py"
BASIN_ESCAPE_CONTRACT = (
    "cached_valid_replay_qps_stagnation_full_range_plan_escape_v1"
)


def seal_manifest(
    destination: Path,
    prepared: Dict[str, Any],
    *,
    algorithm: str,
) -> Dict[str, Any]:
    manifest_path = Path(prepared["manifest"])
    manifest = copy.deepcopy(read_json(manifest_path))
    algorithm_controller = C_CONTROLLER if algorithm == "C" else B_CONTROLLER
    manifest["basin_escape_repair"] = {
        "contract": BASIN_ESCAPE_CONTRACT,
        "algorithm_controller": str(algorithm_controller),
        "algorithm_controller_sha256": sha256(algorithm_controller),
        "shared_outer_framework_controller": str(C_CONTROLLER),
        "shared_outer_framework_controller_sha256": sha256(C_CONTROLLER),
        "regression_test": str(TEST),
        "regression_test_sha256": sha256(TEST),
        "trigger": (
            "fallback_with_valid_cached_decisions_and_zero_new_physical_rows"
        ),
        "full_range_changes_formal_range": False,
        "full_plan_fingerprint_change_required": True,
        "rejected_round_changes_incumbent": False,
        "rejected_round_changes_gradient": False,
        "rejected_round_changes_formal_range": False,
        "clean_terminal_if_full_range_plan_space_exhausted": True,
    }
    manifest["repair_provenance"] = {
        "path": str(C_FAILED_CAMPAIGN),
        "relationship": (
            "supersedes_failed_algorithm_c_campaign"
            if algorithm == "C"
            else "inherits_repaired_shared_outer_framework_only"
        ),
        "state_inherited": False,
        "range_inherited": False,
        "valid_replay_inherited": False,
        "reason": (
            "cached lower-QPS evidence suppressed full-range basin escape"
        ),
    }
    manifest["sealed_at_epoch_s"] = time.time()
    atomic_json(manifest_path, manifest)
    prepared["manifest_sha256"] = sha256(manifest_path)
    prepared["algorithm_controller_sha256"] = sha256(algorithm_controller)
    prepared["shared_outer_framework_controller_sha256"] = sha256(C_CONTROLLER)
    prepared["basin_escape_contract"] = BASIN_ESCAPE_CONTRACT
    prepared["destination"] = str(destination)
    return prepared


def main() -> int:
    prepared_c = prepare_one(C_SOURCE, C_DESTINATION, algorithm="C")
    prepared_b = prepare_one(B_SOURCE, B_DESTINATION, algorithm="B")
    results = [
        seal_manifest(C_DESTINATION, prepared_c, algorithm="C"),
        seal_manifest(B_DESTINATION, prepared_b, algorithm="B"),
    ]
    print(json.dumps(results, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
