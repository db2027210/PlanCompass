#!/usr/bin/env python3
"""Publish fail-fast pre-measurement runtime v11 and coordination v18."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OLD_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260814_v10/optimization_contract.json"
NEW_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260814_v11/optimization_contract.json"
OLD_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260814_v17/coordination_contract.json"
NEW_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260814_v18/coordination_contract.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def rebind(bindings: dict) -> None:
    for key, value in list(bindings.items()):
        if key.endswith("_path") and key[:-5] + "_sha256" in bindings:
            bindings[key[:-5] + "_sha256"] = sha((ROOT / str(value)).resolve())


def write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=False)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def main() -> int:
    optimization = json.loads(OLD_OPT.read_text(encoding="utf-8"))
    optimization["schema_version"] = "mysql_algorithm_c_outer_runtime_optimization_v11"
    optimization["predecessor"] = {"path": rel(OLD_OPT), "sha256": sha(OLD_OPT)}
    optimization["successor_reason"] = "single_bounded_pre_measurement_gate_and_persistent_d_state_filter"
    optimization["bounded_pre_measurement_coordination"] = {
        "lease_acquisition_timeout_seconds": 10,
        "required_quiet_seconds": 30,
        "maximum_quiet_wait_seconds": 60,
        "maximum_gate_attempts_per_wave": 1,
        "persistent_d_state_required_consecutive_samples": 3,
        "single_sample_d_state_action": "record_without_resetting_clean_window",
        "persistent_d_state_action": "fail_closed",
        "external_physical_workload_action": "immediate_fail_closed",
        "workload_or_measurement_semantics_changed": False,
    }
    bindings = optimization["bindings"]
    bindings.update({
        "bounded_prestate_test_path": "tests/test_mysql_cross_project_wave_coordination.py",
        "bounded_prestate_test_sha256": "",
        "bounded_prestate_failure_evidence_path": (
            "history/mysql_four_scene_20260809/"
            "v18_formal_bounded_prestate_v10_20260814T1000p0800_algorithm_c_v2/"
            "formal_incumbent_baseline_cross_project_wave_quiet_gate_attempt_001.json"
        ),
        "bounded_prestate_failure_evidence_sha256": "",
    })
    rebind(bindings)
    write(NEW_OPT, optimization)

    coordination = json.loads(OLD_COORD.read_text(encoding="utf-8"))
    coordination["predecessor"] = {"path": rel(OLD_COORD), "sha256": sha(OLD_COORD)}
    coordination["successor_reason"] = "single_bounded_gate_with_persistent_d_state_filter"
    coordination["lease_acquisition_timeout_seconds"] = 10
    coordination["maximum_quiet_wait_while_held_seconds"] = 60
    coordination["maximum_gate_attempts_per_wave"] = 1
    coordination["persistent_d_state_required_consecutive_samples"] = 3
    coordination["single_sample_d_state_action"] = "record_without_resetting_clean_window"
    coordination["persistent_d_state_action"] = "fail_closed"
    rebind(coordination["source_bindings"])
    write(NEW_COORD, coordination)
    print(json.dumps({
        "optimization_sha256": sha(NEW_OPT),
        "coordination_sha256": sha(NEW_COORD),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
