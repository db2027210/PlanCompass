#!/usr/bin/env python3
"""Seal a v3 optimization authority matching the PG rejection runtime."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PARENT_ALGORITHM = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_async_pg_rejection_20260929_v193/"
    "algorithm_c_async_pg_rejection_contract.json"
)
PARENT_OPTIMIZATION = ROOT / (
    "config/formal/frozen_mysql_four_scene_algorithm_c_client_primary_20260923_v156/"
    "outer_runtime_optimization.json"
)
RELEASE = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_rejection_authority_20260929_v195"
)
OPTIMIZATION = RELEASE / "outer_runtime_optimization.json"
CONTRACT = RELEASE / "algorithm_c_pg_rejection_authority_contract.json"
SOURCES = {
    "release_builder": Path(__file__).resolve(),
    "controller": ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v100.py",
    "runtime_binding_controller": ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v99.py",
    "pg_rejection_policy": ROOT / "scripts/mysql_outer_rejection_cycle_v3.py",
    "parent_algorithm_contract": PARENT_ALGORITHM,
    "parent_optimization": PARENT_OPTIMIZATION,
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if RELEASE.exists():
        raise RuntimeError("v195 release already exists")
    algorithm = json.loads(PARENT_ALGORITHM.read_text())
    parent = json.loads(PARENT_OPTIMIZATION.read_text())
    if algorithm.get("status") != "FROZEN_RUNTIME_VALIDATED" or parent.get("status") != "FROZEN_RUNTIME_VALIDATED":
        raise RuntimeError("v195 parents differ")
    optimization = copy.deepcopy(parent)
    optimization["schema_version"] = "mysql_algorithm_c_outer_runtime_optimization_v195"
    optimization["release_version"] = "v195-pg-half-interval-runtime-authority"
    optimization["parent_optimization_contract"] = {
        "path": str(PARENT_OPTIMIZATION.relative_to(ROOT)),
        "sha256": sha(PARENT_OPTIMIZATION),
    }
    optimization["pg_search_outer"]["outer_rejection_cycle_policy_id"] = (
        "mysql_c_pg_half_interval_two_reject_shrink_v3"
    )
    optimization["pg_search_outer"]["second_valid_miss_boundary_rule"] = (
        "keep_transformed_half_interval_containing_incumbent"
    )
    optimization["bindings"].update({
        "pg_rejection_policy_path": str(SOURCES["pg_rejection_policy"].relative_to(ROOT)),
        "pg_rejection_policy_sha256": sha(SOURCES["pg_rejection_policy"]),
        "runtime_binding_controller_path": str(SOURCES["runtime_binding_controller"].relative_to(ROOT)),
        "runtime_binding_controller_sha256": sha(SOURCES["runtime_binding_controller"]),
    })
    RELEASE.mkdir(parents=True, mode=0o700)
    OPTIMIZATION.write_text(json.dumps(optimization, indent=2, sort_keys=True) + "\n")
    OPTIMIZATION.with_suffix(OPTIMIZATION.suffix + ".sha256").write_text(
        f"{sha(OPTIMIZATION)}  {OPTIMIZATION.name}\n"
    )
    bindings = {}
    for name, path in SOURCES.items():
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"regular source required: {path}")
        bindings[name] = {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}
    value = {
        "schema_version": "mysql_algorithm_c_pg_rejection_authority_release_v1",
        "release_version": "v195-pg-rejection-optimization-authority",
        "status": "FROZEN_RUNTIME_VALIDATED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "fresh_search_required": True,
        "optimization_path": str(OPTIMIZATION.relative_to(ROOT)),
        "optimization_sha256": sha(OPTIMIZATION),
        "source_bindings": bindings,
        "runtime_and_authority_policy_match": True,
        "workload_or_candidate_policy_changed": False,
        "resource_envelope_changed": False,
        "postgresql_process_control_or_stop_performed": False,
    }
    CONTRACT.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    CONTRACT.with_suffix(CONTRACT.suffix + ".sha256").write_text(
        f"{sha(CONTRACT)}  {CONTRACT.name}\n"
    )
    print(json.dumps({"contract": str(CONTRACT), "sha256": sha(CONTRACT), "optimization_sha256": sha(OPTIMIZATION)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
