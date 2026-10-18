#!/usr/bin/env python3
"""Seal the final four-scene PG-exact Algorithm C launch contract."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ALGORITHM_CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_async_pg_rejection_20260929_v193/"
    "algorithm_c_async_pg_rejection_contract.json"
)
RESOURCE_CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_paper_baseline_20260923_v156/"
    "algorithm_c_paper_baseline_contract.json"
)
CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_exact_final_launch_20260929_v194/"
    "four_scene_launch_contract.json"
)
SOURCES = {
    "release_builder": Path(__file__).resolve(),
    "launch_controller": ROOT / "scripts/launch_mysql_algorithm_c_v194_pg_exact_final_four_scene.py",
    "base_parallel_launcher": ROOT / "scripts/launch_mysql_algorithm_c_v156_four_scene.py",
    "scene_controller": ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v99.py",
    "algorithm_runtime": ROOT / "scripts/run_mysql_four_scene_algorithm_c_boundary_resume_v54.py",
    "algorithm_contract": ALGORITHM_CONTRACT,
    "resource_contract": RESOURCE_CONTRACT,
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    if CONTRACT.exists() or CONTRACT.parent.exists():
        raise RuntimeError("v194 launch release already exists")
    algorithm = json.loads(ALGORITHM_CONTRACT.read_text(encoding="utf-8"))
    resource = json.loads(RESOURCE_CONTRACT.read_text(encoding="utf-8"))
    if (
        algorithm.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or algorithm.get("fresh_search_required") is not True
        or resource.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or resource.get("formal_scope") != "mysql_only"
    ):
        raise RuntimeError("v194 parent contracts differ")
    bindings = {}
    for name, path in SOURCES.items():
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"regular launch source required: {path}")
        bindings[name] = {
            "path": str(path.relative_to(ROOT)),
            "sha256": sha(path),
        }
    value = {
        "schema_version": "mysql_algorithm_c_pg_exact_final_four_scene_launch_contract_v1",
        "release_version": "v194-four-scene-pg-exact-final-fresh-search",
        "status": "FROZEN_RUNTIME_VALIDATED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "algorithm_contract_path": str(ALGORITHM_CONTRACT.relative_to(ROOT)),
        "algorithm_contract_sha256": sha(ALGORITHM_CONTRACT),
        "resource_contract_path": str(RESOURCE_CONTRACT.relative_to(ROOT)),
        "resource_contract_sha256": sha(RESOURCE_CONTRACT),
        "source_bindings": bindings,
        "active_scenes": [
            "job_memory",
            "tpch_sf4_memory",
            "hybench_xp_cpu",
            "hybench_xp_io",
        ],
        "all_four_scenes_start_concurrently": True,
        "physical_workers_per_scene": 2,
        "maximum_concurrent_mysql_workers": 8,
        "per_scene_outer_state_machine": True,
        "four_scene_barrier": False,
        "sibling_scene_wait": False,
        "shared_storage_lease": False,
        "fresh_search_required": True,
        "plan_capture_timeout_seconds": 900,
        "plan_capture_read_only": True,
        "plan_capture_outside_formal_measurement_wall_time": True,
        "accelerator_topology_discovery_disabled": True,
        "cpu_or_numa_binding_changed": False,
        "async_rejection_policy_override_prevented": True,
        "historical_interval_state_reuse_prohibited": True,
        "formal_r0_reuse_requires_exact_resource_freeze_and_passed_paper_gate": True,
        "resource_envelope_changed": False,
        "workload_changed": False,
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
