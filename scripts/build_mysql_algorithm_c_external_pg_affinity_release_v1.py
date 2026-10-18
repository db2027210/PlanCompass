#!/usr/bin/env python3
"""Publish v37/v43 with config-derived external PG client affinity enforcement."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OLD_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260815_v36/optimization_contract.json"
NEW_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260815_v37/optimization_contract.json"
OLD_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260815_v42/coordination_contract.json"
NEW_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260815_v43/coordination_contract.json"
FAILED_ROOT = ROOT / "history/mysql_four_scene_20260809/v36_formal_outer9_rank5_resume_20260815T1132p0800"
FAILED_STATE = FAILED_ROOT / "run_state.json"
FAILED_WATCHDOG = FAILED_ROOT / "runtime_external_interference_watchdog.jsonl"
AFFINITY_GUARD = ROOT / "scripts/enforce_frozen_external_pg_client_affinity_v1.py"


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
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def main() -> int:
    state = json.loads(FAILED_STATE.read_text(encoding="utf-8"))
    interruption = state.get("watchdog_interruption") or {}
    evidence = interruption.get("evidence") or {}
    isolated = evidence.get("co_resident_pg_isolation") or {}
    rows = isolated.get("workloads") or []
    offenders = [row for row in rows if row.get("errors")]
    if (
        state.get("status") != "FAILED_CLOSED"
        or state.get("stage") != "FAILED"
        or interruption.get("event")
        != "runtime_interference_detected_under_exclusive_wave_lease"
        or interruption.get("runtime_stage") != "OUTER_010_BATCH_3_MEASURING"
        or len(offenders) != 1
        or int(offenders[0].get("rss_bytes") or 0) > 4 * 1024 * 1024
        or set(offenders[0].get("errors") or []) != {
            "external_cpu_not_in_frozen_union", "external_cpu_overlaps_mysql",
        }
        or isolated.get("container_readback", {}).get("passed") is not True
        or isolated.get("host_swap_total_bytes") != 0
    ):
        raise RuntimeError("external PG affinity failure evidence differs")

    coordination = json.loads(OLD_COORD.read_text(encoding="utf-8"))
    coordination["predecessor"] = {"path": rel(OLD_COORD), "sha256": sha(OLD_COORD)}
    coordination["successor_reason"] = (
        "enforce each frozen disk1 PostgreSQL client workload's own declared "
        "controller_client_cpuset before runtime isolation evaluation"
    )
    coordination["frozen_external_pg_client_affinity_enforcement"] = {
        "source_path": rel(AFFINITY_GUARD),
        "source_sha256": sha(AFFINITY_GUARD),
        "discovery_authority": "disk1 optimizer_config algorithm_c.controller_client_cpuset",
        "poll_seconds": 0.25,
        "management_cpuset": "240-241,496-497",
        "postgres_backends_modified": False,
        "containers_modified": False,
        "databases_modified": False,
        "unrelated_processes_modified": False,
        "affinity_readback_required": True,
        "runtime_watchdog_remains_fail_closed": True,
        "interrupted_rank_credit": 0,
        "formal_resume_rank": 3,
    }
    rebind(coordination["source_bindings"])
    bind(coordination["source_bindings"], "external_pg_affinity_release_builder", Path(__file__).resolve())
    bind(coordination["source_bindings"], "external_pg_affinity_guard", AFFINITY_GUARD)
    bind(coordination["source_bindings"], "external_pg_affinity_failure_state", FAILED_STATE)
    bind(coordination["source_bindings"], "external_pg_affinity_failure_watchdog", FAILED_WATCHDOG)
    write(NEW_COORD, coordination)

    optimization = json.loads(OLD_OPT.read_text(encoding="utf-8"))
    optimization["schema_version"] = "mysql_algorithm_c_outer_runtime_optimization_v37"
    optimization["predecessor"] = {"path": rel(OLD_OPT), "sha256": sha(OLD_OPT)}
    optimization["successor_reason"] = (
        "resume Outer 10 rank 3 with config-derived external PG client affinity enforcement"
    )
    optimization["co_resident_isolated_pg_allowance"]["coordination_contract_path"] = rel(NEW_COORD)
    optimization["co_resident_isolated_pg_allowance"]["coordination_contract_sha256"] = sha(NEW_COORD)
    bindings = optimization["bindings"]
    bind(bindings, "external_pg_affinity_release_builder", Path(__file__).resolve())
    bind(bindings, "external_pg_affinity_guard", AFFINITY_GUARD)
    bind(bindings, "external_pg_affinity_failure_state", FAILED_STATE)
    bind(bindings, "external_pg_affinity_failure_watchdog", FAILED_WATCHDOG)
    rebind(bindings)
    write(NEW_OPT, optimization)
    print(json.dumps({
        "coordination_sha256": sha(NEW_COORD),
        "optimization_sha256": sha(NEW_OPT),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
