#!/usr/bin/env python3
"""Publish v27/v34 with same-run observer D-state correctly nonblocking."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OLD_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260815_v26/optimization_contract.json"
NEW_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260815_v27/optimization_contract.json"
OLD_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260815_v33/coordination_contract.json"
NEW_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260815_v34/coordination_contract.json"
CHECKPOINT = ROOT / "history/mysql_four_scene_20260809/algorithm_c_v25_outer7_checkpoint_v9_20260815T0100p0800/prepared_binding.json"
QUARANTINE = ROOT / "history/mysql_four_scene_20260809/algorithm_c_v25_replay_quarantine_v6_outer7_v9_20260815T0102p0800/replay_quarantine.json"
FAILURE = ROOT / "history/mysql_four_scene_20260809/v25_formal_outer7_resume_20260814T2339p0800/outer_008/top5_batch_1_cross_project_wave_quiet_gate_attempt_001.json"


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
    checkpoint = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
    quarantine = json.loads(QUARANTINE.read_text(encoding="utf-8"))
    failure = json.loads(FAILURE.read_text(encoding="utf-8"))
    same_run_pids = {
        int(pid)
        for sample in failure.get("samples", [])
        for pid in sample.get("persistent_d_state_pids", [])
    }
    if (
        checkpoint.get("status") != "PREPARED"
        or checkpoint.get("completed_outer_attempts") != 7
        or quarantine.get("status") != "PASS"
        or quarantine.get("resume_checkpoint_outer_attempt") != 7
        or failure.get("status") != "BLOCKED_FAIL_CLOSED"
        or failure.get("errors") != ["coordinated_quiet_window_timeout"]
        or not same_run_pids
    ):
        raise RuntimeError("same-run D-state release evidence differs")

    coordination = json.loads(OLD_COORD.read_text(encoding="utf-8"))
    coordination["predecessor"] = {
        "path": rel(OLD_COORD), "sha256": sha(OLD_COORD),
    }
    coordination["successor_reason"] = (
        "record but do not classify a controller or watchdog bound to the "
        "same unique formal run id as external D-state interference"
    )
    coordination["same_run_observer_d_state_policy"] = {
        "scope": "process cmdline contains the exact unique formal run id",
        "raw_d_state_remains_recorded": True,
        "external_interference_classification": False,
        "persistent_external_d_state_gate_unchanged": True,
        "quiet_seconds_unchanged": True,
        "maximum_wait_seconds_unchanged": True,
    }
    rebind(coordination["source_bindings"])
    bind(coordination["source_bindings"], "same_run_dstate_failure_evidence", FAILURE)
    write(NEW_COORD, coordination)

    optimization = json.loads(OLD_OPT.read_text(encoding="utf-8"))
    optimization["schema_version"] = "mysql_algorithm_c_outer_runtime_optimization_v27"
    optimization["predecessor"] = {
        "path": rel(OLD_OPT), "sha256": sha(OLD_OPT),
    }
    optimization["successor_reason"] = (
        "preserve completed Outer-7 state and prevent the same-run watchdog "
        "from being misclassified as external D-state interference"
    )
    optimization["co_resident_isolated_pg_allowance"][
        "coordination_contract_path"
    ] = rel(NEW_COORD)
    optimization["co_resident_isolated_pg_allowance"][
        "coordination_contract_sha256"
    ] = sha(NEW_COORD)
    bindings = optimization["bindings"]
    builder = Path(__file__).resolve()
    bind(bindings, "same_run_dstate_release_builder", builder)
    bind(bindings, "outer7_checkpoint", CHECKPOINT)
    bind(bindings, "outer7_replay_quarantine", QUARANTINE)
    bind(bindings, "same_run_dstate_failure_evidence", FAILURE)
    bind(bindings, "empty_quarantine_successor_builder", ROOT / "scripts/build_mysql_algorithm_c_empty_replay_quarantine_successor_v1.py")
    bind(bindings, "outer_checkpoint_builder", ROOT / "scripts/build_mysql_algorithm_c_outer_checkpoint_v2.py")
    rebind(bindings)
    write(NEW_OPT, optimization)
    print(json.dumps({
        "coordination_sha256": sha(NEW_COORD),
        "optimization_sha256": sha(NEW_OPT),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
