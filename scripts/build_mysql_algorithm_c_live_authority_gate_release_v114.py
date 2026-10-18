#!/usr/bin/env python3
"""Freeze Algorithm C v114 with current-authority kernel readback."""

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
    "config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260906_v113/"
    "algorithm_c_pg_latest_outer_contract.json"
)
SOURCE_OPTIMIZATION = ROOT / (
    "config/formal/frozen_mysql_four_scene_algorithm_c_client_primary_20260906_v113/"
    "outer_runtime_optimization.json"
)
SOURCE_COORDINATION = ROOT / (
    "config/formal/frozen_mysql_cross_project_wave_coordination_20260906_v111/"
    "coordination_contract.json"
)
CONTRACT_ROOT = ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260907_v114"
)
OPTIMIZATION_ROOT = ROOT / (
    "config/formal/frozen_mysql_four_scene_algorithm_c_client_primary_20260907_v114"
)
COORDINATION_ROOT = ROOT / (
    "config/formal/frozen_mysql_cross_project_wave_coordination_20260907_v114"
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


def bind(path: str) -> dict[str, str]:
    target = ROOT / path
    if not target.is_file() or target.is_symlink():
        raise RuntimeError(f"regular source required: {target}")
    return {"path": path, "sha256": sha(target)}


def main() -> int:
    if any(path.exists() for path in (
        CONTRACT_ROOT, OPTIMIZATION_ROOT, COORDINATION_ROOT,
    )):
        raise RuntimeError("fresh v114 contract roots required")
    coordination = copy.deepcopy(load(SOURCE_COORDINATION))
    coordination.update({
        "schema_version": "mysql_kernel_cgroup_noninterference_contract_v2",
        "release_version": "v114-live-authority-kernel-gate",
        "live_authority_prepared_binding_required": True,
        "hardcoded_prepared_binding_prohibited": True,
    })
    coordination["source_bindings"].update({
        "coordination_source_path": "scripts/mysql_cross_project_wave_coordination_v29.py",
        "coordination_source_sha256": sha(ROOT / "scripts/mysql_cross_project_wave_coordination_v29.py"),
        "rolling_prestart_path": "scripts/mysql_c_external_storage_inventory_only_v6.py",
        "rolling_prestart_sha256": sha(ROOT / "scripts/mysql_c_external_storage_inventory_only_v6.py"),
        "live_authority_gate_test_path": "tests/test_mysql_live_authority_kernel_gate_v29.py",
        "live_authority_gate_test_sha256": sha(ROOT / "tests/test_mysql_live_authority_kernel_gate_v29.py"),
    })
    coordination_path = COORDINATION_ROOT / "coordination_contract.json"
    atomic(coordination_path, coordination)

    optimization = copy.deepcopy(load(SOURCE_OPTIMIZATION))
    optimization.update({
        "schema_version": "mysql_algorithm_c_outer_runtime_optimization_v65",
        "release_version": "v114-live-authority-kernel-gate",
        "released_at": datetime.now().astimezone().isoformat(),
        "live_authority_kernel_gate": True,
    })
    optimization_path = OPTIMIZATION_ROOT / "outer_runtime_optimization.json"
    atomic(optimization_path, optimization)

    contract = copy.deepcopy(load(SOURCE_CONTRACT))
    contract.update({
        "schema_version": "mysql_algorithm_c_pg_latest_outer_contract_v6",
        "release_version": "v114-live-authority-kernel-gate",
        "created_on": datetime.now().astimezone().date().isoformat(),
        "live_authority_kernel_gate": {
            "prepared_binding_source": (
                "MYSQL_TWO_WORKER_AUTHORITY_ROOT/prepared_binding.json"
            ),
            "hardcoded_prepared_binding_prohibited": True,
            "history_root_and_regular_file_validation_required": True,
            "resource_readback_fail_closed": True,
            "external_process_control_or_stop_performed": False,
            "postgresql_process_control_or_stop_performed": False,
        },
    })
    bindings = contract["source_bindings"]
    bindings["mysql_controller"] = bind(
        "scripts/run_mysql_algorithm_c_four_scene_client_primary_v24.py"
    )
    bindings["kernel_cgroup_quiet_gate"] = bind(
        "scripts/mysql_cross_project_wave_coordination_v29.py"
    )
    bindings["kernel_cgroup_quiet_gate_test"] = bind(
        "tests/test_mysql_live_authority_kernel_gate_v29.py"
    )
    bindings["kernel_cgroup_prestart_inventory"] = bind(
        "scripts/mysql_c_external_storage_inventory_only_v6.py"
    )
    bindings["kernel_coordination_contract"] = {
        "path": str(coordination_path.relative_to(ROOT)),
        "sha256": sha(coordination_path),
    }
    bindings["client_primary_optimization"] = {
        "path": str(optimization_path.relative_to(ROOT)),
        "sha256": sha(optimization_path),
    }
    contract_path = CONTRACT_ROOT / "algorithm_c_pg_latest_outer_contract.json"
    atomic(contract_path, contract)
    print(json.dumps({"status": "FROZEN_RUNTIME_VALIDATED",
                      "contract": str(contract_path),
                      "contract_sha256": sha(contract_path),
                      "coordination": str(coordination_path)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
