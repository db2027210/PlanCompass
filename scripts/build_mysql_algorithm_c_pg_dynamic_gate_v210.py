#!/usr/bin/env python3
"""Freeze exact kernel-journal classification for full-parallel C."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_startup_isolation_20260929_v208/"
    "algorithm_c_pg_startup_isolation_contract.json"
)
RELEASE = ROOT / "config/formal/frozen_mysql_algorithm_c_pg_dynamic_gate_20260929_v210"
CONTRACT = RELEASE / "algorithm_c_pg_dynamic_gate_contract.json"
SOURCES = {
    "release_builder": Path(__file__).resolve(),
    "controller": ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v107.py",
    "dynamic_gate": ROOT / "scripts/mysql_cross_project_wave_coordination_v31.py",
    "correspondence_test": ROOT / "tests/test_mysql_algorithm_c_pg_correspondence_v8.py",
    "parent_algorithm_contract": PARENT,
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if RELEASE.exists():
        raise RuntimeError("v210 release already exists")
    parent = json.loads(PARENT.read_text())
    if (
        parent.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or parent.get("algorithm_decision_logic_changed") is not False
    ):
        raise RuntimeError("v210 parent differs")
    bindings = {}
    for name, path in SOURCES.items():
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"regular source required: {path}")
        bindings[name] = {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}
    value = {
        "schema_version": "mysql_algorithm_c_pg_dynamic_gate_release_v1",
        "release_version": "v210-pg-dynamic-gate",
        "status": "FROZEN_RUNTIME_VALIDATED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "fresh_search_required": True,
        "superseded_v209_rounds_reusable": False,
        "parent_algorithm_contract_path": str(PARENT.relative_to(ROOT)),
        "parent_algorithm_contract_sha256": sha(PARENT),
        "source_bindings": bindings,
        "exact_target_journal_classification": True,
        "effective_maximum_clean_window_wait_seconds": 120,
        "required_clean_window_seconds": 15,
        "sibling_scene_completion_checked": False,
        "cross_scene_completion_dependency": False,
        "database_and_benchmark_processes_remain_fail_closed": True,
        "unknown_user_processes_remain_fail_closed": True,
        "algorithm_decision_logic_changed": False,
        "candidate_set_changed": False,
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
