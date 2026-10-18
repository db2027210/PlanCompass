#!/usr/bin/env python3
"""Freeze mixed-SSD two-worker-per-scene Top4 Algorithm-A."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_a_20260826_v10"
OUTPUT = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_a_cpu_a_slots_20260828_v11"
FREEZE = (
    ROOT / "config/formal/frozen_mysql_four_scene_cpu_a_slots_two_worker_20260828_v2"
    / "four_scene_effective_resource_successor.json"
)


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def binding(path: str) -> dict[str, str]:
    target = ROOT / path
    if not target.is_file() or target.is_symlink():
        raise RuntimeError(f"regular frozen source required: {target}")
    return {"path": path, "sha256": sha(target)}


def main() -> int:
    if OUTPUT.exists():
        raise RuntimeError(f"fresh release directory required: {OUTPUT}")
    coordination = load(SOURCE / "coordination_contract.json")
    coordination.update({
        "schema_version": "mysql_algorithm_a_top4_cpu_a_slots_no_lease_coordination_v2",
        "created_at": now(), "status": "FROZEN_RUNTIME_VALIDATED",
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "maximum_workers_per_scene": 2,
        "maximum_concurrent_mysql_workers": 8,
        "four_scene_barrier": False, "sibling_scene_wait": False,
        "shared_nvme_mutual_exclusion": False,
        "numa_exclusive_scheduler_lock": False,
        "whole_round_resource_lease": False, "lease_mode": "none",
    })
    OUTPUT.mkdir(parents=True, mode=0o700)
    coordination_path = OUTPUT / "coordination_contract.json"
    atomic(coordination_path, coordination)

    contract = load(SOURCE / "algorithm_a_top4_contract.json")
    contract.update({
        "schema_version": "mysql_algorithm_a_xp_top4_cpu_a_slots_two_worker_contract_v2",
        "release_version": "v11-cpu-a-slots-two-worker",
        "created_at": now(), "status": "FROZEN_RUNTIME_VALIDATED",
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "resource_freeze_path": str(FREEZE),
        "resource_freeze_sha256": sha(FREEZE),
        "physical_replay_parallelism_per_scene": 2,
        "candidate_batches": [
            [1, 2], [3, 4], [5, 6], [7, 8],
            [9, 10], [11, 12], [13, 14], [15, 16]
        ],
        "four_scene_wave_barrier": False, "sibling_scene_wait": False,
        "lease_mode": "none", "post_convergence_stability_required": False,
    })
    sources = {
        "builder": "scripts/build_mysql_xp_top4_algorithm_a_cpu_a_slots_release_v1.py",
        "controller": "scripts/run_mysql_algorithm_a_xp_top4_cpu_a_slots_v2.py",
        "scene_launcher": "scripts/launch_mysql_algorithm_a_xp_top4_cpu_a_slots_scene_v2.py",
        "scene_authority_refresh": "scripts/refresh_mysql_cpu_a_slots_two_worker_scene_authority_v2.py",
        "dynamic_noninterference_gate": "scripts/mysql_cross_project_wave_coordination_cpu_a_slots_v12.py",
        "full_parallel_preflight": "scripts/preflight_mysql_four_scene_cpu_a_slots_v10.py",
        "mixed_ssd_xp_reset": "scripts/reset_mysql_xp_data_planes_mixed_four_worker_v2.py",
        "xp_reset_predecessor_v6": "scripts/reset_mysql_xp_data_planes_v6.py",
        "shared_algorithm_c_measurement_core": "scripts/run_mysql_four_scene_algorithm_c_v2.py",
        "no_lease_measurement": "scripts/mysql_algorithm_b_no_lease_measurement_v1.py",
        "pg_aligned_a_core": "scripts/run_mysql_algorithm_a_async_scene_v3.py",
        "two_worker_runtime_adapter": "scripts/run_mysql_algorithm_a_job_to_xp_resume_v1.py",
        "full_factorial": "scripts/mysql_algorithm_a_full_factorial_v1.py",
        "cpu_a_slots_resource_freeze": str(FREEZE.relative_to(ROOT)),
        "xp_cpu_top4_table": "config/formal/frozen_mysql_xp_top4_algorithm_c_20260825_v1/T.hybench_xp_cpu.top4.operator_physics_v1.json",
        "xp_io_top4_table": "config/formal/frozen_mysql_xp_top4_algorithm_c_20260825_v1/T.hybench_xp_io.top4.operator_physics_v1.json",
        "top4_parameter_contract": "config/formal/frozen_mysql_xp_top4_algorithm_c_20260825_v1/parameter_contract.json",
        "handoff_source_manifest": "history/mysql_four_scene_20260809/v893_to_cpu_a_slots_two_worker_handoff_source_20260828/source_manifest.json",
    }
    contract["source_bindings"] = {
        name: binding(path) for name, path in sources.items()
    }
    atomic(OUTPUT / "algorithm_a_top4_contract.json", contract)
    print(json.dumps({
        "status": "FROZEN_RUNTIME_VALIDATED",
        "contract": str(OUTPUT / "algorithm_a_top4_contract.json"),
        "contract_sha256": sha(OUTPUT / "algorithm_a_top4_contract.json"),
        "coordination_sha256": sha(coordination_path),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
