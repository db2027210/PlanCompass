#!/usr/bin/env python3
"""Publish v29/v36 for frozen PG native concurrency and long psql ports."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OLD_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260815_v28/optimization_contract.json"
NEW_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260815_v29/optimization_contract.json"
OLD_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260815_v35/coordination_contract.json"
NEW_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260815_v36/coordination_contract.json"
CHECKPOINT = ROOT / "history/mysql_four_scene_20260809/algorithm_c_v27_outer8_checkpoint_v10_20260815T0232p0800/prepared_binding.json"
QUARANTINE = ROOT / "history/mysql_four_scene_20260809/algorithm_c_v27_replay_quarantine_v7_outer8_v10_20260815T0232p0800/replay_quarantine.json"
WATCHDOG = ROOT / "history/mysql_four_scene_20260809/v28_formal_outer9_resume_20260815T0243p0800/runtime_external_interference_watchdog.jsonl"
PG_CONFIG_ROOT = Path("/home/anonymous/program/resource_adaptive/config/runtime_pg_nomutex_20260813")


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
    interruptions = [
        row for row in events
        if row.get("event") == "runtime_interference_detected_under_exclusive_wave_lease"
    ]
    if len(interruptions) != 1:
        raise RuntimeError("single runtime interference event required")
    failure = interruptions[0]
    isolation = failure.get("co_resident_pg_isolation") or {}
    errors = isolation.get("errors") or []
    if (
        failure.get("runtime_stage") != "FORMAL_INCUMBENT_BASELINE_MEASURING"
        or isolation.get("workload_count") != 75
        or "external_workload_process_count_exceeds_frozen_limit" not in errors
        or sum("external_command_not_in_frozen_pg_cohort" in error for error in errors) != 2
        or any(row.get("overlap_target_cpus") for row in isolation.get("workloads", []))
    ):
        raise RuntimeError("native PG concurrency failure evidence differs")
    configs = {
        name: json.loads((PG_CONFIG_ROOT / f"{name}.json").read_text(encoding="utf-8"))
        for name in ("xp_cpu", "xp_io", "job_memory", "tpch_memory")
    }
    if (
        {
            key: configs["xp_cpu"]["algorithm_c"]["workload_contract"]["native_xp"][key]
            for key in ("xapclient", "xtpclient")
        } != {"xapclient": 1, "xtpclient": 2}
        or {
            key: configs["xp_io"]["algorithm_c"]["workload_contract"]["native_xp"][key]
            for key in ("xapclient", "xtpclient")
        } != {"xapclient": 13, "xtpclient": 18}
    ):
        raise RuntimeError("frozen PG native XP concurrency differs")

    coordination = json.loads(OLD_COORD.read_text(encoding="utf-8"))
    coordination["predecessor"] = {"path": rel(OLD_COORD), "sha256": sha(OLD_COORD)}
    coordination["successor_reason"] = (
        "derive the PG workload-process bound from frozen native client counts, "
        "recognize psql --port, and avoid summing shared backend RSS as private memory"
    )
    allowance = coordination["co_resident_isolated_pg_allowance"]
    allowance["maximum_external_workload_processes"] = 96
    allowance["maximum_external_client_rss_bytes"] = 17179869184
    allowance["raw_per_process_rss_sum_gate"] = False
    allowance["raw_per_process_rss_sum_reason"] = (
        "PostgreSQL shared pages repeat in each backend RSS; frozen container cgroup "
        "limits plus host headroom/swap/PSI remain the physical memory gates"
    )
    allowance["frozen_workload_process_budget"] = {
        "xp_io_native_two_workers": 64,
        "xp_cpu_native_two_workers": 8,
        "job_two_clients_and_backends": 4,
        "tpch_two_clients_and_backends": 4,
        "plan_probe_safety_margin": 16,
        "total": 96,
    }
    allowance["recognized_psql_port_forms"] = ["-p PORT", "--port PORT", "--port=PORT"]
    allowance["pg_runtime_config_bindings"] = {
        name: {"path": str(PG_CONFIG_ROOT / f"{name}.json"), "sha256": sha(PG_CONFIG_ROOT / f"{name}.json")}
        for name in configs
    }
    rebind(coordination["source_bindings"])
    bind(coordination["source_bindings"], "native_pg_concurrency_failure_watchdog", WATCHDOG)
    write(NEW_COORD, coordination)

    optimization = json.loads(OLD_OPT.read_text(encoding="utf-8"))
    optimization["schema_version"] = "mysql_algorithm_c_outer_runtime_optimization_v29"
    optimization["predecessor"] = {"path": rel(OLD_OPT), "sha256": sha(OLD_OPT)}
    optimization["successor_reason"] = (
        "resume unchanged Outer 8 state after freezing the actual PG native concurrency "
        "envelope; MySQL measurement, acceptance and convergence remain unchanged"
    )
    optimization["co_resident_isolated_pg_allowance"]["coordination_contract_path"] = rel(NEW_COORD)
    optimization["co_resident_isolated_pg_allowance"]["coordination_contract_sha256"] = sha(NEW_COORD)
    bindings = optimization["bindings"]
    bind(bindings, "pg_native_concurrency_release_builder", Path(__file__).resolve())
    bind(bindings, "pg_native_concurrency_failure_watchdog", WATCHDOG)
    rebind(bindings)
    write(NEW_OPT, optimization)
    print(json.dumps({
        "coordination_sha256": sha(NEW_COORD),
        "optimization_sha256": sha(NEW_OPT),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
