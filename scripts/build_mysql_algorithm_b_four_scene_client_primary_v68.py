#!/usr/bin/env python3
"""Freeze v68 after the v20 host-swap predicate correction."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "config/formal/frozen_mysql_four_scene_algorithm_b_client_primary_20260903_v67"
OUT = ROOT / "config/formal/frozen_mysql_four_scene_algorithm_b_client_primary_20260903_v68"
SOURCES = {
    "builder": Path(__file__).resolve(),
    "controller": ROOT / "scripts/run_mysql_algorithm_b_four_scene_client_primary_v2.py",
    "controller_predecessor": ROOT / "scripts/run_mysql_algorithm_b_four_scene_client_primary_v1.py",
    "client_primary_measurement": ROOT / "scripts/mysql_single_scene_measurement_client_primary_v1.py",
    "xp_reset": ROOT / "scripts/reset_mysql_xp_data_planes_client_primary_v2.py",
    "xp_reset_predecessor": ROOT / "scripts/reset_mysql_xp_data_planes_client_primary_v1.py",
    "identity_refresh": ROOT / "scripts/refresh_mysql_client_primary_authority_after_xp_reset_v1.py",
    "strict_qps_outer": ROOT / "scripts/mysql_outer_qps_strict_progress_v1.py",
    "two_reject_cycle": ROOT / "scripts/mysql_outer_rejection_cycle_v2.py",
    "resource_freeze": ROOT / "config/formal/frozen_mysql_four_scene_two_worker_20260903_v20/four_scene_effective_resource_successor.json",
    "xp_io_qualification": ROOT / "history/mysql_four_scene_20260809/v1175_xp_io_two_worker_qualification_v6_20260903/qualification_result.json",
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic(path: Path, value: dict) -> None:
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
    if OUT.exists() or OUT.is_symlink():
        raise RuntimeError(f"fresh v68 release required: {OUT}")
    coordination = json.loads(
        (SOURCE / "coordination_contract.json").read_text(encoding="utf-8")
    )
    contract = json.loads(
        (SOURCE / "algorithm_b_top4_contract.json").read_text(encoding="utf-8")
    )
    created = datetime.now().astimezone().isoformat()
    coordination.update({
        "created_at": created,
        "release_version": "v68-container-local-no-swap",
    })
    contract.update({
        "created_at": created,
        "release_version": "v68-container-local-no-swap",
        "coordination_contract_path": str(OUT / "coordination_contract.json"),
        "technical_recovery_successor": {
            "failed_scenes": ["hybench_xp_cpu", "hybench_xp_io"],
            "failure_stage": "independent_baseline_reset",
            "formal_measurements_completed_before_failure": 0,
            "accepted_or_rejected_outer_state_reused": False,
            "job_tpch_sibling_controllers_restarted": False,
            "all_four_scenes_were_started_in_original_campaign": True,
            "host_swap_inventory_remains_recorded": True,
            "container_memory_swappiness_required": 0,
            "container_resource_checks_weakened": False,
            "external_process_control_or_stop_performed": False,
        },
        "source_bindings": {
            name: {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}
            for name, path in SOURCES.items()
        },
    })
    OUT.mkdir(parents=True, mode=0o700)
    atomic(OUT / "coordination_contract.json", coordination)
    contract["coordination_contract_sha256"] = sha(
        OUT / "coordination_contract.json"
    )
    atomic(OUT / "algorithm_b_top4_contract.json", contract)
    print(json.dumps({
        "status": "FROZEN_RUNTIME_VALIDATED",
        "contract": str(OUT / "algorithm_b_top4_contract.json"),
        "sha256": sha(OUT / "algorithm_b_top4_contract.json"),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
