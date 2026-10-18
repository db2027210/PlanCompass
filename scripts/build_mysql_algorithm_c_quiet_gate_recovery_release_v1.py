#!/usr/bin/env python3
"""Publish v50/v55 for bounded quiet-gate recovery after a completed rank."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OLD_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260816_v54/coordination_contract.json"
NEW_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260816_v55/coordination_contract.json"
OLD_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260816_v49/optimization_contract.json"
NEW_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260816_v50/optimization_contract.json"
FAILURE_ROOT = ROOT / "history/mysql_four_scene_20260809/v49_formal_outer13_contract_pointer_fix_20260816_attempt006"
FAILURE_STATE = FAILURE_ROOT / "run_state.json"
FAILURE_GATE = FAILURE_ROOT / "outer_013/top5_batch_2_cross_project_wave_quiet_gate_attempt_001.json"
COMPLETED_RANK = FAILURE_ROOT / "outer_013/top5_batch_1/batch_result.json"
ENTRYPOINT = ROOT / "scripts/run_mysql_four_scene_algorithm_c_boundary_resume_v4.py"
LAUNCHER = ROOT / "scripts/launch_mysql_algorithm_c_boundary_resume_v4.py"
INSTALLER = ROOT / "scripts/mysql_co_resident_pg_isolation_v3.py"
TEST = ROOT / "tests/test_mysql_algorithm_c_quiet_gate_partial_resume_v4.py"


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
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def main() -> int:
    state = load(FAILURE_STATE)
    gate = load(FAILURE_GATE)
    batch = load(COMPLETED_RANK)
    if (
        state.get("status") != "FAILED_CLOSED"
        or state.get("error")
        != "RuntimeError: cross-project quiet gate exhausted its single bounded attempt"
        or gate.get("status") != "BLOCKED_FAIL_CLOSED"
        or gate.get("errors") != ["coordinated_quiet_window_timeout"]
        or float(gate.get("required_clean_seconds") or 0) != 15.0
        or float(gate.get("maximum_wait_seconds") or 0) != 20.0
        or not any(
            sample.get("persistent_d_state_pids")
            for sample in gate.get("samples") or []
        )
        or (gate.get("samples") or [{}])[-1].get("errors") not in ([], None)
        or batch.get("status") != "PASS"
        or batch.get("failures") not in ([], None)
        or len(batch.get("measurements") or {}) != 4
        or any(
            row.get("hard_gate_passed") is not True
            or row.get("coverage_complete") is not True
            or int(row.get("rank") or 0) != 1
            for row in (batch.get("measurements") or {}).values()
        )
    ):
        raise RuntimeError("quiet-gate recovery failure evidence differs")

    coordination = load(OLD_COORD)
    coordination["predecessor"] = {
        "path": rel(OLD_COORD), "sha256": sha(OLD_COORD),
    }
    coordination["release_version"] = "v55"
    coordination["maximum_quiet_wait_while_held_seconds"] = 60
    coordination["successor_reason"] = (
        "retain the full 15-second clean window and persistent-D-state fail-closed "
        "semantics while increasing the bounded recovery margin from 5 to 45 "
        "seconds; preserve a completed PASS rank across a premeasurement timeout; "
        "no workload, resource envelope, Top-5, scoring, binary shrink, acceptance "
        "or convergence change"
    )
    coordination["quiet_gate_bounded_recovery"] = {
        "required_clean_seconds": 15,
        "maximum_wait_seconds": 60,
        "maximum_gate_attempts_per_wave": 1,
        "persistent_d_state_required_consecutive_samples": 3,
        "persistent_d_state_action": "fail_closed",
        "completed_rank_reuse_after_premeasurement_timeout": True,
        "interrupted_rank_state_credit": 0,
    }
    coordination["measurement_checkpoint_recovery"] = {
        **coordination.get("measurement_checkpoint_recovery", {}),
        "latest_completed_outer_attempt": 12,
        "latest_interrupted_outer_attempt": 13,
        "latest_reusable_completed_ranks": [1],
        "latest_resume_rank": 2,
        "interrupted_rank_credit": 0,
    }
    source_bindings = coordination["source_bindings"]
    rebind(source_bindings)
    for label, path in {
        "quiet_gate_recovery_release_builder": Path(__file__),
        "algorithm_c_v50_entrypoint": ENTRYPOINT,
        "algorithm_c_v50_launcher": LAUNCHER,
        "co_resident_profile_installer_v3": INSTALLER,
        "quiet_gate_partial_resume_test_v4": TEST,
        "quiet_gate_timeout_failure_state": FAILURE_STATE,
        "quiet_gate_timeout_failure_gate": FAILURE_GATE,
        "quiet_gate_completed_rank1": COMPLETED_RANK,
    }.items():
        bind(source_bindings, label, path)
    write(NEW_COORD, coordination)

    optimization = load(OLD_OPT)
    optimization["schema_version"] = (
        "mysql_algorithm_c_outer_runtime_optimization_v50"
    )
    optimization["predecessor"] = {
        "path": rel(OLD_OPT), "sha256": sha(OLD_OPT),
    }
    optimization["successor_reason"] = coordination["successor_reason"]
    allowance = optimization["co_resident_isolated_pg_allowance"]
    allowance["coordination_contract_path"] = rel(NEW_COORD)
    allowance["coordination_contract_sha256"] = sha(NEW_COORD)
    allowance["formal_completed_rank_reuse_after_quiet_timeout"] = True
    allowance["quiet_gate_required_clean_seconds"] = 15
    allowance["quiet_gate_maximum_wait_seconds"] = 60
    bindings = optimization["bindings"]
    rebind(bindings)
    bindings["wave_coordination_contract_path"] = rel(NEW_COORD)
    bindings["wave_coordination_contract_sha256"] = sha(NEW_COORD)
    for label, path in {
        "quiet_gate_recovery_release_builder": Path(__file__),
        "algorithm_c_v50_entrypoint": ENTRYPOINT,
        "algorithm_c_v50_launcher": LAUNCHER,
        "co_resident_profile_installer_v3": INSTALLER,
        "quiet_gate_partial_resume_test_v4": TEST,
        "quiet_gate_timeout_failure_state": FAILURE_STATE,
        "quiet_gate_timeout_failure_gate": FAILURE_GATE,
        "quiet_gate_completed_rank1": COMPLETED_RANK,
    }.items():
        bind(bindings, label, path)
    write(NEW_OPT, optimization)
    print(json.dumps({
        "coordination_sha256": sha(NEW_COORD),
        "optimization_sha256": sha(NEW_OPT),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

