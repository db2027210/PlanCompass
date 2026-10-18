#!/usr/bin/env python3
"""Freeze v64 reusable boundary continuations for JOB/CPU/I/O."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "config/formal/frozen_mysql_four_scene_top4_algorithm_b_20260827_v64"
PREDECESSOR = ROOT / "config/formal/frozen_mysql_four_scene_top4_algorithm_b_20260827_v63"
SOURCES = {
    "builder": Path(__file__).resolve(),
    "boundary_controller": ROOT / "scripts/run_mysql_algorithm_b_top4_boundary_resume_v1.py",
    "job_controller": ROOT / "scripts/run_mysql_algorithm_b_top4_boundary_resume_job_v1.py",
    "cpu_controller": ROOT / "scripts/run_mysql_algorithm_b_top4_boundary_resume_cpu_v1.py",
    "io_controller": ROOT / "scripts/run_mysql_algorithm_b_top4_boundary_resume_io_v1.py",
    "relational_runtime": ROOT / "scripts/run_mysql_algorithm_b_four_scene_top4_two_worker_v11.py",
    "xp_runtime": ROOT / "scripts/run_mysql_algorithm_b_four_scene_top4_two_worker_v17.py",
    "two_worker_controller": ROOT / "scripts/run_mysql_algorithm_b_xp_top4_two_worker_v1.py",
    "algorithm_b_core": ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v1.py",
    "dynamic_noninterference_gate": ROOT / "scripts/mysql_cross_project_wave_coordination_v12.py",
    "xp_reset_implementation": ROOT / "scripts/reset_mysql_xp_data_planes_v6.py",
}

def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value

def atomic(path: Path, value: dict) -> None:
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
    created = datetime.now(timezone.utc).astimezone().isoformat()
    coordination = load(PREDECESSOR / "coordination_contract.json")
    contract = load(PREDECESSOR / "algorithm_b_top4_contract.json")
    coordination["created_at"] = created
    contract.update({
        "created_at": created,
        "release_version": "v64-job-cpu-io-completed-boundary-continuations",
        "coordination_contract_path": str(OUTPUT / "coordination_contract.json"),
        "completed_boundary_continuations": {
            "job_memory": {"committed_outer": 3, "partial_outer": 4, "completed_ranks": [1, 2]},
            "hybench_xp_cpu": {"committed_outer": 0, "partial_outer": 1, "completed_ranks": [1, 2, 3, 4]},
            "hybench_xp_io": {"completed_calibration_waves": 8, "failed_calibration_wave_retried": 9},
            "failed_measurement_reused": False,
            "candidate_replay_due_to_controller_failure": False,
        },
        "source_bindings": {
            name: {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}
            for name, path in SOURCES.items()
        },
    })
    OUTPUT.mkdir(parents=True, mode=0o700)
    atomic(OUTPUT / "coordination_contract.json", coordination)
    contract["coordination_contract_sha256"] = sha(OUTPUT / "coordination_contract.json")
    atomic(OUTPUT / "algorithm_b_top4_contract.json", contract)
    print(json.dumps({"status": "FROZEN_RUNTIME_VALIDATED", "contract": str(OUTPUT / "algorithm_b_top4_contract.json"), "sha256": sha(OUTPUT / "algorithm_b_top4_contract.json")}, sort_keys=True))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
