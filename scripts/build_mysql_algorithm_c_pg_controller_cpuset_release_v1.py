#!/usr/bin/env python3
"""Publish v28/v35 with the frozen PG client/controller CPU sets allowed."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OLD_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260815_v27/optimization_contract.json"
NEW_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260815_v28/optimization_contract.json"
OLD_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260815_v34/coordination_contract.json"
NEW_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260815_v35/coordination_contract.json"
CHECKPOINT = ROOT / "history/mysql_four_scene_20260809/algorithm_c_v27_outer8_checkpoint_v10_20260815T0232p0800/prepared_binding.json"
QUARANTINE = ROOT / "history/mysql_four_scene_20260809/algorithm_c_v27_replay_quarantine_v7_outer8_v10_20260815T0232p0800/replay_quarantine.json"
FAILURE_STATE = ROOT / "history/mysql_four_scene_20260809/v27_formal_outer8_resume_20260815T0109p0800/run_state.json"


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
    checkpoint = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
    quarantine = json.loads(QUARANTINE.read_text(encoding="utf-8"))
    failure = json.loads(FAILURE_STATE.read_text(encoding="utf-8"))
    interruption = failure.get("watchdog_interruption") or {}
    blockers = (interruption.get("evidence") or {}).get("blockers") or []
    if (
        checkpoint.get("status") != "PREPARED"
        or checkpoint.get("completed_outer_attempts") != 8
        or quarantine.get("status") != "PASS"
        or quarantine.get("resume_checkpoint_outer_attempt") != 8
        or failure.get("status") != "FAILED_CLOSED"
        or interruption.get("event") != "runtime_interference_detected_under_exclusive_wave_lease"
        or interruption.get("runtime_stage") != "OUTER_009_BATCH_1_RESETTING_XP"
        or interruption.get("formal_state_credit_increment") != 0
        or sorted({row.get("cpus_allowed_list") for row in blockers}) != ["176-179"]
        or any(row.get("overlap_mems") != [0, 1] for row in blockers)
    ):
        raise RuntimeError("PG controller cpuset failure evidence differs")

    coordination = json.loads(OLD_COORD.read_text(encoding="utf-8"))
    coordination["predecessor"] = {"path": rel(OLD_COORD), "sha256": sha(OLD_COORD)}
    coordination["successor_reason"] = (
        "freeze the already CPU-disjoint PostgreSQL Algorithm C client/controller "
        "sets so their recognized disk1/allowed-port clients pass dynamic isolation"
    )
    allowance = coordination["co_resident_isolated_pg_allowance"]
    allowance["allowed_external_cpu_union"] = "48-91,96-99,128-131,136-183"
    allowance["frozen_controller_client_cpu_sets"] = {
        "xp_cpu": "128-131", "xp_io": "144-159",
        "job_memory": "176-179", "tpch_memory": "180-183",
    }
    allowance["controller_client_constraints"] = {
        "mysql_target_cpu_overlap_required": 0,
        "recognized_pg_command_and_allowed_port_required": True,
        "container_disk1_mount_and_memory_readback_unchanged": True,
        "host_memory_swap_and_psi_gates_unchanged": True,
    }
    rebind(coordination["source_bindings"])
    bind(coordination["source_bindings"], "pg_controller_cpuset_failure_state", FAILURE_STATE)
    write(NEW_COORD, coordination)

    optimization = json.loads(OLD_OPT.read_text(encoding="utf-8"))
    optimization["schema_version"] = "mysql_algorithm_c_outer_runtime_optimization_v28"
    optimization["predecessor"] = {"path": rel(OLD_OPT), "sha256": sha(OLD_OPT)}
    optimization["successor_reason"] = (
        "resume from completed Outer 8 with exact PG controller/client CPU isolation "
        "recorded; Algorithm C and all measurement/acceptance semantics are unchanged"
    )
    optimization["co_resident_isolated_pg_allowance"]["coordination_contract_path"] = rel(NEW_COORD)
    optimization["co_resident_isolated_pg_allowance"]["coordination_contract_sha256"] = sha(NEW_COORD)
    bindings = optimization["bindings"]
    bind(bindings, "pg_controller_cpuset_release_builder", Path(__file__).resolve())
    bind(bindings, "outer8_checkpoint", CHECKPOINT)
    bind(bindings, "outer8_replay_quarantine", QUARANTINE)
    bind(bindings, "pg_controller_cpuset_failure_state", FAILURE_STATE)
    bind(bindings, "outer_checkpoint_builder", ROOT / "scripts/build_mysql_algorithm_c_outer_checkpoint_v3.py")
    rebind(bindings)
    write(NEW_OPT, optimization)
    print(json.dumps({
        "coordination_sha256": sha(NEW_COORD),
        "optimization_sha256": sha(NEW_OPT),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
