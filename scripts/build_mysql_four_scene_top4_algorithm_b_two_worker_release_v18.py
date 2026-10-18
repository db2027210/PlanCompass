#!/usr/bin/env python3
"""Freeze the v61 TPC-H Outer-6 gate-continuation release."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "config/formal/frozen_mysql_four_scene_top4_algorithm_b_20260827_v61"
PREDECESSOR = ROOT / "config/formal/frozen_mysql_four_scene_top4_algorithm_b_20260826_v60"
SOURCES = {
    "builder": Path(__file__).resolve(),
    "controller": ROOT / "scripts/run_mysql_algorithm_b_four_scene_top4_two_worker_v18.py",
    "controller_predecessor": ROOT / "scripts/run_mysql_algorithm_b_four_scene_top4_two_worker_v17.py",
    "algorithm_b_core": ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v1.py",
    "dynamic_noninterference_gate": ROOT / "scripts/mysql_cross_project_wave_coordination_v12.py",
    "dynamic_noninterference_gate_predecessor": ROOT / "scripts/mysql_cross_project_wave_coordination_v11.py",
    "xp_reset_start_timeout": ROOT / "scripts/reset_mysql_xp_data_planes_four_worker_v4.py",
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
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def main() -> int:
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise RuntimeError(f"fresh release required: {OUTPUT}")
    for path in SOURCES.values():
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"release source absent: {path}")
    created = datetime.now(timezone.utc).astimezone().isoformat()
    coordination = load(PREDECESSOR / "coordination_contract.json")
    contract = load(PREDECESSOR / "algorithm_b_top4_contract.json")
    coordination.update({
        "created_at": created,
        "release_version": "v68-container-scoped-pg-statistics-control-plane",
        "dynamic_gate_source_path": str(
            ROOT / "scripts/mysql_cross_project_wave_coordination_v12.py"
        ),
        "dynamic_gate_source_sha256": sha(
            ROOT / "scripts/mysql_cross_project_wave_coordination_v12.py"
        ),
    })
    contract.update({
        "created_at": created,
        "release_version": "v61-tpch-outer6-control-plane-gate-continuation",
        "coordination_contract_path": str(OUTPUT / "coordination_contract.json"),
        "outer6_gate_continuation": {
            "scene": "tpch_sf4_memory",
            "committed_outer_state_reused_through": 5,
            "completed_partial_outer_ranks_reused": [1, 2],
            "failed_gate_measurement_reused": False,
            "candidate_replay_due_to_parallelism_or_gate_failure": False,
            "accepted_or_rejected_credit_for_partial_outer": 0,
            "algorithm_flow_changed": False,
        },
        "container_scoped_pg_statistics_control_plane": {
            "exact_read_only_probe_only": True,
            "live_container_cpu_storage_memory_identity_readback_required": True,
            "actual_workload_overlap_permitted": False,
            "external_process_mutation_performed": False,
        },
        "source_bindings": {
            name: {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}
            for name, path in SOURCES.items()
        },
    })
    OUTPUT.mkdir(parents=True, mode=0o700)
    atomic(OUTPUT / "coordination_contract.json", coordination)
    contract["coordination_contract_sha256"] = sha(
        OUTPUT / "coordination_contract.json"
    )
    atomic(OUTPUT / "algorithm_b_top4_contract.json", contract)
    print(json.dumps({
        "status": "FROZEN_RUNTIME_VALIDATED",
        "contract": str(OUTPUT / "algorithm_b_top4_contract.json"),
        "sha256": sha(OUTPUT / "algorithm_b_top4_contract.json"),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
