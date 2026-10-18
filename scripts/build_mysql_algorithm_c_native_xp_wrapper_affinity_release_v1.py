#!/usr/bin/env python3
"""Publish v39/v45 with native-XP wrapper affinity inheritance."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OLD_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260815_v38/optimization_contract.json"
NEW_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260815_v39/optimization_contract.json"
OLD_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260815_v44/coordination_contract.json"
NEW_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260815_v45/coordination_contract.json"
FAILED_ROOT = ROOT / "history/mysql_four_scene_20260809/v38_formal_outer10_rank3_resume_20260815T1232p0800"
FAILED_STATE = FAILED_ROOT / "run_state.json"
FAILED_WATCHDOG = FAILED_ROOT / "runtime_external_interference_watchdog.jsonl"
FAILED_GUARD = ROOT / "history/mysql_four_scene_20260809/formal_launch_logs/v38_formal_outer10_rank3_resume_20260815T1232p0800.external_pg_affinity.jsonl"
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
    isolation = ((interruption.get("evidence") or {}).get("co_resident_pg_isolation") or {})
    offenders = [row for row in (isolation.get("workloads") or []) if row.get("errors")]
    guard_rows = [json.loads(line) for line in FAILED_GUARD.read_text(encoding="utf-8").splitlines() if line]
    if (
        state.get("outer_attempt") != 9
        or interruption.get("runtime_stage") != "OUTER_010_BATCH_3_MEASURING"
        or interruption.get("formal_state_credit_increment") != 0
        or len(offenders) != 2
        or any(int(row.get("rss_bytes") or 0) > 4 * 1024 * 1024 for row in offenders)
        or any(set(row.get("errors") or []) != {
            "external_cpu_not_in_frozen_union", "external_cpu_overlaps_mysql",
        } for row in offenders)
        or any(row.get("command_sha256") == offenders[0].get("command_sha256")
               for row in guard_rows if row.get("event") == "frozen_external_pg_client_affinity_corrected")
        or isolation.get("container_readback", {}).get("passed") is not True
        or isolation.get("host_swap_total_bytes") != 0
    ):
        raise RuntimeError("native XP wrapper affinity failure evidence differs")

    coordination = json.loads(OLD_COORD.read_text(encoding="utf-8"))
    coordination["predecessor"] = {"path": rel(OLD_COORD), "sha256": sha(OLD_COORD)}
    coordination["successor_reason"] = (
        "recognize the frozen stmt_postgres.toml native-XP launcher before Java exec "
        "so its declared controller_client_cpuset is inherited by the real client"
    )
    policy = coordination["frozen_external_pg_client_affinity_enforcement"]
    policy["client_recognition_markers"] = [
        "com.hybench.HyBench", "stmt_postgres.toml", "benchbase.jar", "psql",
    ]
    policy["native_xp_wrapper_pinned_before_exec"] = True
    policy["same_optimizer_config_cpuset_authority"] = True
    policy["runtime_watchdog_remains_fail_closed"] = True
    policy["formal_resume_rank"] = 3
    rebind(coordination["source_bindings"])
    bind(coordination["source_bindings"], "native_xp_wrapper_affinity_release_builder", Path(__file__).resolve())
    bind(coordination["source_bindings"], "external_pg_affinity_guard", AFFINITY_GUARD)
    bind(coordination["source_bindings"], "native_xp_wrapper_failure_state", FAILED_STATE)
    bind(coordination["source_bindings"], "native_xp_wrapper_failure_watchdog", FAILED_WATCHDOG)
    bind(coordination["source_bindings"], "native_xp_wrapper_failure_guard", FAILED_GUARD)
    write(NEW_COORD, coordination)

    optimization = json.loads(OLD_OPT.read_text(encoding="utf-8"))
    optimization["schema_version"] = "mysql_algorithm_c_outer_runtime_optimization_v39"
    optimization["predecessor"] = {"path": rel(OLD_OPT), "sha256": sha(OLD_OPT)}
    optimization["successor_reason"] = (
        "replay Outer 10 rank 3 after pinning the native-XP wrapper before Java exec"
    )
    optimization["co_resident_isolated_pg_allowance"]["coordination_contract_path"] = rel(NEW_COORD)
    optimization["co_resident_isolated_pg_allowance"]["coordination_contract_sha256"] = sha(NEW_COORD)
    bindings = optimization["bindings"]
    bind(bindings, "native_xp_wrapper_affinity_release_builder", Path(__file__).resolve())
    bind(bindings, "external_pg_affinity_guard", AFFINITY_GUARD)
    bind(bindings, "native_xp_wrapper_failure_state", FAILED_STATE)
    bind(bindings, "native_xp_wrapper_failure_watchdog", FAILED_WATCHDOG)
    bind(bindings, "native_xp_wrapper_failure_guard", FAILED_GUARD)
    rebind(bindings)
    write(NEW_OPT, optimization)
    print(json.dumps({
        "coordination_sha256": sha(NEW_COORD),
        "optimization_sha256": sha(NEW_OPT),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
