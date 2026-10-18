#!/usr/bin/env python3
"""Publish coordination v58 and optimization v56 for launcher wrappers."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OLD_COORD = (
    ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260817_v57"
    / "coordination_contract.json"
)
NEW_COORD = (
    ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260817_v58"
    / "coordination_contract.json"
)
OLD_OPT = (
    ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260817_v55"
    / "optimization_contract.json"
)
NEW_OPT = (
    ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260817_v56"
    / "optimization_contract.json"
)


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
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def main() -> int:
    coordination = load(OLD_COORD)
    optimization = load(OLD_OPT)
    if (
        coordination.get("release_version") != "v57"
        or optimization.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or optimization.get("formal_scope") != "mysql_only"
        or optimization.get("cross_engine_comparison_prohibited") is not True
    ):
        raise RuntimeError("predecessor release authority differs")
    coordination["predecessor"] = {
        "path": rel(OLD_COORD), "sha256": sha(OLD_COORD),
    }
    coordination["release_version"] = "v58"
    coordination["successor_reason"] = (
        "recognize only tiny full-cpuset disk1 launcher wrappers when a direct "
        "child is already pinned to the frozen CPU-disjoint cohort; no external "
        "mutation and persistent or unpaired overlap remains fail-closed"
    )
    policy = coordination["co_resident_isolated_pg_allowance"]
    policy["transient_launcher_wrapper_allowance"] = {
        "maximum_rss_bytes": 8 * 1024 * 1024,
        "full_host_cpuset_required": True,
        "direct_child_cpu_disjointness_required": True,
        "disk1_lineage_required": True,
        "persistent_or_unpaired_overlap_action": "fail_closed",
        "external_mutation": False,
    }
    coord_bindings = coordination["source_bindings"]
    additions = {
        "co_resident_profile_installer_v6": ROOT / "scripts/mysql_co_resident_pg_isolation_v6.py",
        "runtime_watchdog_v55_entrypoint": ROOT / "scripts/watch_mysql_formal_external_postgres_interference_v6.py",
        "algorithm_c_v56_entrypoint": ROOT / "scripts/run_mysql_four_scene_algorithm_c_boundary_resume_v15.py",
        "algorithm_c_v56_launcher": ROOT / "scripts/launch_mysql_algorithm_c_boundary_resume_v15.py",
        "transient_launcher_release_builder": Path(__file__),
    }
    for label, path in additions.items():
        bind(coord_bindings, label, path)
    write(NEW_COORD, coordination)

    optimization["schema_version"] = "mysql_algorithm_c_outer_runtime_optimization_v56"
    optimization["predecessor"] = {
        "path": rel(OLD_OPT), "sha256": sha(OLD_OPT),
    }
    optimization["successor_reason"] = coordination["successor_reason"]
    optimization["co_resident_isolated_pg_allowance"].update({
        "coordination_contract_path": rel(NEW_COORD),
        "coordination_contract_sha256": sha(NEW_COORD),
        "transient_launcher_wrapper_recognition": True,
        "external_mutation": False,
        "algorithm_acceptance_and_convergence_semantics_changed": False,
    })
    opt_bindings = optimization["bindings"]
    opt_bindings["wave_coordination_contract_path"] = rel(NEW_COORD)
    opt_bindings["wave_coordination_contract_sha256"] = sha(NEW_COORD)
    for label, path in additions.items():
        bind(opt_bindings, label, path)
    write(NEW_OPT, optimization)
    print(json.dumps({
        "status": "PASS",
        "coordination_contract": str(NEW_COORD),
        "coordination_sha256": sha(NEW_COORD),
        "optimization_contract": str(NEW_OPT),
        "optimization_sha256": sha(NEW_OPT),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
