#!/usr/bin/env python3
"""Freeze the mixed-device XP/IO baseline/champion point replay."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "config/formal/frozen_mysql_xp_io_device_fix_point_replay_20260829_v1"
CONTRACT = OUT / "point_replay_contract.json"
AUTHORITY = ROOT / "history/mysql_four_scene_20260809/v1062_xp_io_device_fix_point_replay_authority_20260829/live_authority.json"
FREEZE = ROOT / "config/formal/frozen_mysql_xp_eight_worker_20260827_v1/xp_effective_resource_successor.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source(path: str) -> dict[str, str]:
    resolved = ROOT / path
    return {"path": path, "sha256": sha(resolved)}


def atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(f"{sha(path)}  {path.name}\n")


def main() -> int:
    if not AUTHORITY.is_file() or AUTHORITY.is_symlink():
        raise RuntimeError("fresh point-replay authority is missing")
    points = {
        "baseline": {
            "disk_temptable_create_cost": 20.0,
            "disk_temptable_row_cost": 0.5,
            "io_block_read_cost": 1.0,
            "key_compare_cost": 0.05,
            "memory_block_read_cost": 0.25,
            "memory_temptable_create_cost": 1.0,
            "memory_temptable_row_cost": 0.1,
            "row_evaluate_cost": 0.1,
        },
        "algorithm_a_champion": {
            "disk_temptable_create_cost": 176.336613355,
            "disk_temptable_row_cost": 1.44513195501,
            "io_block_read_cost": 0.194564006159,
            "key_compare_cost": 0.05,
            "memory_block_read_cost": 0.25,
            "memory_temptable_create_cost": 1.0,
            "memory_temptable_row_cost": 0.0321967844425,
            "row_evaluate_cost": 0.1,
        },
        "algorithm_b_champion": {
            "disk_temptable_create_cost": 30.285714285714285,
            "disk_temptable_row_cost": 2.525,
            "io_block_read_cost": 0.1,
            "key_compare_cost": 0.05,
            "memory_block_read_cost": 0.25,
            "memory_temptable_create_cost": 1.0,
            "memory_temptable_row_cost": 1.0,
            "row_evaluate_cost": 0.1,
        },
        "algorithm_c_champion": {
            "disk_temptable_create_cost": 40.6588410899,
            "disk_temptable_row_cost": 1.01858618625,
            "io_block_read_cost": 2.29664436515,
            "key_compare_cost": 0.05,
            "memory_block_read_cost": 0.25,
            "memory_temptable_create_cost": 1.0,
            "memory_temptable_row_cost": 0.0495988878854,
            "row_evaluate_cost": 0.1,
        },
    }
    assignments = {
        "xp_io_w1": "baseline",
        "xp_io_w3": "baseline",
        "xp_io_w2": "algorithm_a_champion",
        "xp_io_w4": "algorithm_a_champion",
        "xp_io_w5": "algorithm_b_champion",
        "xp_io_w7": "algorithm_b_champion",
        "xp_io_w6": "algorithm_c_champion",
        "xp_io_w8": "algorithm_c_champion",
    }
    evidence = {
        "algorithm_a": source("history/mysql_four_scene_20260809/v1060_to_a_io_round6_v2_resume_20260828_xp_io/algorithm_a_state.json"),
        "algorithm_b": source("history/mysql_four_scene_20260809/v809_to_b_io_outer15_slow_docker_resume_20260827_xp_io/run_state.json"),
        "algorithm_c": source("history/mysql_four_scene_20260809/v1038_to_c_io_outer13_v90_exact_fallback_resume_20260828_xp_io_xp_io/run_state.json"),
    }
    contract = {
        "schema_version": "mysql_xp_io_mixed_device_point_replay_contract_v1",
        "created_at": datetime.now().astimezone().isoformat(),
        "status": "FROZEN_RUNTIME_VALIDATED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "scene": "hybench_xp_io",
        "purpose": "rerun_only_contaminated_baseline_and_existing_a_b_c_champions",
        "algorithm_search_reexecuted": False,
        "accepted_state_credit_increment": 0,
        "historical_artifacts_mutated": False,
        "physical_worker_count": 8,
        "replicas_per_point": 2,
        "required_devices_per_point": ["259:5", "259:6"],
        "all_points_single_concurrent_wave": True,
        "sibling_scene_wait": False,
        "shared_nvme_mutual_exclusion": False,
        "native_xp_workload_unchanged": True,
        "authority": {"path": str(AUTHORITY), "sha256": sha(AUTHORITY)},
        "resource_freeze": {"path": str(FREEZE), "sha256": sha(FREEZE)},
        "points": points,
        "assignments": assignments,
        "source_result_evidence": evidence,
        "source_bindings": {
            "runner": source("scripts/run_mysql_xp_io_device_fix_point_replay_v1.py"),
            "shared_measurement": source("scripts/run_mysql_four_scene_algorithm_c_v2.py"),
            "mixed_device_sampler": source("scripts/run_mysql_four_scene_r0_v5.py"),
            "runtime_configurator": source("scripts/run_mysql_algorithm_a_job_to_xp_resume_v1.py"),
            "eight_worker_reset": source("scripts/reset_mysql_xp_data_planes_mixed_eight_worker_scene_v6.py"),
            "workload_adapter": source("mysql_workload_adapter_v2.py"),
        },
    }
    atomic(CONTRACT, contract)
    print(json.dumps({"status": "PASS", "contract": str(CONTRACT), "sha256": sha(CONTRACT)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
