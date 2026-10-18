#!/usr/bin/env python3
"""Freeze the fresh per-scene Top-4 Algorithm B release and bootstraps."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_b_20260826_v10"
HISTORY = ROOT / "history/mysql_four_scene_20260809"
TOP4 = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_c_20260825_v1"
CONTROLLER = ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v1.py"
COORDINATION = OUTPUT / "coordination_contract.json"
CONTRACT = OUTPUT / "algorithm_b_top4_contract.json"
PARAMETER_CONTRACT = TOP4 / "parameter_contract.json"
TABLES = {
    "hybench_xp_cpu": TOP4 / "T.hybench_xp_cpu.top4.operator_physics_v1.json",
    "hybench_xp_io": TOP4 / "T.hybench_xp_io.top4.operator_physics_v1.json",
}
BOOTSTRAP_VERSION = "v10"
SOURCES = {
    "controller": CONTROLLER,
    "parallel_221_scheduler": ROOT / "scripts/mysql_algorithm_b_fresh_parallel_221_scheduler_v25.py",
    "no_lease_measurement_runtime": ROOT / "scripts/mysql_algorithm_b_no_lease_measurement_v1.py",
    "dynamic_noninterference_gate": ROOT / "scripts/mysql_cross_project_wave_coordination_v4.py",
    "single_scene_measurement": ROOT / "scripts/run_mysql_four_scene_algorithm_b_scene_independent_measurement_v4.py",
    "shared_algorithm_c_measurement_core": ROOT / "scripts/run_mysql_four_scene_algorithm_c_v2.py",
    "xp_four_worker_reset": ROOT / "scripts/reset_mysql_xp_data_planes_four_worker_v1.py",
    "xp_reset_implementation": ROOT / "scripts/reset_mysql_xp_data_planes_v6.py",
    "xp_four_worker_authority": ROOT / "scripts/run_mysql_xp_four_worker_authority_v1.py",
    "xp_four_worker_resource_freeze": (
        ROOT / "config/formal/frozen_mysql_algorithm_c_xp4_handoff_20260826_v2"
        / "xp_four_worker_resource_successor.json"
    ),
    "scene_launcher": ROOT / "scripts/launch_mysql_xp_top4_algorithm_b_scene_v1.py",
    "abc_pipeline_monitor": ROOT / "scripts/monitor_mysql_four_scene_abc_pipeline_v1.py",
    "top4_parameter_contract": PARAMETER_CONTRACT,
    "xp_cpu_top4_table": TABLES["hybench_xp_cpu"],
    "xp_io_top4_table": TABLES["hybench_xp_io"],
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


def binding(path: Path) -> dict[str, str]:
    return {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError("fresh Top-4 B release directory required")
    parameter = load(PARAMETER_CONTRACT)
    active = parameter.get("active_parameters_by_scene") or {}
    if (
        parameter.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or set(active) != set(TABLES)
        or any(load(TABLES[scene])["parameter_space"]["active_params"] != names
               for scene, names in active.items())
    ):
        raise RuntimeError("Top-4 parameter predecessor differs")
    OUTPUT.mkdir(parents=True, mode=0o700)
    atomic(COORDINATION, {
        "schema_version": "mysql_algorithm_b_top4_no_lease_coordination_v1",
        "status": "FROZEN_RUNTIME_VALIDATED", "created_at": now(),
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "per_wave_quiet_seconds": 15.0, "maximum_quiet_wait_seconds": 60.0,
        "maximum_idle_load1": 16.0, "lease_mode": "none",
        "shared_nvme_mutual_exclusion": False,
        "numa_exclusive_scheduler_lock": False,
        "whole_round_resource_lease": False,
        "four_scene_barrier": False, "sibling_scene_wait": False,
        "external_process_control_or_stop_performed": False,
        "source_bindings": {
            "dynamic_noninterference_gate": binding(
                SOURCES["dynamic_noninterference_gate"]
            ),
        },
    })
    source_bindings = {name: binding(path) for name, path in SOURCES.items()}
    source_bindings["coordination_contract"] = binding(COORDINATION)
    atomic(CONTRACT, {
        "schema_version": "mysql_algorithm_b_xp_top4_async_contract_v1",
        "status": "FROZEN_RUNTIME_VALIDATED", "created_at": now(),
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "algorithm": "B", "active_parameters_by_scene": active,
        "fresh_baseline_required": True,
        "fresh_calibration_replays_per_scene": 15,
        "historical_calibration_or_outer_credit": False,
        "algorithm_c_used_only_as_sequence_gate": True,
        "algorithm_c_state_or_result_credit": False,
        "candidate_batches": [[1, 2, 3, 4], [5]],
        "physical_replay_parallelism": 4,
        "real_top_k": 5, "accepted_target": 10,
        "post_search_stability_confirmation": False,
        "database_interface": "mysql", "prediction_model": "mysql_fresh_resource_model",
        "lease_mode": "none", "four_scene_barrier": False,
        "sibling_scene_wait": False, "per_scene_outer_state_machine": True,
        "source_bindings": source_bindings,
    })
    bootstraps = {}
    for scene, names in active.items():
        suffix = "cpu" if scene == "hybench_xp_cpu" else "io"
        root = HISTORY / (
            f"v161_to_b_top4_fresh_bootstrap_20260826_{BOOTSTRAP_VERSION}_{suffix}"
        )
        if root.exists() or root.is_symlink():
            raise RuntimeError(f"fresh B bootstrap required: {root}")
        root.mkdir(parents=True, mode=0o700)
        path = root / "bootstrap_manifest.json"
        atomic(path, {
            "schema_version": "mysql_algorithm_b_xp_top4_fresh_bootstrap_v1",
            "status": "FROZEN", "created_at": now(), "scene": scene,
            "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
            "active_parameters": names,
            "fresh_baseline_required": True,
            "fresh_calibration_replays_required": 15,
            "historical_calibration_or_outer_credit": False,
            "algorithm_c_state_or_result_credit": False,
            "release_contract_path": str(CONTRACT),
            "release_contract_sha256": sha(CONTRACT),
        })
        bootstraps[scene] = str(root)
    print(json.dumps({"status": "FROZEN", "contract": str(CONTRACT),
                      "bootstraps": bootstraps}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
