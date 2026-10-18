#!/usr/bin/env python3
"""Seal the complete-PG-recovery four-scene launch contract."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ALGORITHM_CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_complete_recovery_20260929_v202/"
    "algorithm_c_pg_complete_recovery_contract.json"
)
RESOURCE_CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_paper_baseline_20260923_v156/"
    "algorithm_c_paper_baseline_contract.json"
)
RELEASE = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_complete_launch_20260929_v203"
)
CONTRACT = RELEASE / "four_scene_launch_contract.json"
SOURCES = {
    "release_builder": Path(__file__).resolve(),
    "launch_controller": ROOT / "scripts/launch_mysql_algorithm_c_v203_pg_complete_recovery_four_scene.py",
    "base_parallel_launcher": ROOT / "scripts/launch_mysql_algorithm_c_v156_four_scene.py",
    "scene_controller": ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v103.py",
    "algorithm_runtime": ROOT / "scripts/run_mysql_four_scene_algorithm_c_boundary_resume_v56.py",
    "recovery_policy": ROOT / "scripts/mysql_algorithm_c_pg_recovery_v1.py",
    "algorithm_contract": ALGORITHM_CONTRACT,
    "resource_contract": RESOURCE_CONTRACT,
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if RELEASE.exists():
        raise RuntimeError("v203 launch release already exists")
    algorithm = json.loads(ALGORITHM_CONTRACT.read_text(encoding="utf-8"))
    resource = json.loads(RESOURCE_CONTRACT.read_text(encoding="utf-8"))
    correspondence = algorithm.get("pg_search_correspondence") or {}
    if (
        algorithm.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or algorithm.get("fresh_search_required") is not True
        or correspondence.get("measured_outer_bracket_before_due_shrink") is not True
        or correspondence.get("empty_admission_counts_as_miss") is not False
        or correspondence.get("numeric_exhaustion_fails_closed") is not True
        or resource.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or resource.get("formal_scope") != "mysql_only"
    ):
        raise RuntimeError("v203 parent contracts differ")
    bindings = {}
    for name, path in SOURCES.items():
        if path.is_symlink() or not path.is_file():
            raise RuntimeError(f"regular launch source required: {path}")
        bindings[name] = {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}
    value = {
        "schema_version": "mysql_algorithm_c_pg_complete_recovery_four_scene_launch_contract_v1",
        "release_version": "v203-four-scene-pg-complete-recovery",
        "status": "FROZEN_RUNTIME_VALIDATED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "algorithm_contract_path": str(ALGORITHM_CONTRACT.relative_to(ROOT)),
        "algorithm_contract_sha256": sha(ALGORITHM_CONTRACT),
        "resource_contract_path": str(RESOURCE_CONTRACT.relative_to(ROOT)),
        "resource_contract_sha256": sha(RESOURCE_CONTRACT),
        "source_bindings": bindings,
        "active_scenes": [
            "job_memory", "tpch_sf4_memory", "hybench_xp_cpu", "hybench_xp_io",
        ],
        "all_four_scenes_start_concurrently": True,
        "physical_workers_per_scene": 2,
        "maximum_concurrent_mysql_workers": 8,
        "per_scene_outer_state_machine": True,
        "four_scene_barrier": False,
        "sibling_scene_wait": False,
        "shared_storage_lease": False,
        "fresh_search_required": True,
        "superseded_v196_v198_v201_rounds_reused": False,
        "plan_capture_timeout_seconds": 900,
        "plan_capture_read_only": True,
        "plan_capture_outside_formal_measurement_wall_time": True,
        "accelerator_topology_discovery_disabled": True,
        "cpu_or_numa_binding_changed": False,
        "outer_boundary_refinement_before_due_shrink": True,
        "empty_admission_progressive_recovery": True,
        "resource_envelope_changed": False,
        "workload_changed": False,
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
