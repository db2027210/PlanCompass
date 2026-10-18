#!/usr/bin/env python3
"""Freeze Top4-B for four independent mixed-SSD workers per scene."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
PREDECESSOR = ROOT / "config/formal/frozen_mysql_four_scene_top4_algorithm_b_20260827_v64"
OUTPUT = ROOT / "config/formal/frozen_mysql_four_scene_top4_algorithm_b_mixed_four_worker_20260827_v14"
FREEZE = (
    ROOT / "config/formal/frozen_mysql_four_scene_mixed_disk_four_worker_20260827_v3"
    / "four_scene_effective_resource_successor.json"
)
SOURCES = {
    "builder": ROOT / "scripts/build_mysql_four_scene_top4_algorithm_b_mixed_four_worker_release_v1.py",
    "runtime": ROOT / "scripts/run_mysql_algorithm_b_four_scene_top4_mixed_four_worker_v1.py",
    "algorithm_b_core": ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v1.py",
    "algorithm_b_qps_rejection_facade": ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v30.py",
    "xp_adapter_compatibility": ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v3.py",
    "outer_rejection_cycle": ROOT / "scripts/mysql_outer_rejection_cycle_v2.py",
    "boundary_controller": ROOT / "scripts/run_mysql_algorithm_b_top4_boundary_resume_v1.py",
    "mixed_boundary_controller": ROOT / "scripts/run_mysql_algorithm_b_top4_mixed_four_worker_boundary_resume_v1.py",
    "job_controller": ROOT / "scripts/run_mysql_algorithm_b_top4_mixed_four_worker_job_v1.py",
    "tpch_controller": ROOT / "scripts/run_mysql_algorithm_b_top4_mixed_four_worker_tpch_v1.py",
    "cpu_controller": ROOT / "scripts/run_mysql_algorithm_b_top4_mixed_four_worker_cpu_v1.py",
    "io_controller": ROOT / "scripts/run_mysql_algorithm_b_top4_mixed_four_worker_io_v1.py",
    "scheduler": ROOT / "scripts/mysql_algorithm_b_fresh_parallel_4x_scheduler_v26.py",
    "reset_authority": ROOT / "scripts/reset_mysql_xp_data_planes_mixed_four_worker_v1.py",
    "point_authority": ROOT / "scripts/run_mysql_four_scene_mixed_four_worker_authority_v1.py",
    "scene_independent_measurement": ROOT / "scripts/run_mysql_four_scene_algorithm_b_scene_independent_measurement_v5.py",
    "scene_independent_measurement_predecessor_v4": ROOT / "scripts/run_mysql_four_scene_algorithm_b_scene_independent_measurement_v4.py",
    "scene_independent_measurement_predecessor_v3": ROOT / "scripts/run_mysql_four_scene_algorithm_b_scene_independent_measurement_v3.py",
    "adoption": ROOT / "scripts/adopt_mysql_mixed_disk_scene_w34_v1.py",
    "authority_builder": ROOT / "scripts/build_mysql_mixed_disk_four_worker_scene_authority_v1.py",
    "dynamic_noninterference_gate": ROOT / "scripts/mysql_cross_project_wave_coordination_v14.py",
    "dynamic_noninterference_predecessor": ROOT / "scripts/mysql_cross_project_wave_coordination_v13.py",
    "resource_freeze_builder": ROOT / "scripts/build_mysql_mixed_disk_four_worker_resource_release_v3.py",
}


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
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError(f"fresh release output required: {OUTPUT}")
    if any(not path.is_file() or path.is_symlink() for path in SOURCES.values()):
        raise RuntimeError("mixed four-worker source binding is absent")
    freeze = load(FREEZE)
    if (
        freeze.get("schema_version")
        != "mysql_four_scene_mixed_disk_four_worker_successor_v3"
        or len(freeze.get("workers") or []) != 16
        or freeze.get("formal_scope") != "mysql_only"
        or freeze.get("cross_engine_comparison_prohibited") is not True
    ):
        raise RuntimeError("mixed four-worker resource freeze differs")
    contract = load(PREDECESSOR / "algorithm_b_top4_contract.json")
    contract.update({
        "schema_version": "mysql_algorithm_b_four_scene_top4_mixed_four_worker_contract_v1",
        "status": "FROZEN_RUNTIME_VALIDATED", "created_at": now(),
        "candidate_batches": [[1, 2, 3, 4], [5]],
        "physical_replay_parallelism": 4,
        "maximum_workers_per_scene": 4,
        "maximum_concurrent_mysql_workers": 16,
        "resource_freeze_path": str(FREEZE.relative_to(ROOT)),
        "resource_freeze_sha256": sha(FREEZE),
        "resource_freeze_schema": freeze["schema_version"],
        "disk1_worker_count": 8, "disk2_worker_count": 8,
        "completed_history_preserved_across_parallelism_change": True,
        "candidate_replay_due_only_to_parallelism_change": False,
        "recalibration_due_only_to_parallelism_change": False,
        "fresh_four_lane_baseline_at_boundary": True,
        "four_scene_barrier": False, "sibling_scene_wait": False,
        "lease_mode": "none", "shared_nvme_mutual_exclusion": False,
        "numa_exclusive_scheduler_lock": False,
        "whole_round_resource_lease": False,
        "maximum_idle_load1": 512.0,
        "source_bindings": {
            name: {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}
            for name, path in sorted(SOURCES.items())
        },
    })
    coordination = load(PREDECESSOR / "coordination_contract.json")
    coordination.update({
        "schema_version": "mysql_algorithm_b_top4_mixed_four_worker_no_lease_coordination_v1",
        "status": "FROZEN_RUNTIME_VALIDATED", "created_at": now(),
        "maximum_workers_per_scene": 4,
        "maximum_concurrent_mysql_workers": 16,
        "maximum_quiet_wait_seconds": 600,
        "four_scene_barrier": False, "sibling_scene_wait": False,
        "lease_mode": "none", "shared_nvme_mutual_exclusion": False,
        "numa_exclusive_scheduler_lock": False,
        "whole_round_resource_lease": False,
        "maximum_idle_load1": 512.0,
        "external_process_control_or_stop_performed": False,
        "dynamic_gate_source_path": str(SOURCES["dynamic_noninterference_gate"]),
        "dynamic_gate_source_sha256": sha(SOURCES["dynamic_noninterference_gate"]),
    })
    atomic(OUTPUT / "algorithm_b_top4_contract.json", contract)
    atomic(OUTPUT / "coordination_contract.json", coordination)
    result = {
        "status": "FROZEN_RUNTIME_VALIDATED",
        "contract": str(OUTPUT / "algorithm_b_top4_contract.json"),
        "contract_sha256": sha(OUTPUT / "algorithm_b_top4_contract.json"),
        "coordination": str(OUTPUT / "coordination_contract.json"),
        "coordination_sha256": sha(OUTPUT / "coordination_contract.json"),
    }
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
