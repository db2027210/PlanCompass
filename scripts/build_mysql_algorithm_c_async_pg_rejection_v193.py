#!/usr/bin/env python3
"""Seal the final legacy-entry PostgreSQL rejection-policy binding."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_hwloc_cpu_only_20260929_v190/"
    "algorithm_c_hwloc_contract.json"
)
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_async_pg_rejection_20260929_v193/"
    "algorithm_c_async_pg_rejection_contract.json"
)
SOURCES = {
    "release_builder": Path(__file__).resolve(),
    "controller": ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v99.py",
    "parent_controller": ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v98.py",
    "client_entry_v13": ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v13.py",
    "client_entry_v30": ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v30.py",
    "pg_rejection_policy": ROOT / "scripts/mysql_outer_rejection_cycle_v3.py",
    "parent_algorithm_contract": PARENT,
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if CONTRACT.exists() or CONTRACT.parent.exists():
        raise RuntimeError("v193 release already exists")
    parent = json.loads(PARENT.read_text())
    if parent.get("status") != "FROZEN_RUNTIME_VALIDATED":
        raise RuntimeError("v193 parent differs")
    policy_module = {}
    exec(
        compile(SOURCES["pg_rejection_policy"].read_text(), str(SOURCES["pg_rejection_policy"]), "exec"),
        policy_module,
    )
    policy = policy_module["policy_contract"]()
    if (
        policy.get("policy_id") != "mysql_c_pg_half_interval_two_reject_shrink_v3"
        or policy.get("forced_shrink_boundary_rule")
        != "keep_transformed_half_interval_containing_incumbent"
        or policy.get("terminal_normalized_width") != 0.01
        or policy.get("stability_confirmation_required") is not False
    ):
        raise RuntimeError("v193 PG rejection policy differs")
    bindings = {}
    for name, path in SOURCES.items():
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"regular source required: {path}")
        bindings[name] = {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}
    value = {
        "schema_version": "mysql_algorithm_c_async_pg_rejection_release_v1",
        "release_version": "v193-async-pg-rejection-binding",
        "status": "FROZEN_RUNTIME_VALIDATED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "parent_algorithm_contract": {
            "path": str(PARENT.relative_to(ROOT)), "sha256": sha(PARENT),
        },
        "source_bindings": bindings,
        "fresh_search_required": True,
        "discarded_predecessor_runs": ["v187", "v189", "v191", "v192"],
        "discarded_predecessor_formal_credit": 0,
        "runtime_rejection_policy": policy,
        "legacy_runtime_overrides_prevented": ["v13", "v30"],
        "pg_interval_region_runtime_retained": True,
        "workload_or_candidate_policy_changed": False,
        "resource_envelope_changed": False,
        "postgresql_process_control_or_stop_performed": False,
    }
    CONTRACT.parent.mkdir(parents=True, mode=0o700)
    CONTRACT.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    CONTRACT.with_suffix(CONTRACT.suffix + ".sha256").write_text(
        f"{sha(CONTRACT)}  {CONTRACT.name}\n"
    )
    print(json.dumps({"contract": str(CONTRACT), "sha256": sha(CONTRACT)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
