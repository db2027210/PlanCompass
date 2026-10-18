#!/usr/bin/env python3
"""Freeze XP Top4-A two-worker full-factorial release v10."""

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
OUTPUT = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_a_20260826_v10"
FREEZE = (
    ROOT / "config/formal/frozen_mysql_four_scene_two_worker_20260826_v19"
    / "four_scene_effective_resource_successor.json"
)
TOP4 = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_c_20260825_v1"
SOURCES = {
    "builder": Path(__file__).resolve(),
    "controller": ROOT / "scripts/run_mysql_algorithm_a_xp_top4_two_worker_v1.py",
    "scene_launcher": ROOT / "scripts/launch_mysql_algorithm_a_xp_top4_two_worker_scene_v1.py",
    "scene_authority_refresh": ROOT / "scripts/refresh_mysql_two_worker_scene_authority_v1.py",
    "dynamic_noninterference_gate": ROOT / "scripts/mysql_cross_project_wave_coordination_v8.py",
    "pg_aligned_a_core": ROOT / "scripts/run_mysql_algorithm_a_async_scene_v3.py",
    "two_worker_runtime_adapter": ROOT / "scripts/run_mysql_algorithm_a_job_to_xp_resume_v1.py",
    "full_factorial": ROOT / "scripts/mysql_algorithm_a_full_factorial_v1.py",
    "no_lease_measurement": ROOT / "scripts/mysql_algorithm_b_no_lease_measurement_v1.py",
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


def write(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError(f"fresh release required: {OUTPUT}")
    for path in SOURCES.values():
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"release source absent: {path}")
    from scripts import run_mysql_algorithm_a_async_scene_v3 as core
    active = {
        scene: list(load(TOP4 / filename)["parameter_space"]["active_params"])
        for scene, filename in {
            "hybench_xp_cpu": "T.hybench_xp_cpu.top4.operator_physics_v1.json",
            "hybench_xp_io": "T.hybench_xp_io.top4.operator_physics_v1.json",
        }.items()
    }
    if any(len(names) != 4 for names in active.values()):
        raise RuntimeError("Top4 parameter cardinality differs")
    created = datetime.now(timezone.utc).astimezone().isoformat()
    batches = [[index, index + 1] for index in range(1, 17, 2)]
    OUTPUT.mkdir(parents=True, mode=0o700)
    coordination_path = OUTPUT / "coordination_contract.json"
    coordination = {
        "schema_version": "mysql_algorithm_a_top4_two_worker_no_lease_coordination_v1",
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
    write(coordination_path, coordination)
    bindings = {
        name: {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}
        for name, path in SOURCES.items()
    }
    bindings["coordination_contract"] = {
        "path": str(coordination_path.relative_to(ROOT)),
        "sha256": sha(coordination_path),
    }
    contract = {
        "schema_version": "mysql_algorithm_a_xp_top4_two_worker_contract_v1",
        "status": "FROZEN_RUNTIME_VALIDATED", "created_at": created,
        "formal_scope": "mysql_only", "cross_engine_comparison_prohibited": True,
        "release_version": "v10-two-worker", "algorithm": "A",
        "method": core.METHOD,
        "candidate_search_contract": core.SEARCH_CONTRACT,
        "accepted_target": core.TARGET_ACCEPTED,
        "normalized_width_threshold": core.NORMALIZED_WIDTH_THRESHOLD,
        "maximum_rejected_outer_attempts": core.MAX_REJECTED_OUTER_ATTEMPTS,
        "physical_replay_parallelism_per_scene": 2,
        "candidate_batches": batches,
        "scenes": {
            scene: {
                "active_parameters": names, "candidates_per_round": 16,
                "physical_workers": 2, "physical_waves_per_round": 8,
            }
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
        "resource_freeze_path": str(FREEZE),
        "resource_freeze_sha256": sha(FREEZE),
        "source_bindings": bindings,
    }
    contract_path = OUTPUT / "algorithm_a_top4_contract.json"
    write(contract_path, contract)
    print(json.dumps({"status": "FROZEN_RUNTIME_VALIDATED", "contract": str(contract_path)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
