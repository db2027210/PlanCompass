#!/usr/bin/env python3
"""Freeze the v52 true two-worker Algorithm-B scheduler release."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "config/formal/frozen_mysql_four_scene_top4_algorithm_b_20260826_v52"
PREDECESSOR = ROOT / "config/formal/frozen_mysql_four_scene_top4_algorithm_b_20260826_v51"
SOURCES = {
    "builder": Path(__file__).resolve(),
    "controller": ROOT / "scripts/run_mysql_algorithm_b_four_scene_top4_two_worker_v9.py",
    "controller_predecessor": ROOT / "scripts/run_mysql_algorithm_b_four_scene_top4_two_worker_v8.py",
    "scene_launcher": ROOT / "scripts/launch_mysql_four_scene_top4_algorithm_b_two_worker_scene_v9.py",
    "scene_authority_refresh": ROOT / "scripts/refresh_mysql_two_worker_scene_authority_v3.py",
    "scene_scoped_relational_measurement": ROOT / "scripts/run_mysql_four_scene_algorithm_b_scene_independent_measurement_v5.py",
    "chain_monitor": ROOT / "scripts/monitor_mysql_four_scene_top4_abc_chain_v9.py",
    "true_two_worker_scheduler": ROOT / "scripts/mysql_algorithm_b_fresh_parallel_221_scheduler_v26.py",
    "native_xp_adapter_300s": ROOT / "mysql_workload_adapter_v2.py",
    "native_xp_runner_300s": ROOT / "config/formal/frozen_mysql_async_outer_20260820_v38/runtime/hybench-adaptive-runner-v4.jar",
    "dynamic_noninterference_gate": ROOT / "scripts/mysql_cross_project_wave_coordination_v9.py",
    "qps_outer_policy": ROOT / "scripts/mysql_outer_qps_strict_progress_v1.py",
    "rejection_cycle_policy": ROOT / "scripts/mysql_outer_rejection_cycle_v2.py",
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
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(f"{sha(path)}  {path.name}\n", encoding="ascii")

def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError(f"fresh release required: {OUTPUT}")
    for path in SOURCES.values():
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"release source absent: {path}")
    coordination = load(PREDECESSOR / "coordination_contract.json")
    contract = load(PREDECESSOR / "algorithm_b_top4_contract.json")
    created = datetime.now(timezone.utc).astimezone().isoformat()
    coordination["created_at"] = created
    contract.update({
        "created_at": created,
        "release_version": "v52-four-scene-true-two-worker-221-scheduler",
        "coordination_contract_path": str(OUTPUT / "coordination_contract.json"),
        "candidate_batches": [[1, 2], [3, 4], [5]],
        "physical_replay_parallelism": 2,
        "true_two_worker_scheduler_release": {
            "calibration_cardinality": 2,
            "outer_candidate_batches": [[1, 2], [3, 4], [5]],
            "completed_calibration_may_be_reused_after_scheduler_only_failure": True,
            "accepted_or_rejected_outer_credit_reused": False,
            "algorithm_candidate_set_changed": False,
        },
        "source_bindings": {
            name: {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}
            for name, path in SOURCES.items()
        },
    })
    OUTPUT.mkdir(parents=True, mode=0o700)
    atomic(OUTPUT / "coordination_contract.json", coordination)
    contract_path = OUTPUT / "algorithm_b_top4_contract.json"
    atomic(contract_path, contract)
    print(json.dumps({"status": "FROZEN_RUNTIME_VALIDATED", "contract": str(contract_path)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
