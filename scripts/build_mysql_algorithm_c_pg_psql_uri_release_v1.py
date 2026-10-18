#!/usr/bin/env python3
"""Publish v30/v37 recognizing only allowed-port PostgreSQL client URIs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OLD_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260815_v29/optimization_contract.json"
NEW_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260815_v30/optimization_contract.json"
OLD_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260815_v36/coordination_contract.json"
NEW_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260815_v37/coordination_contract.json"
WATCHDOG = ROOT / "history/mysql_four_scene_20260809/v29_formal_outer9_resume_20260815T0308p0800/runtime_external_interference_watchdog.jsonl"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def bind(bindings: dict, label: str, path: Path) -> None:
    bindings[f"{label}_path"] = rel(path)
    bindings[f"{label}_sha256"] = sha(path)


def rebind(bindings: dict) -> None:
    for key, value in list(bindings.items()):
        sha_key = key[:-5] + "_sha256" if key.endswith("_path") else None
        if sha_key is None or sha_key not in bindings:
            continue
        path = (ROOT / str(value)).resolve()
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"runtime release binding differs: {path}")
        bindings[sha_key] = sha(path)


def write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=False)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def main() -> int:
    events = [json.loads(line) for line in WATCHDOG.read_text(encoding="utf-8").splitlines()]
    failures = [
        row for row in events
        if row.get("event") == "runtime_interference_detected_under_exclusive_wave_lease"
    ]
    if len(failures) != 1:
        raise RuntimeError("single psql URI failure required")
    failure = failures[0]
    isolation = failure.get("co_resident_pg_isolation") or {}
    errors = isolation.get("errors") or []
    command_errors = [error for error in errors if "external_command_not_in_frozen_pg_cohort" in error]
    if (
        failure.get("runtime_stage") != "FORMAL_INCUMBENT_BASELINE_RESETTING_XP"
        or isolation.get("workload_count") != 80
        or len(command_errors) != 2
        or len(errors) != 2
        or any(row.get("overlap_target_cpus") for row in isolation.get("workloads", []))
        or isolation.get("external_client_rss_bytes", 2**64) >= 17179869184
    ):
        raise RuntimeError("psql URI failure evidence differs")

    coordination = json.loads(OLD_COORD.read_text(encoding="utf-8"))
    coordination["predecessor"] = {"path": rel(OLD_COORD), "sha256": sha(OLD_COORD)}
    coordination["successor_reason"] = (
        "recognize psql PostgreSQL URIs only when their embedded port belongs to "
        "the exact frozen PG cohort; all CPU, process, memory and storage gates remain"
    )
    allowance = coordination["co_resident_isolated_pg_allowance"]
    allowance["recognized_psql_port_forms"] = [
        "-p PORT", "--port PORT", "--port=PORT", "postgresql://...:PORT/database",
    ]
    allowance["psql_uri_constraints"] = {
        "psql_executable_required": True,
        "embedded_port_must_be_in_allowed_postgres_ports": True,
        "credential_material_not_persisted": True,
        "unknown_port_action": "fail_closed",
    }
    rebind(coordination["source_bindings"])
    bind(coordination["source_bindings"], "pg_psql_uri_failure_watchdog", WATCHDOG)
    write(NEW_COORD, coordination)

    optimization = json.loads(OLD_OPT.read_text(encoding="utf-8"))
    optimization["schema_version"] = "mysql_algorithm_c_outer_runtime_optimization_v30"
    optimization["predecessor"] = {"path": rel(OLD_OPT), "sha256": sha(OLD_OPT)}
    optimization["successor_reason"] = (
        "resume unchanged Outer 8 state after exact allowed-port psql URI recognition; "
        "MySQL Algorithm C and formal measurement semantics are unchanged"
    )
    optimization["co_resident_isolated_pg_allowance"]["coordination_contract_path"] = rel(NEW_COORD)
    optimization["co_resident_isolated_pg_allowance"]["coordination_contract_sha256"] = sha(NEW_COORD)
    bindings = optimization["bindings"]
    bind(bindings, "pg_psql_uri_release_builder", Path(__file__).resolve())
    bind(bindings, "pg_psql_uri_failure_watchdog", WATCHDOG)
    rebind(bindings)
    write(NEW_OPT, optimization)
    print(json.dumps({
        "coordination_sha256": sha(NEW_COORD),
        "optimization_sha256": sha(NEW_OPT),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
