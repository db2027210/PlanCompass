#!/usr/bin/env python3
"""Publish the bounded fast-prestate runtime v14 and coordination v21."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OLD_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260814_v13/optimization_contract.json"
NEW_OPT = ROOT / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260814_v14/optimization_contract.json"
OLD_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260814_v20/coordination_contract.json"
NEW_COORD = ROOT / "config/formal/frozen_mysql_cross_project_wave_coordination_20260814_v21/coordination_contract.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


def rebind(bindings: dict) -> None:
    for key, value in list(bindings.items()):
        if key.endswith("_path") and key[:-5] + "_sha256" in bindings:
            bindings[key[:-5] + "_sha256"] = sha((ROOT / str(value)).resolve())


def write(path: Path, value: dict) -> None:
    # This builder is also the deterministic verifier for its not-yet-activated
    # release, so rebuilding the same version must reproduce the same bytes.
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    path.with_name(path.name + ".sha256").write_text(
        f"{sha(path)}  {path.name}\n", encoding="ascii"
    )


def main() -> int:
    coordination = json.loads(OLD_COORD.read_text(encoding="utf-8"))
    coordination["predecessor"] = {"path": rel(OLD_COORD), "sha256": sha(OLD_COORD)}
    coordination["successor_reason"] = "bound formal prestate latency without weakening fail-closed interference detection"
    coordination["lease_acquisition_timeout_seconds"] = 3
    coordination["per_wave_quiet_seconds"] = 15
    coordination["maximum_quiet_wait_while_held_seconds"] = 20
    coordination["maximum_gate_attempts_per_wave"] = 1
    coordination["fast_prestate_contract"] = {
        "healthy_release_seconds": 15,
        "lease_contention_fail_seconds": 3,
        "quiet_gate_absolute_bound_seconds": 20,
        "external_physical_workload_action": "immediate_fail_closed",
        "r0_baseline_rankings_and_completed_candidates_reused_by_sha": True,
        "workload_measurement_acceptance_and_convergence_unchanged": True,
    }
    rebind(coordination["source_bindings"])
    write(NEW_COORD, coordination)

    optimization = json.loads(OLD_OPT.read_text(encoding="utf-8"))
    optimization["schema_version"] = "mysql_algorithm_c_outer_runtime_optimization_v14"
    optimization["predecessor"] = {"path": rel(OLD_OPT), "sha256": sha(OLD_OPT)}
    optimization["successor_reason"] = "bound formal prestate latency to seconds while retaining rolling fail-closed gate"
    optimization["bounded_pre_measurement_coordination"] = {
        "lease_acquisition_timeout_seconds": 3,
        "required_quiet_seconds": 15,
        "maximum_quiet_wait_seconds": 20,
        "maximum_gate_attempts_per_wave": 1,
        "persistent_d_state_required_consecutive_samples": 3,
        "single_sample_d_state_action": "record_without_resetting_clean_window",
        "persistent_d_state_action": "fail_closed",
        "external_physical_workload_action": "immediate_fail_closed",
        "cached_state_reuse": ["fresh_r0", "formal_incumbent_baseline", "predicted_rankings", "completed_candidate_batches"],
        "workload_or_measurement_semantics_changed": False,
    }
    optimization["bindings"]["fast_prestate_builder_path"] = rel(Path(__file__).resolve())
    optimization["bindings"]["fast_prestate_builder_sha256"] = sha(Path(__file__).resolve())
    checkpoint_builder = ROOT / "scripts/build_mysql_algorithm_c_outer_checkpoint_v2.py"
    optimization["bindings"]["outer_checkpoint_builder_path"] = rel(checkpoint_builder)
    optimization["bindings"]["outer_checkpoint_builder_sha256"] = sha(checkpoint_builder)
    pause_guard = ROOT / "scripts/mysql_external_pg_lane_pause_guard_v1.py"
    optimization["bindings"]["external_pg_pause_guard_path"] = rel(pause_guard)
    optimization["bindings"]["external_pg_pause_guard_sha256"] = sha(pause_guard)
    rebind(optimization["bindings"])
    write(NEW_OPT, optimization)

    print(json.dumps({
        "optimization_sha256": sha(NEW_OPT),
        "coordination_sha256": sha(NEW_COORD),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
