#!/usr/bin/env python3
"""Freeze the Algorithm C v112 stale-containerd-task recovery release."""

from __future__ import annotations

import copy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
SOURCE_CONTRACT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260906_v111/"
    "algorithm_c_pg_latest_outer_contract.json"
)
SOURCE_OPTIMIZATION = ROOT / (
    "config/formal/frozen_mysql_four_scene_algorithm_c_client_primary_20260906_v111/"
    "outer_runtime_optimization.json"
)
CONTRACT_ROOT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260906_v112"
)
OPTIMIZATION_ROOT = ROOT / (
    "config/formal/frozen_mysql_four_scene_algorithm_c_client_primary_20260906_v112"
)


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"JSON object required: {path}")
    return dict(value)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=False)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def binding(path: str) -> dict[str, str]:
    target = ROOT / path
    if not target.is_file() or target.is_symlink():
        raise RuntimeError(f"regular source required: {target}")
    return {"path": path, "sha256": sha(target)}


def main() -> int:
    if CONTRACT_ROOT.exists() or OPTIMIZATION_ROOT.exists():
        raise RuntimeError("fresh v112 contract roots required")
    optimization = copy.deepcopy(load(SOURCE_OPTIMIZATION))
    optimization.update({
        "schema_version": "mysql_algorithm_c_outer_runtime_optimization_v63",
        "release_version": "v112-stale-containerd-task-recovery",
        "released_at": datetime.now().astimezone().isoformat(),
    })
    optimization["bindings"]["reset_source_path"] = (
        "scripts/reset_mysql_xp_data_planes_client_primary_v5.py"
    )
    optimization["bindings"]["reset_source_sha256"] = sha(
        ROOT / optimization["bindings"]["reset_source_path"]
    )
    optimization["optimized_reset"]["stale_containerd_task_recovery"] = True
    optimization_path = OPTIMIZATION_ROOT / "outer_runtime_optimization.json"
    atomic(optimization_path, optimization)

    contract = copy.deepcopy(load(SOURCE_CONTRACT))
    contract.update({
        "schema_version": "mysql_algorithm_c_pg_latest_outer_contract_v4",
        "release_version": "v112-stale-containerd-task-recovery",
        "created_on": datetime.now().astimezone().date().isoformat(),
        "stale_containerd_task_recovery": {
            "exact_error_required": True,
            "stopped_pid_zero_required": True,
            "experiment_ownership_and_mount_verification_required": True,
            "container_id_must_match_predecessor": True,
            "container_record_removal_allowed": True,
            "bind_mounted_datadir_deleted": False,
            "recreate_from_staged_data_plane": True,
            "all_other_docker_failures_fail_closed": True,
            "completed_outer_history_preserved": True,
            "incomplete_outer_credit": 0,
        },
    })
    contract["complete_lifecycle_recovery"][
        "optimization_contract_bound_to_xp_reset_v5"
    ] = True
    bindings = contract["source_bindings"]
    bindings["mysql_controller"] = binding(
        "scripts/run_mysql_algorithm_c_four_scene_client_primary_v22.py"
    )
    bindings["xp_run_scoped_quarantine"] = binding(
        "scripts/reset_mysql_xp_data_planes_client_primary_v5.py"
    )
    bindings["xp_run_scoped_quarantine_test"] = binding(
        "tests/test_mysql_xp_stale_task_recovery_v5.py"
    )
    bindings["scene_resume_checkpoint"] = binding(
        "scripts/build_mysql_algorithm_c_scene_resume_checkpoint_v5.py"
    )
    bindings["scene_resume_checkpoint_test"] = binding(
        "tests/test_mysql_algorithm_c_scene_resume_checkpoint_v5.py"
    )
    bindings["client_primary_optimization"] = {
        "path": str(optimization_path.relative_to(ROOT)),
        "sha256": sha(optimization_path),
    }
    contract_path = CONTRACT_ROOT / "algorithm_c_pg_latest_outer_contract.json"
    atomic(contract_path, contract)
    print(json.dumps({
        "status": "FROZEN_RUNTIME_VALIDATED",
        "contract": str(contract_path), "contract_sha256": sha(contract_path),
        "optimization": str(optimization_path),
        "optimization_sha256": sha(optimization_path),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
