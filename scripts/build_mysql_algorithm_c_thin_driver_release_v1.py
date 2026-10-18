#!/usr/bin/env python3
"""Publish coordination v61 and optimization v59 for thin drivers."""

from __future__ import annotations
import hashlib, json, os
from pathlib import Path
from typing import Any
ROOT = Path(__file__).resolve().parents[1]
OLD_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260817_v60/coordination_contract.json"
NEW_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260817_v61/coordination_contract.json"
OLD_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260817_v58/optimization_contract.json"
NEW_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260817_v59/optimization_contract.json"

def sha(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()
def rel(path: Path) -> str: return str(path.resolve().relative_to(ROOT))
def load(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink(): raise RuntimeError(f"regular evidence required: {path}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict): raise RuntimeError(f"JSON object required: {path}")
    return value
def bind(bindings: dict[str, Any], label: str, path: Path) -> None:
    bindings[f"{label}_path"], bindings[f"{label}_sha256"] = rel(path), sha(path)
def write(path: Path, value: dict[str, Any]) -> None:
    if path.exists(): raise RuntimeError(f"fresh successor already exists: {path}")
    path.parent.mkdir(parents=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(f"{sha(path)}  {path.name}\n", encoding="ascii")

def main() -> int:
    coordination, optimization = load(OLD_COORD), load(OLD_OPT)
    if coordination.get("release_version") != "v60" or optimization.get("status") != "FROZEN_RUNTIME_VALIDATED":
        raise RuntimeError("predecessor release authority differs")
    coordination["predecessor"] = {"path": rel(OLD_COORD), "sha256": sha(OLD_COORD)}
    coordination["release_version"] = "v61"
    coordination["successor_reason"] = (
        "admit only exact frozen-port TPC-H BenchBase thin drivers under a quantitative "
        "10-process, 256MiB each, 0.5-total-core ceiling; server backends remain pinned, "
        "all exceedances fail closed, and no external process is changed"
    )
    policy = coordination["co_resident_isolated_pg_allowance"]
    policy["thin_benchbase_tpch_driver_allowance"] = {
        "maximum_processes": 10,
        "maximum_rss_bytes_per_process": 256 * 1024 * 1024,
        "maximum_total_lifetime_cpu_cores": 0.5,
        "exact_command_signature_required": True,
        "allowed_ports_bound_by_existing_policy": True,
        "server_backends_remain_cpu_and_storage_isolated": True,
        "external_mutation": False,
    }
    additions = {
        "co_resident_profile_installer_v9": ROOT / "scripts/mysql_co_resident_pg_isolation_v9.py",
        "runtime_watchdog_v58_entrypoint": ROOT / "scripts/watch_mysql_formal_external_postgres_interference_v9.py",
        "algorithm_c_v59_entrypoint": ROOT / "scripts/run_mysql_four_scene_algorithm_c_boundary_resume_v18.py",
        "algorithm_c_v59_launcher": ROOT / "scripts/launch_mysql_algorithm_c_boundary_resume_v18.py",
        "thin_driver_release_builder": Path(__file__),
    }
    for label, path in additions.items(): bind(coordination["source_bindings"], label, path)
    write(NEW_COORD, coordination)
    optimization["schema_version"] = "mysql_algorithm_c_outer_runtime_optimization_v59"
    optimization["predecessor"] = {"path": rel(OLD_OPT), "sha256": sha(OLD_OPT)}
    optimization["successor_reason"] = coordination["successor_reason"]
    optimization["co_resident_isolated_pg_allowance"].update({
        "coordination_contract_path": rel(NEW_COORD), "coordination_contract_sha256": sha(NEW_COORD),
        "bounded_thin_benchbase_driver_allowance": True, "external_mutation": False,
        "algorithm_acceptance_and_convergence_semantics_changed": False,
    })
    bindings = optimization["bindings"]
    bindings["wave_coordination_contract_path"], bindings["wave_coordination_contract_sha256"] = rel(NEW_COORD), sha(NEW_COORD)
    for label, path in additions.items(): bind(bindings, label, path)
    write(NEW_OPT, optimization)
    print(json.dumps({"status": "PASS", "coordination": str(NEW_COORD), "optimization": str(NEW_OPT)}))
    return 0
if __name__ == "__main__": raise SystemExit(main())
