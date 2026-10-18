#!/usr/bin/env python3
"""Publish v35/v41 after proving the PG affinity-startup watchdog TOCTOU."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OLD_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260815_v34/optimization_contract.json"
NEW_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260815_v35/optimization_contract.json"
OLD_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260815_v40/coordination_contract.json"
NEW_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260815_v41/coordination_contract.json"
FAILED_ROOT = ROOT / "history/mysql_four_scene_20260809/v34_formal_outer9_rank3_resume_20260815T1055p0800"
FAILED_STATE = FAILED_ROOT / "run_state.json"
WATCHDOG = FAILED_ROOT / "runtime_external_interference_watchdog.jsonl"
TEST = ROOT / "tests/test_mysql_algorithm_c_boundary_technical_isolation.py"


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
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii",
    )


def main() -> int:
    state = json.loads(FAILED_STATE.read_text(encoding="utf-8"))
    events = [
        json.loads(line) for line in WATCHDOG.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    failures = [
        row for row in events
        if row.get("event") == "runtime_interference_detected_under_exclusive_wave_lease"
    ]
    failure = failures[0] if len(failures) == 1 else {}
    isolation = failure.get("co_resident_pg_isolation") or {}
    bad = [
        row for row in (isolation.get("workloads") or [])
        if "external_cpu_overlaps_mysql" in (row.get("errors") or [])
    ]
    if (
        state.get("status") != "FAILED_CLOSED"
        or state.get("accepted_rounds")
        != {"hybench_xp_cpu": 3, "hybench_xp_io": 2, "job_memory": 4, "tpch_sf4_memory": 4}
        or failure.get("runtime_stage") != "OUTER_009_BATCH_5_MEASURING"
        or len(bad) != 2
        or any(row.get("cpus_allowed_list") != "0-511" for row in bad)
        or any(int(row.get("rss_bytes") or 0) > 2 * 1024 * 1024 for row in bad)
        or events[-1].get("event") != "formal_forced_stopped_after_sigint_timeout"
        or (FAILED_ROOT / "outer_009/top5_batch_5/batch_result.json").exists()
        or any(
            json.loads((FAILED_ROOT / f"outer_009/top5_batch_{rank}/batch_result.json").read_text(encoding="utf-8")).get("status") != "PASS"
            for rank in (3, 4)
        )
    ):
        raise RuntimeError("affinity-startup TOCTOU evidence differs")

    coordination = json.loads(OLD_COORD.read_text(encoding="utf-8"))
    coordination["predecessor"] = {"path": rel(OLD_COORD), "sha256": sha(OLD_COORD)}
    coordination["successor_reason"] = (
        "require a fresh same-PID/error persistence confirmation before runtime "
        "termination so a PG wrapper's create-then-affinitize window is not interference"
    )
    coordination["runtime_affinity_startup_confirmation"] = {
        "confirmation_seconds": 0.5,
        "same_pid_and_error_required": True,
        "transient_startup_mask_action": "continue_and_reinventory",
        "persistent_overlap_action": "fail_closed",
        "interrupted_rank_reuse": [1, 2, 3, 4],
        "interrupted_rank_credit": 0,
    }
    rebind(coordination["source_bindings"])
    bind(coordination["source_bindings"], "affinity_startup_release_builder", Path(__file__).resolve())
    bind(coordination["source_bindings"], "affinity_startup_failure_watchdog", WATCHDOG)
    bind(coordination["source_bindings"], "affinity_startup_failure_state", FAILED_STATE)
    bind(coordination["source_bindings"], "affinity_startup_watchdog_test", TEST)
    write(NEW_COORD, coordination)

    optimization = json.loads(OLD_OPT.read_text(encoding="utf-8"))
    optimization["schema_version"] = "mysql_algorithm_c_outer_runtime_optimization_v35"
    optimization["predecessor"] = {"path": rel(OLD_OPT), "sha256": sha(OLD_OPT)}
    optimization["successor_reason"] = (
        "resume Outer 9 at rank 5 after four SHA-valid PASS ranks and fix the "
        "transient PG affinity-startup watchdog classification"
    )
    optimization["co_resident_isolated_pg_allowance"]["coordination_contract_path"] = rel(NEW_COORD)
    optimization["co_resident_isolated_pg_allowance"]["coordination_contract_sha256"] = sha(NEW_COORD)
    bindings = optimization["bindings"]
    bind(bindings, "affinity_startup_release_builder", Path(__file__).resolve())
    bind(bindings, "affinity_startup_failure_watchdog", WATCHDOG)
    bind(bindings, "affinity_startup_failure_state", FAILED_STATE)
    bind(bindings, "affinity_startup_watchdog_test", TEST)
    rebind(bindings)
    write(NEW_OPT, optimization)
    print(json.dumps({
        "coordination_sha256": sha(NEW_COORD),
        "optimization_sha256": sha(NEW_OPT),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
