#!/usr/bin/env python3
"""Publish v48/v53 after repairing direct-path Python entrypoints."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OLD_COORD = (
    ROOT
    / "config/formal/frozen_mysql_cross_project_wave_coordination_20260816_v52"
    / "coordination_contract.json"
)
NEW_COORD = (
    ROOT
    / "config/formal/frozen_mysql_cross_project_wave_coordination_20260816_v53"
    / "coordination_contract.json"
)
OLD_OPT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260816_v47"
    / "optimization_contract.json"
)
NEW_OPT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260816_v48"
    / "optimization_contract.json"
)
FAILED_STARTUP_STDERR = (
    ROOT / "history/mysql_four_scene_20260809/formal_launch_logs"
    / "v47_formal_outer13_isolated_pg_profiles_20260816_attempt001.stderr.log"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT))


def load(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON object required: {path}")
    return value


def bind(bindings: dict[str, Any], label: str, path: Path) -> None:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f"regular binding required: {path}")
    bindings[f"{label}_path"] = rel(path)
    bindings[f"{label}_sha256"] = sha(path)


def rebind(bindings: dict[str, Any]) -> None:
    for key, value in list(bindings.items()):
        if not key.endswith("_path"):
            continue
        sha_key = key[:-5] + "_sha256"
        if sha_key not in bindings:
            continue
        path = (ROOT / str(value)).resolve()
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"bound release input differs: {path}")
        bindings[sha_key] = sha(path)


def write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=False, mode=0o700)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def main() -> int:
    if "ModuleNotFoundError: No module named 'scripts'" not in (
        FAILED_STARTUP_STDERR.read_text(encoding="utf-8")
    ):
        raise RuntimeError("direct-path startup failure evidence differs")

    coordination = load(OLD_COORD)
    coordination["predecessor"] = {
        "path": rel(OLD_COORD), "sha256": sha(OLD_COORD),
    }
    coordination["release_version"] = "v53"
    coordination["successor_reason"] = (
        "repair direct-path Python entrypoint import authority; sealed profile, "
        "workload, resource, candidate, acceptance and convergence semantics unchanged"
    )
    coordination["isolated_co_resident_runtime_v2"].update({
        "entrypoint_import_repair": True,
        "workload_or_resource_envelope_changed": False,
        "candidate_schedule_scoring_acceptance_and_convergence_changed": False,
    })
    source_bindings = coordination["source_bindings"]
    rebind(source_bindings)
    bind(source_bindings, "co_resident_profile_release_v2_builder", Path(__file__))
    bind(source_bindings, "v47_direct_path_startup_failure", FAILED_STARTUP_STDERR)
    write(NEW_COORD, coordination)

    optimization = load(OLD_OPT)
    optimization["schema_version"] = (
        "mysql_algorithm_c_outer_runtime_optimization_v48"
    )
    optimization["predecessor"] = {
        "path": rel(OLD_OPT), "sha256": sha(OLD_OPT),
    }
    optimization["successor_reason"] = (
        "repair direct-path Python entrypoint import authority; no workload, "
        "resource, Top-5, scoring, binary shrink, acceptance or convergence change"
    )
    allowance = optimization["co_resident_isolated_pg_allowance"]
    allowance["coordination_contract_path"] = rel(NEW_COORD)
    allowance["coordination_contract_sha256"] = sha(NEW_COORD)
    allowance["entrypoint_import_repair"] = True
    bindings = optimization["bindings"]
    rebind(bindings)
    bindings["wave_coordination_contract_path"] = rel(NEW_COORD)
    bindings["wave_coordination_contract_sha256"] = sha(NEW_COORD)
    bind(bindings, "co_resident_profile_release_v2_builder", Path(__file__))
    bind(bindings, "v47_direct_path_startup_failure", FAILED_STARTUP_STDERR)
    write(NEW_OPT, optimization)
    print(json.dumps({
        "coordination_sha256": sha(NEW_COORD),
        "optimization_sha256": sha(NEW_OPT),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

