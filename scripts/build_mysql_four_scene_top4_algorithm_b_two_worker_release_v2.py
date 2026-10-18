#!/usr/bin/env python3
"""Freeze four-scene Top4-B with tolerant MySQL FLOAT-boundary shrink."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
OUTPUT = ROOT / "config/formal/frozen_mysql_four_scene_top4_algorithm_b_20260826_v45"
HISTORY = ROOT / "history/mysql_four_scene_20260809"
FREEZE = ROOT / "config/formal/frozen_mysql_four_scene_two_worker_20260826_v19/four_scene_effective_resource_successor.json"
TOP4 = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_c_20260825_v1"
TABLES = {
    "job_memory": "T.job_memory.operator_physics_v1.json",
    "tpch_sf4_memory": "T.tpch_memory.operator_physics_v1.json",
    "hybench_xp_cpu": "T.hybench_xp_cpu.top4.operator_physics_v1.json",
    "hybench_xp_io": "T.hybench_xp_io.top4.operator_physics_v1.json",
}
SUFFIXES = {
    "job_memory": "job", "tpch_sf4_memory": "tpch",
    "hybench_xp_cpu": "xp_cpu", "hybench_xp_io": "xp_io",
}
SOURCES = {
    "builder": Path(__file__).resolve(),
    "controller": ROOT / "scripts/run_mysql_algorithm_b_four_scene_top4_two_worker_v2.py",
    "controller_predecessor": ROOT / "scripts/run_mysql_algorithm_b_xp_top4_two_worker_v1.py",
    "scene_launcher": ROOT / "scripts/launch_mysql_four_scene_top4_algorithm_b_two_worker_scene_v2.py",
    "chain_monitor": ROOT / "scripts/monitor_mysql_four_scene_top4_abc_chain_v2.py",
    "scene_authority_refresh": ROOT / "scripts/refresh_mysql_two_worker_scene_authority_v1.py",
    "dynamic_noninterference_gate": ROOT / "scripts/mysql_cross_project_wave_coordination_v8.py",
    "parallel_221_scheduler": ROOT / "scripts/mysql_algorithm_b_fresh_parallel_221_scheduler_v25.py",
    "qps_outer_policy": ROOT / "scripts/mysql_outer_qps_strict_progress_v1.py",
    "rejection_cycle_policy": ROOT / "scripts/mysql_outer_rejection_cycle_v2.py",
    "two_worker_resource_freeze": FREEZE,
    **{f"table_{scene}": TOP4 / filename for scene, filename in TABLES.items()},
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def atomic(path: Path, value: dict[str, Any]) -> None:
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
    for path in SOURCES.values():
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"release source absent: {path}")
    from scripts import mysql_outer_qps_strict_progress_v1 as qps
    from scripts import mysql_outer_rejection_cycle_v2 as rejection
    active = {
        scene: list(load(TOP4 / filename)["parameter_space"]["active_params"])
        for scene, filename in TABLES.items()
    }
    if any(len(names) != 4 for names in active.values()):
        raise RuntimeError("Top4 cardinality differs")
    created = datetime.now(timezone.utc).astimezone().isoformat()
    coordination = {
        "schema_version": "mysql_algorithm_b_top4_two_worker_no_lease_coordination_v1",
        "status": "FROZEN_RUNTIME_VALIDATED", "created_at": created,
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "lease_mode": "none", "four_scene_barrier": False,
        "sibling_scene_wait": False, "shared_nvme_mutual_exclusion": False,
        "numa_exclusive_scheduler_lock": False,
        "whole_round_resource_lease": False,
        "maximum_workers_per_scene": 2, "maximum_concurrent_mysql_workers": 8,
        "per_wave_quiet_seconds": 15, "maximum_quiet_wait_seconds": 180,
        "maximum_idle_load1": 16,
        "external_process_control_or_stop_performed": False,
    }
    contract = {
        "schema_version": "mysql_algorithm_b_four_scene_top4_two_worker_contract_v1",
        "status": "FROZEN_RUNTIME_VALIDATED", "created_at": created,
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "release_version": "v45-four-scene-two-worker-float-boundary",
        "active_parameters_by_scene": active,
        "fresh_baseline_required": True,
        "fresh_calibration_replays_per_scene": 15,
        "candidate_batches": [[1, 2], [3, 4], [5]],
        "physical_replay_parallelism": 2,
        "maximum_concurrent_mysql_workers": 8,
        "lease_mode": "none", "four_scene_barrier": False,
        "sibling_scene_wait": False, "scene_scoped_reset": True,
        "outer_acceptance_policy": qps.policy_contract(),
        "outer_rejection_cycle_policy": rejection.policy_contract(),
        "acceptance_aware_selection_effective_outer": 1,
        "online_resource_model_update_effective_outer": 1,
        "post_search_stability_confirmation": False,
        "resource_freeze_path": str(FREEZE),
        "resource_freeze_sha256": sha(FREEZE),
        "coordination_contract_path": str(OUTPUT / "coordination_contract.json"),
        "source_bindings": {
            name: {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}
            for name, path in SOURCES.items()
        },
    }
    OUTPUT.mkdir(parents=True, mode=0o700)
    atomic(OUTPUT / "coordination_contract.json", coordination)
    contract_path = OUTPUT / "algorithm_b_top4_contract.json"
    atomic(contract_path, contract)
    for scene, suffix in SUFFIXES.items():
        root = HISTORY / f"v405_to_b_top4_two_worker_bootstrap_v45_20260826_{suffix}"
        if root.exists() or root.is_symlink():
            raise RuntimeError(f"fresh bootstrap required: {root}")
        atomic(root / "bootstrap_manifest.json", {
            "schema_version": "mysql_algorithm_b_xp_top4_fresh_bootstrap_v1",
            "status": "FROZEN", "created_at": created, "scene": scene,
            "active_parameters": active[scene],
            "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
            "fresh_baseline_required": True,
            "fresh_calibration_replays_required": 15,
            "historical_calibration_or_outer_credit": False,
            "algorithm_c_state_or_result_credit": False,
            "release_contract_path": str(contract_path),
            "release_contract_sha256": sha(contract_path),
        })
    print(json.dumps({"status": "FROZEN_RUNTIME_VALIDATED", "contract": str(contract_path)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
