#!/usr/bin/env python3
"""Freeze same-worker default baselines for corrected champion comparison."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "config/formal/frozen_mysql_matched_baseline_replay_20260829_v8"
CONTRACT = OUT / "baseline_replay_contract.json"
AUTHORITY_ROOT = ROOT / "history/mysql_four_scene_20260809/v1079_matched_baseline_live_authority_20260829"
CHAMPION_CONTRACT = ROOT / "config/formal/frozen_mysql_contaminated_champion_replay_20260829_v2/champion_replay_contract.json"

DEFAULTS = {
    "disk_temptable_create_cost": 20,
    "disk_temptable_row_cost": 0.5,
    "io_block_read_cost": 1,
    "key_compare_cost": 0.05,
    "memory_block_read_cost": 0.25,
    "memory_temptable_create_cost": 1,
    "memory_temptable_row_cost": 0.1,
    "row_evaluate_cost": 0.1,
}

ASSIGNMENTS = {
    "job_memory": {"job_w1": "b_job_baseline", "job_w2": "c_job_baseline"},
    "tpch_sf4_memory": {"tpch_w1": "c_tpch_baseline"},
    "hybench_xp_cpu": {"xp_cpu_w1": "b_cpu_baseline"},
    "hybench_xp_io": {"xp_io_w1": "b_io_baseline", "xp_io_w2": "c_io_baseline"},
}

POINTS = {
    "b_job_baseline": {"algorithm": "B", "scene": "job_memory"},
    "c_job_baseline": {"algorithm": "C", "scene": "job_memory"},
    "c_tpch_baseline": {"algorithm": "C", "scene": "tpch_sf4_memory"},
    "b_cpu_baseline": {"algorithm": "B", "scene": "hybench_xp_cpu"},
    "b_io_baseline": {"algorithm": "B", "scene": "hybench_xp_io"},
    "c_io_baseline": {"algorithm": "C", "scene": "hybench_xp_io"},
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def bind(path: Path) -> dict[str, str]:
    return {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}


def main() -> int:
    if OUT.exists() or OUT.is_symlink():
        raise RuntimeError(f"fresh matched-baseline contract required: {OUT}")
    live_path = AUTHORITY_ROOT / "live_authority.json"
    live = json.loads(live_path.read_text(encoding="utf-8"))
    if live.get("status") != "PASS" or live.get("formal_launch_authorized") is not True:
        raise RuntimeError("matched-baseline live authority is not launchable")
    prepared = Path(live["artifacts"]["prepared"])
    workers = {
        str(row["worker_id"]): row
        for row in json.loads(prepared.read_text(encoding="utf-8"))["workers"]
    }
    assigned = {worker for rows in ASSIGNMENTS.values() for worker in rows}
    if assigned != set(workers):
        raise RuntimeError("matched-baseline workers differ from live authority")
    points = {
        label: {**metadata, "params": dict(DEFAULTS), "matched_worker": worker}
        for scene, rows in ASSIGNMENTS.items()
        for worker, label in rows.items()
        for metadata in [POINTS[label]]
    }
    champion_results = {
        "job": ROOT / "history/mysql_four_scene_20260809/v1070_contaminated_champion_replay_job_20260829/champion_replay_result.json",
        "tpch": ROOT / "history/mysql_four_scene_20260809/v1071_contaminated_champion_replay_tpch_20260829/champion_replay_result.json",
        "xp_cpu": ROOT / "history/mysql_four_scene_20260809/v1072_contaminated_champion_replay_xp_cpu_20260829/champion_replay_result.json",
        "xp_io": ROOT / "history/mysql_four_scene_20260809/v1073_contaminated_champion_replay_xp_io_20260829/champion_replay_result.json",
    }
    document = {
        "schema_version": "mysql_matched_baseline_replay_contract_v8",
        "created_at": datetime.now().astimezone().isoformat(),
        "status": "FROZEN_RUNTIME_VALIDATED",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
        "baseline_replayed": True,
        "champion_replayed": False,
        "algorithm_search_reexecuted": False,
        "accepted_state_credit_increment": 0,
        "historical_artifacts_mutated": False,
        "xp_reset_cross_scene_lock_used": False,
        "xp_worker_reset_parallelism": "all_workers_assigned_to_each_scene",
        "docker_json_readback_retry_bound_seconds": 600,
        "supersedes_incomplete_runs": [
            "v1080_matched_baseline_replay_job_20260829",
            "v1082_matched_baseline_replay_xp_cpu_20260829",
            "v1083_matched_baseline_replay_xp_io_20260829",
            "v1084_matched_baseline_replay_v2_job_20260829",
            "v1085_matched_baseline_replay_v2_xp_cpu_20260829",
            "v1086_matched_baseline_replay_v2_xp_io_20260829",
            "v1087_matched_baseline_replay_v4_job_20260829",
            "v1088_matched_baseline_replay_v4_xp_cpu_20260829",
            "v1089_matched_baseline_replay_v4_xp_io_20260829",
            "v1090_matched_baseline_replay_v5_job_20260829",
            "v1092_matched_baseline_replay_v5_xp_io_20260829",
            "v1094_matched_baseline_replay_v6_xp_io_20260829",
            "v1093_matched_baseline_replay_v6_job_20260829"
        ],
        "assignments_by_scene": ASSIGNMENTS,
        "points": points,
        "comparison_contract": {
            "pairing": "same_logical_worker_same_device_same_corrected_sampler",
            "default_parameter_vector_identical_for_all_six_baselines": True,
            "cross_worker_averaging_prohibited": True,
            "zero_from_unobserved_sibling_device_prohibited": True,
            "champion_result_contract": bind(CHAMPION_CONTRACT),
        },
        "authority": {
            "manifest_path": str(live_path), "manifest_sha256": sha(live_path),
            "prepared_path": live["artifacts"]["prepared"],
            "prepared_sha256": live["artifacts_sha256"]["prepared"],
            "preflight_path": live["artifacts"]["preflight"],
            "preflight_sha256": live["artifacts_sha256"]["preflight"],
            "accounts_path": live["artifacts"]["accounts"],
            "accounts_sha256": live["artifacts_sha256"]["accounts"],
        },
        "resource_freeze_by_scene": live["resource_freeze_by_scene"],
        "source_bindings": {
            "builder": bind(Path(__file__).resolve()),
            "runner": bind(ROOT / "scripts/run_mysql_matched_baseline_replay_v8.py"),
            "single_scene_measurement": bind(ROOT / "scripts/mysql_single_scene_measurement_v2.py"),
            "parallel_xp_reset": bind(ROOT / "scripts/mysql_async_xp_post_reset_settle_v2.py"),
            "authority_sealer": bind(ROOT / "scripts/seal_mysql_baseline_replay_authority_v6.py"),
            "dynamic_gate": bind(ROOT / "scripts/mysql_cross_project_wave_coordination_v24.py"),
            "coordination_contract": bind(
                ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260829_v76/coordination_contract.json"
            ),
        },
        "source_result_evidence": {
            key: bind(path) for key, path in champion_results.items()
        },
    }
    OUT.mkdir(parents=True, exist_ok=False)
    temporary = CONTRACT.with_name(f".{CONTRACT.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
    os.replace(temporary, CONTRACT)
    CONTRACT.with_name(CONTRACT.name + ".sha256").write_text(
        f"{sha(CONTRACT)}  {CONTRACT.name}\n", encoding="ascii",
    )
    print(json.dumps({"status": "PASS", "contract": str(CONTRACT), "sha256": sha(CONTRACT)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
