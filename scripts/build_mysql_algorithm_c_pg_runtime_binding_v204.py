#!/usr/bin/env python3
"""Freeze the complete-PG expanded-runtime dependency binding."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_complete_recovery_20260929_v202/"
    "algorithm_c_pg_complete_recovery_contract.json"
)
RELEASE = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_runtime_binding_20260929_v204"
)
CONTRACT = RELEASE / "algorithm_c_pg_runtime_binding_contract.json"
SOURCES = {
    "release_builder": Path(__file__).resolve(),
    "controller": ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v104.py",
    "runtime_binding": ROOT / "scripts/run_mysql_four_scene_algorithm_c_boundary_resume_v57.py",
    "expanded_runtime": ROOT / "scripts/run_mysql_four_scene_algorithm_c_boundary_resume_v56.py",
    "interval_control": ROOT / "scripts/mysql_algorithm_c_interval_control_v2.py",
    "recovery_policy": ROOT / "scripts/mysql_algorithm_c_pg_recovery_v1.py",
    "rejection_cycle": ROOT / "scripts/mysql_outer_rejection_cycle_v3.py",
    "runtime_binding_test": ROOT / "tests/test_mysql_algorithm_c_pg_correspondence_v5.py",
    "parent_algorithm_contract": PARENT,
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if RELEASE.exists():
        raise RuntimeError("v204 release already exists")
    parent = json.loads(PARENT.read_text(encoding="utf-8"))
    if (
        parent.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or parent.get("fresh_search_required") is not True
    ):
        raise RuntimeError("v204 parent differs")
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
        "schema_version": "mysql_algorithm_c_pg_runtime_binding_release_v1",
        "release_version": "v204-pg-runtime-binding",
        "status": "FROZEN_RUNTIME_VALIDATED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "fresh_search_required": True,
        "superseded_v203_rounds_reusable": False,
        "parent_algorithm_contract_path": str(PARENT.relative_to(ROOT)),
        "parent_algorithm_contract_sha256": sha(PARENT),
        "source_bindings": bindings,
        "expanded_runtime_interval_control_bound": True,
        "expanded_runtime_recovery_policy_bound": True,
        "expanded_runtime_rejection_cycle_bound": True,
        "first_decision_path_tested": True,
        "workload_changed": False,
        "resource_envelope_changed": False,
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
