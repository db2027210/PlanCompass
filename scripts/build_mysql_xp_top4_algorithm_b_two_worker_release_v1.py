#!/usr/bin/env python3
"""Freeze XP Top4-B two-worker strict-QPS release v41."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
OUTPUT = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_b_20260826_v41"
FREEZE = (
    ROOT / "config/formal/frozen_mysql_four_scene_two_worker_20260826_v19"
    / "four_scene_effective_resource_successor.json"
)
TOP4 = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_c_20260825_v1"
SOURCES = {
    "builder": Path(__file__).resolve(),
    "controller": ROOT / "scripts/run_mysql_algorithm_b_xp_top4_two_worker_v1.py",
    "scene_launcher": ROOT / "scripts/launch_mysql_xp_top4_algorithm_b_two_worker_scene_v1.py",
    "scene_authority_refresh": ROOT / "scripts/refresh_mysql_two_worker_scene_authority_v1.py",
    "dynamic_noninterference_gate": ROOT / "scripts/mysql_cross_project_wave_coordination_v8.py",
    "parallel_221_scheduler": ROOT / "scripts/mysql_algorithm_b_fresh_parallel_221_scheduler_v25.py",
    "qps_outer_policy": ROOT / "scripts/mysql_outer_qps_strict_progress_v1.py",
    "rejection_cycle_policy": ROOT / "scripts/mysql_outer_rejection_cycle_v1.py",
    "two_worker_resource_freeze": FREEZE,
    "xp_cpu_top4_table": TOP4 / "T.hybench_xp_cpu.top4.operator_physics_v1.json",
    "xp_io_top4_table": TOP4 / "T.hybench_xp_io.top4.operator_physics_v1.json",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def main() -> int:
    if OUTPUT.exists():
        raise RuntimeError(f"fresh release required: {OUTPUT}")
    for path in SOURCES.values():
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"release source absent: {path}")
    from scripts import mysql_outer_qps_strict_progress_v1 as qps
    from scripts import mysql_outer_rejection_cycle_v1 as rejection
    active = {
        scene: list(load(TOP4 / filename)["parameter_space"]["active_params"])
        for scene, filename in {
            "hybench_xp_cpu": "T.hybench_xp_cpu.top4.operator_physics_v1.json",
            "hybench_xp_io": "T.hybench_xp_io.top4.operator_physics_v1.json",
        }.items()
    }
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
        "schema_version": "mysql_algorithm_b_xp_top4_two_worker_contract_v1",
        "status": "FROZEN_RUNTIME_VALIDATED", "created_at": created,
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "release_version": "v41-two-worker",
        "active_parameters_by_scene": active,
        "fresh_baseline_required": True,
        "fresh_calibration_replays_per_scene": 15,
        "candidate_batches": [[1, 2], [3, 4], [5]],
        "physical_replay_parallelism": 2,
        "maximum_concurrent_mysql_workers": 8,
        "lease_mode": "none", "four_scene_barrier": False,
        "sibling_scene_wait": False,
        "scene_scoped_reset": True,
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
    for name, document in (
        ("coordination_contract.json", coordination),
        ("algorithm_b_top4_contract.json", contract),
    ):
        path = OUTPUT / name
        path.write_text(json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        path.with_name(path.name + ".sha256").write_text(
            f"{sha(path)}  {path.name}\n", encoding="ascii",
        )
    print(json.dumps({"status": "FROZEN_RUNTIME_VALIDATED", "output": str(OUTPUT)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
