#!/usr/bin/env python3
"""Publish v31/v38 with global load diagnostic under direct isolation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OLD_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260815_v30/optimization_contract.json"
NEW_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260815_v31/optimization_contract.json"
OLD_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260815_v37/coordination_contract.json"
NEW_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260815_v38/coordination_contract.json"
FAILURE_GATE = ROOT / "history/mysql_four_scene_20260809/v30_formal_outer9_resume_20260815T0841p0800/formal_incumbent_baseline_cross_project_wave_quiet_gate_attempt_001.json"


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
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def main() -> int:
    failure = json.loads(FAILURE_GATE.read_text(encoding="utf-8"))
    samples = failure.get("samples") or []
    if (
        failure.get("status") != "BLOCKED_FAIL_CLOSED"
        or failure.get("errors") != ["coordinated_quiet_window_timeout"]
        or not samples
        or not any("host_idle_load1_exceeds_limit" in row.get("errors", []) for row in samples)
        or any(
            (row.get("co_resident_pg_isolation") or {}).get("passed") is not True
            for row in samples
        )
        or any(
            row.get("persistent_d_state_pids")
            for row in samples
        )
    ):
        raise RuntimeError("isolated host-load failure evidence differs")

    coordination = json.loads(OLD_COORD.read_text(encoding="utf-8"))
    coordination["predecessor"] = {"path": rel(OLD_COORD), "sha256": sha(OLD_COORD)}
    coordination["successor_reason"] = (
        "make lagging host-wide load1 diagnostic when direct CPU/resource isolation "
        "passes; CPU overlap, NUMA/memory/swap, storage and persistent D-state remain hard gates"
    )
    coordination["global_load1_policy"] = {
        "decision_role": "diagnostic_only",
        "reason": "host-wide one-minute decay cannot attribute load to the frozen MySQL cpuset",
        "direct_cpu_overlap_gate_required": True,
        "co_resident_resource_readback_required": True,
        "persistent_d_state_gate_required": True,
        "runtime_watchdog_required": True,
    }
    rebind(coordination["source_bindings"])
    bind(coordination["source_bindings"], "isolated_load_failure_gate", FAILURE_GATE)
    bind(coordination["source_bindings"], "isolated_load_release_builder", Path(__file__).resolve())
    write(NEW_COORD, coordination)

    optimization = json.loads(OLD_OPT.read_text(encoding="utf-8"))
    optimization["schema_version"] = "mysql_algorithm_c_outer_runtime_optimization_v31"
    optimization["predecessor"] = {"path": rel(OLD_OPT), "sha256": sha(OLD_OPT)}
    optimization["successor_reason"] = (
        "resume unchanged Outer 8 state using direct isolated-resource evidence instead "
        "of lagging host-wide load1; measurement and convergence semantics are unchanged"
    )
    optimization["co_resident_isolated_pg_allowance"]["coordination_contract_path"] = rel(NEW_COORD)
    optimization["co_resident_isolated_pg_allowance"]["coordination_contract_sha256"] = sha(NEW_COORD)
    optimization["bounded_pre_measurement_coordination"]["global_load1_policy"] = "diagnostic_only"
    bindings = optimization["bindings"]
    bind(bindings, "isolated_load_release_builder", Path(__file__).resolve())
    bind(bindings, "isolated_load_failure_gate", FAILURE_GATE)
    rebind(bindings)
    write(NEW_OPT, optimization)
    print(json.dumps({
        "coordination_sha256": sha(NEW_COORD),
        "optimization_sha256": sha(NEW_OPT),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
