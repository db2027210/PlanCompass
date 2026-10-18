#!/usr/bin/env python3
"""Freeze fresh per-scene four-worker Top4 Algorithm A."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_a_20260826_v1"
CONTRACT = OUTPUT / "algorithm_a_top4_contract.json"
COORDINATION = OUTPUT / "coordination_contract.json"
TOP4 = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_c_20260825_v1"
TABLES = {
    "hybench_xp_cpu": TOP4 / "T.hybench_xp_cpu.top4.operator_physics_v1.json",
    "hybench_xp_io": TOP4 / "T.hybench_xp_io.top4.operator_physics_v1.json",
}
SOURCES = {
    "controller": ROOT / "scripts/run_mysql_algorithm_a_xp_top4_async_v1.py",
    "pg_aligned_a_core": ROOT / "scripts/run_mysql_algorithm_a_async_scene_v3.py",
    "four_worker_runtime_adapter": ROOT / "scripts/run_mysql_algorithm_a_job_to_xp_resume_v1.py",
    "full_factorial": ROOT / "scripts/mysql_algorithm_a_full_factorial_v1.py",
    "pg_outer_policy": ROOT / "scripts/mysql_algorithm_b_pg_outer_policy_v36.py",
    "no_lease_measurement": ROOT / "scripts/mysql_algorithm_b_no_lease_measurement_v1.py",
    "single_scene_measurement": ROOT / "scripts/mysql_single_scene_measurement_v1.py",
    "shared_measurement_core": ROOT / "scripts/run_mysql_four_scene_algorithm_c_v2.py",
    "xp_reset": ROOT / "scripts/reset_mysql_xp_data_planes_four_worker_v1.py",
    "xp_reset_implementation": ROOT / "scripts/reset_mysql_xp_data_planes_v6.py",
    "current_authority_refresh": ROOT / "scripts/refresh_mysql_xp4_live_authority_from_reset_v1.py",
    "scene_launcher": ROOT / "scripts/launch_mysql_algorithm_a_xp_top4_scene_v1.py",
    "workload_adapter": ROOT / "mysql_workload_adapter_v2.py",
    "resource_freeze": ROOT / "config/formal/frozen_mysql_algorithm_c_xp4_handoff_20260826_v2/xp_four_worker_resource_successor.json",
    "xp_cpu_table": TABLES["hybench_xp_cpu"],
    "xp_io_table": TABLES["hybench_xp_io"],
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
        raise RuntimeError("fresh Top4 Algorithm A release required")
    active = {
        scene: load(table)["parameter_space"]["active_params"]
        for scene, table in TABLES.items()
    }
    if any(len(names) != 4 for names in active.values()):
        raise RuntimeError("Top4 Algorithm A parameter contract differs")
    OUTPUT.mkdir(parents=True, mode=0o700)
    atomic(COORDINATION, {
        "schema_version": "mysql_algorithm_a_top4_no_lease_coordination_v1",
        "status": "FROZEN_RUNTIME_VALIDATED", "created_at": now(),
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "per_wave_quiet_seconds": 15.0, "maximum_quiet_wait_seconds": 60.0,
        "maximum_idle_load1": 16.0, "lease_mode": "none",
        "shared_nvme_mutual_exclusion": False,
        "numa_exclusive_scheduler_lock": False,
        "whole_round_resource_lease": False,
        "four_scene_barrier": False, "sibling_scene_wait": False,
        "external_process_control_or_stop_performed": False,
    })
    sources = {name: binding(path) for name, path in SOURCES.items()}
    sources["coordination_contract"] = binding(COORDINATION)
    atomic(CONTRACT, {
        "schema_version": "mysql_algorithm_a_xp_top4_async_contract_v1",
        "status": "FROZEN_RUNTIME_VALIDATED", "created_at": now(),
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "algorithm": "A",
        "method": "exhaustive_binary_factorial_algorithm_a_v2",
        "candidate_search_contract": (
            "lane_local_complete_lr_cartesian_physical_attempt_v2"
        ),
        "accepted_target": 7, "normalized_width_threshold": 0.01,
        "maximum_rejected_outer_attempts": 10,
        "physical_replay_parallelism_per_scene": 4,
        "candidate_batches": [[1, 2, 3, 4]],
        "scenes": {
            scene: {"active_parameters": names, "candidates_per_round": 16,
                    "physical_workers": 4, "physical_waves_per_round": 4}
            for scene, names in active.items()
        },
        "fresh_baseline_required": True,
        "historical_algorithm_b_or_c_credit": False,
        "qps_reference_mode": "last_accepted",
        "prediction_used_for_selection": False,
        "prediction_used_for_ordering": False,
        "complete_round_requires_every_parameter_vector_attempted": True,
        "lease_mode": "none", "four_scene_wave_barrier": False,
        "sibling_scene_wait": False, "per_scene_outer_state_machine": True,
        "post_convergence_stability_required": False,
        "post_target_physical_replays": 0,
        "source_bindings": sources,
    })
    print(json.dumps({"status": "FROZEN", "contract": str(CONTRACT)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
