#!/usr/bin/env python3
"""Publish coordination v59 and optimization v57 for clone clients."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OLD_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260817_v58/coordination_contract.json"
NEW_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260817_v59/coordination_contract.json"
OLD_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260817_v56/optimization_contract.json"
NEW_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260817_v57/optimization_contract.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular evidence required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def bind(bindings: dict[str, Any], label: str, path: Path) -> None:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular binding required: {path}")
    bindings[f"{label}_path"] = rel(path)
    bindings[f"{label}_sha256"] = sha(path)


def write(path: Path, value: dict[str, Any]) -> None:
    if path.exists():
        raise RuntimeError(f"fresh successor already exists: {path}")
    path.parent.mkdir(parents=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def main() -> int:
    coordination = load(OLD_COORD)
    optimization = load(OLD_OPT)
    if coordination.get("release_version") != "v58" or optimization.get("status") != "FROZEN_RUNTIME_VALIDATED":
        raise RuntimeError("predecessor release authority differs")
    coordination["predecessor"] = {"path": rel(OLD_COORD), "sha256": sha(OLD_COORD)}
    coordination["release_version"] = "v59"
    coordination["successor_reason"] = (
        "classify only exact low-footprint psql CREATE DATABASE fullgrid job/tpch "
        "template-clone clients as control-plane; the PostgreSQL server backend "
        "remains a CPU/storage-isolated workload and no external process is changed"
    )
    policy = coordination["co_resident_isolated_pg_allowance"]
    policy["template_clone_control_client_allowance"] = {
        "allowed_databases": ["fullgrid_job", "fullgrid_tpch_sf5"],
        "allowed_templates": ["template_fullgrid_job", "template_fullgrid_tpch_sf5"],
        "owner": "postgres",
        "allowed_ports_bound_by_existing_policy": True,
        "actual_server_backend_remains_workload": True,
        "external_mutation": False,
    }
    additions = {
        "co_resident_profile_installer_v7": ROOT / "scripts/mysql_co_resident_pg_isolation_v7.py",
        "runtime_watchdog_v56_entrypoint": ROOT / "scripts/watch_mysql_formal_external_postgres_interference_v7.py",
        "algorithm_c_v57_entrypoint": ROOT / "scripts/run_mysql_four_scene_algorithm_c_boundary_resume_v16.py",
        "algorithm_c_v57_launcher": ROOT / "scripts/launch_mysql_algorithm_c_boundary_resume_v16.py",
        "template_clone_control_release_builder": Path(__file__),
    }
    for label, path in additions.items():
        bind(coordination["source_bindings"], label, path)
    write(NEW_COORD, coordination)

    optimization["schema_version"] = "mysql_algorithm_c_outer_runtime_optimization_v57"
    optimization["predecessor"] = {"path": rel(OLD_OPT), "sha256": sha(OLD_OPT)}
    optimization["successor_reason"] = coordination["successor_reason"]
    optimization["co_resident_isolated_pg_allowance"].update({
        "coordination_contract_path": rel(NEW_COORD),
        "coordination_contract_sha256": sha(NEW_COORD),
        "exact_template_clone_control_recognition": True,
        "external_mutation": False,
        "algorithm_acceptance_and_convergence_semantics_changed": False,
    })
    bindings = optimization["bindings"]
    bindings["wave_coordination_contract_path"] = rel(NEW_COORD)
    bindings["wave_coordination_contract_sha256"] = sha(NEW_COORD)
    for label, path in additions.items():
        bind(bindings, label, path)
    write(NEW_OPT, optimization)
    print(json.dumps({
        "status": "PASS", "coordination_contract": str(NEW_COORD),
        "coordination_sha256": sha(NEW_COORD), "optimization_contract": str(NEW_OPT),
        "optimization_sha256": sha(NEW_OPT),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
