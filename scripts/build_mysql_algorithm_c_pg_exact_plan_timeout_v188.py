#!/usr/bin/env python3
"""Seal the 900-second plan-capture successor for fresh PG-exact C."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_interval_exact_20260929_v186/"
    "algorithm_c_pg_interval_exact_contract.json"
)
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_exact_plan_timeout_20260929_v188/"
    "algorithm_c_plan_timeout_contract.json"
)
SOURCES = {
    "release_builder": Path(__file__).resolve(),
    "controller": ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v97.py",
    "plan_capture": ROOT / "scripts/mysql_algorithm_c_plan_capture_timeout_v1.py",
    "algorithm_runtime": ROOT / "scripts/run_mysql_four_scene_algorithm_c_boundary_resume_v54.py",
    "parent_algorithm_contract": PARENT,
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if CONTRACT.exists() or CONTRACT.parent.exists():
        raise RuntimeError("v188 release already exists")
    parent = json.loads(PARENT.read_text())
    if parent.get("status") != "FROZEN_RUNTIME_VALIDATED":
        raise RuntimeError("v188 parent differs")
    bindings = {}
    for name, path in SOURCES.items():
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"regular source required: {path}")
        bindings[name] = {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}
    value = {
        "schema_version": "mysql_algorithm_c_pg_exact_plan_timeout_release_v1",
        "release_version": "v188-pg-exact-plan-capture-900s",
        "status": "FROZEN_RUNTIME_VALIDATED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "parent_algorithm_contract": {
            "path": str(PARENT.relative_to(ROOT)), "sha256": sha(PARENT),
        },
        "source_bindings": bindings,
        "fresh_search_required": True,
        "discarded_v187_incomplete_outer": 1,
        "discarded_v187_formal_credit": 0,
        "plan_capture_timeout_seconds": 900,
        "plan_capture_read_only": True,
        "plan_capture_outside_formal_measurement_wall_time": True,
        "formal_measurement_timeout_changed": False,
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
