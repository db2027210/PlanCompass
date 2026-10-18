#!/usr/bin/env python3
"""Publish coordination v60 and optimization v58 for raw clone commands."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OLD_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260817_v59/coordination_contract.json"
NEW_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260817_v60/coordination_contract.json"
OLD_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260817_v57/optimization_contract.json"
NEW_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260817_v58/optimization_contract.json"


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
    bindings[f"{label}_path"] = rel(path)
    bindings[f"{label}_sha256"] = sha(path)


def write(path: Path, value: dict[str, Any]) -> None:
    if path.exists():
        raise RuntimeError(f"fresh successor already exists: {path}")
    path.parent.mkdir(parents=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(f"{sha(path)}  {path.name}\n", encoding="ascii")


def main() -> int:
    coordination = load(OLD_COORD)
    optimization = load(OLD_OPT)
    if coordination.get("release_version") != "v59" or optimization.get("status") != "FROZEN_RUNTIME_VALIDATED":
        raise RuntimeError("predecessor release authority differs")
    coordination["predecessor"] = {"path": rel(OLD_COORD), "sha256": sha(OLD_COORD)}
    coordination["release_version"] = "v60"
    coordination["successor_reason"] = (
        "match the exact template-clone SQL from raw proc cmdline after argv quoting "
        "is flattened; ordinary psql remains a workload and no external process is changed"
    )
    clone = coordination["co_resident_isolated_pg_allowance"]["template_clone_control_client_allowance"]
    clone.update({"raw_cmdline_matching": True, "exact_sql_required": True})
    additions = {
        "co_resident_profile_installer_v8": ROOT / "scripts/mysql_co_resident_pg_isolation_v8.py",
        "runtime_watchdog_v57_entrypoint": ROOT / "scripts/watch_mysql_formal_external_postgres_interference_v8.py",
        "algorithm_c_v58_entrypoint": ROOT / "scripts/run_mysql_four_scene_algorithm_c_boundary_resume_v17.py",
        "algorithm_c_v58_launcher": ROOT / "scripts/launch_mysql_algorithm_c_boundary_resume_v17.py",
        "raw_clone_control_release_builder": Path(__file__),
    }
    for label, path in additions.items():
        bind(coordination["source_bindings"], label, path)
    write(NEW_COORD, coordination)

    optimization["schema_version"] = "mysql_algorithm_c_outer_runtime_optimization_v58"
    optimization["predecessor"] = {"path": rel(OLD_OPT), "sha256": sha(OLD_OPT)}
    optimization["successor_reason"] = coordination["successor_reason"]
    optimization["co_resident_isolated_pg_allowance"].update({
        "coordination_contract_path": rel(NEW_COORD),
        "coordination_contract_sha256": sha(NEW_COORD),
        "raw_cmdline_exact_clone_control_recognition": True,
        "external_mutation": False,
        "algorithm_acceptance_and_convergence_semantics_changed": False,
    })
    bindings = optimization["bindings"]
    bindings["wave_coordination_contract_path"] = rel(NEW_COORD)
    bindings["wave_coordination_contract_sha256"] = sha(NEW_COORD)
    for label, path in additions.items():
        bind(bindings, label, path)
    write(NEW_OPT, optimization)
    print(json.dumps({"status": "PASS", "coordination": str(NEW_COORD), "optimization": str(NEW_OPT)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
