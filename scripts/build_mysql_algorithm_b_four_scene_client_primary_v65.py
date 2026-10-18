#!/usr/bin/env python3
"""Freeze fresh four-scene Algorithm B with client-primary XP QPS."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PREDECESSOR = ROOT / "config/formal/frozen_mysql_four_scene_top4_algorithm_b_20260826_v60"
OUT = ROOT / "config/formal/frozen_mysql_four_scene_algorithm_b_client_primary_20260903_v65"
FREEZE = ROOT / (
    "config/formal/frozen_mysql_four_scene_two_worker_20260903_v20/"
    "four_scene_effective_resource_successor.json"
)
QUALIFICATION = ROOT / (
    "history/mysql_four_scene_20260809/"
    "v1175_xp_io_two_worker_qualification_v6_20260903/qualification_result.json"
)
SOURCES = {
    "builder": Path(__file__).resolve(),
    "controller": ROOT / "scripts/run_mysql_algorithm_b_four_scene_client_primary_v1.py",
    "controller_predecessor": ROOT / "scripts/run_mysql_algorithm_b_four_scene_top4_two_worker_v17.py",
    "client_primary_measurement": ROOT / "scripts/mysql_single_scene_measurement_client_primary_v1.py",
    "physical_measurement_predecessor": ROOT / "scripts/mysql_single_scene_measurement_v2.py",
    "xp_reset": ROOT / "scripts/reset_mysql_xp_data_planes_client_primary_v1.py",
    "strict_qps_outer": ROOT / "scripts/mysql_outer_qps_strict_progress_v1.py",
    "two_reject_cycle": ROOT / "scripts/mysql_outer_rejection_cycle_v2.py",
    "resource_freeze": FREEZE,
    "xp_io_qualification": QUALIFICATION,
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
        raise RuntimeError(f"fresh Algorithm B release required: {OUT}")
    for path in SOURCES.values():
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"release source absent: {path}")
    qualification = json.loads(QUALIFICATION.read_text(encoding="utf-8"))
    if qualification.get("status") != "PASS" or qualification.get("qualification_passed") is not True:
        raise RuntimeError("XP/I/O qualification is not PASS")
    created = datetime.now().astimezone().isoformat()
    coordination = json.loads((PREDECESSOR / "coordination_contract.json").read_text(encoding="utf-8"))
    contract = json.loads((PREDECESSOR / "algorithm_b_top4_contract.json").read_text(encoding="utf-8"))
    coordination.update({
        "created_at": created,
        "release_version": "v65-client-primary-full-parallel",
        "formal_scope": "mysql_only",
        "cross_engine_comparison_prohibited": True,
    })
    contract.update({
        "created_at": created,
        "release_version": "v65-client-primary-full-parallel",
        "status": "FROZEN_RUNTIME_VALIDATED",
        "resource_freeze_path": str(FREEZE),
        "resource_freeze_sha256": sha(FREEZE),
        "coordination_contract_path": str(OUT / "coordination_contract.json"),
        "xp_outer_primary_metric": "native_hybench_xp_client_xap_qps",
        "xp_server_questions_qps_role": "audit_only",
        "job_tpch_metric_semantics_changed": False,
        "xp_io_presearch_qualification": {
            "path": str(QUALIFICATION.relative_to(ROOT)),
            "sha256": sha(QUALIFICATION),
            "environment_only": True,
            "accepted_state_credit_increment": 0,
            "old_b_or_c_search_state_reused": False,
        },
        "full_parallel_launch": {
            "all_four_scenes_started_together": True,
            "workers_per_scene": 2,
            "maximum_concurrent_mysql_workers": 8,
            "four_scene_barrier": False,
            "sibling_scene_wait": False,
            "cross_scene_completion_dependency": False,
            "lease_mode": "none",
        },
        "source_bindings": {
            name: {"path": str(path.relative_to(ROOT)), "sha256": sha(path)}
            for name, path in SOURCES.items()
        },
    })
    OUT.mkdir(parents=True, mode=0o700)
    atomic(OUT / "coordination_contract.json", coordination)
    contract["coordination_contract_sha256"] = sha(OUT / "coordination_contract.json")
    atomic(OUT / "algorithm_b_top4_contract.json", contract)
    print(json.dumps({
        "status": "FROZEN_RUNTIME_VALIDATED",
        "contract": str(OUT / "algorithm_b_top4_contract.json"),
        "sha256": sha(OUT / "algorithm_b_top4_contract.json"),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
