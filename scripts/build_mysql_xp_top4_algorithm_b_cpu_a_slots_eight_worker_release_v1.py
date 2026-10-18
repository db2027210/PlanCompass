#!/usr/bin/env python3
"""Freeze current eight-worker XP Top4 Algorithm-B runtime."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
PREDECESSOR = (
    ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_b_cpu_a_slots_four_worker_20260828_v46"
)
OUTPUT = (
    ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_b_cpu_a_slots_eight_worker_20260828_v48"
)
FREEZE = (
    ROOT / "config/formal/frozen_mysql_xp_eight_worker_cpu_a_slots_20260828_v6"
    / "xp_effective_resource_successor.json"
)
SOURCES = {
    "builder": Path(__file__).resolve(),
    "runtime": ROOT / "scripts/run_mysql_algorithm_b_xp_top4_cpu_a_slots_eight_worker_v1.py",
    "scene_launcher": ROOT / "scripts/launch_mysql_xp_top4_algorithm_b_cpu_a_slots_eight_worker_scene_v1.py",
    "authority_builder": ROOT / "scripts/prepare_mysql_xp_eight_worker_cpu_a_slots_scene_authority_v1.py",
    "resource_freeze_builder": ROOT / "scripts/build_mysql_xp_eight_worker_cpu_a_slots_resource_v1.py",
    "scheduler": ROOT / "scripts/mysql_algorithm_b_fresh_parallel_8x_scheduler_v27.py",
    "reset_authority": ROOT / "scripts/reset_mysql_xp_data_planes_mixed_eight_worker_v5.py",
    "point_authority": ROOT / "scripts/run_mysql_four_scene_mixed_eight_worker_authority_v1.py",
    "dynamic_noninterference_gate": ROOT / "scripts/mysql_cross_project_wave_coordination_v17.py",
    "qps_policy": ROOT / "scripts/mysql_outer_qps_strict_progress_v1.py",
    "rejection_cycle": ROOT / "scripts/mysql_outer_rejection_cycle_v2.py",
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


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError(f"fresh release required: {OUTPUT}")
    if any(not path.is_file() or path.is_symlink() for path in SOURCES.values()):
        raise RuntimeError("current XP8 B source binding absent")
    freeze = load(FREEZE)
    if (
        freeze.get("schema_version")
        != "mysql_xp_eight_worker_cpu_a_slots_successor_v6"
        or len(freeze.get("workers") or []) != 16
        or freeze.get("formal_scope") != "mysql_only"
        or freeze.get("cross_engine_comparison_prohibited") is not True
    ):
        raise RuntimeError("current XP8 resource freeze differs")
    contract = copy.deepcopy(load(PREDECESSOR / "algorithm_b_top4_contract.json"))
    contract.update({
        "schema_version": "mysql_algorithm_b_xp_top4_cpu_a_slots_eight_worker_contract_v1",
        "status": "FROZEN_RUNTIME_VALIDATED", "created_at": now(),
        "candidate_batches": [[1, 2, 3, 4, 5]],
        "physical_replay_parallelism": 8,
        "maximum_workers_per_scene": 8,
        "maximum_concurrent_mysql_workers": 16,
        "disk1_worker_count": 8, "disk2_worker_count": 8,
        "resource_freeze_path": str(FREEZE.relative_to(ROOT)),
        "resource_freeze_sha256": sha(FREEZE),
        "resource_freeze_schema": freeze["schema_version"],
        "four_scene_barrier": False, "sibling_scene_wait": False,
        "lease_mode": "none", "shared_nvme_mutual_exclusion": False,
        "numa_exclusive_scheduler_lock": False,
        "whole_round_resource_lease": False,
        "candidate_replay_due_only_to_parallelism_change": False,
        "recalibration_due_only_to_parallelism_change": False,
        "completed_history_preserved_across_parallelism_change": True,
        "algorithm_a_candidate_injection": False,
        "algorithm_a_qps_used_for_generation_or_ranking": False,
        "algorithm_a_parameter_ranges_used": False,
        "source_bindings": {
            name: {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}
            for name, path in sorted(SOURCES.items())
        },
    })
    coordination = copy.deepcopy(load(PREDECESSOR / "coordination_contract.json"))
    coordination.update({
        "schema_version": "mysql_algorithm_b_top4_cpu_a_slots_eight_worker_no_lease_coordination_v1",
        "status": "FROZEN_RUNTIME_VALIDATED", "created_at": now(),
        "maximum_workers_per_scene": 8,
        "maximum_concurrent_mysql_workers": 16,
        "maximum_quiet_wait_seconds": 600,
        "maximum_idle_load1": 512.0,
        "four_scene_barrier": False, "sibling_scene_wait": False,
        "lease_mode": "none", "shared_nvme_mutual_exclusion": False,
        "numa_exclusive_scheduler_lock": False,
        "whole_round_resource_lease": False,
        "external_process_control_or_stop_performed": False,
        "dynamic_gate_source_path": str(SOURCES["dynamic_noninterference_gate"]),
        "dynamic_gate_source_sha256": sha(SOURCES["dynamic_noninterference_gate"]),
    })
    atomic(OUTPUT / "algorithm_b_top4_contract.json", contract)
    atomic(OUTPUT / "coordination_contract.json", coordination)
    print(json.dumps({
        "status": "FROZEN_RUNTIME_VALIDATED",
        "contract_sha256": sha(OUTPUT / "algorithm_b_top4_contract.json"),
        "coordination_sha256": sha(OUTPUT / "coordination_contract.json"),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
