#!/usr/bin/env python3
"""Resume one XP scene on four workers with external diagnostics only."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_external_workload_diagnostic_gate_v1 as diagnostic_gate
from scripts import run_mysql_algorithm_a_async_scene_v3 as current_base
from scripts import run_mysql_algorithm_a_job_to_xp_resume_v1 as predecessor


_ORIGINAL_CONFIGURE = predecessor.configure_runtime
_ORIGINAL_VALIDATE_AUTHORITY = predecessor.validate_authority
_ORIGINAL_LOAD = predecessor.load
_ORIGINAL_FRESH_STATE = current_base.fresh_state


def configure_runtime(**kwargs: Any) -> None:
    _ORIGINAL_CONFIGURE(**kwargs)
    current_base.runtime.coordination.rolling_quiet_gate = (
        diagnostic_gate.rolling_quiet_gate
    )


def validate_authority(
    authority_path: Path, predecessor_path: Path, scene: str,
):
    authority = _ORIGINAL_LOAD(authority_path)
    gate = authority.get("dynamic_noninterference_gate") or {}
    if (
        gate.get("schema_version")
        != "mysql_external_workload_diagnostic_gate_v1"
        or gate.get("status") != "PASS"
        or gate.get("external_workload_gate_enforced") is not False
        or gate.get("external_processes_diagnostic_only") is not True
        or gate.get("external_process_control_or_stop_performed") is not False
        or float(gate.get("actual_wait_seconds") or 0.0) != 0.0
    ):
        raise RuntimeError(f"{scene} external-diagnostic transition differs")

    resolved_authority = authority_path.resolve()

    def compatibility_load(path: Path):
        value = _ORIGINAL_LOAD(path)
        if path.resolve() == resolved_authority:
            value = copy.deepcopy(value)
            value["dynamic_noninterference_gate"]["observed_seconds"] = 60.0
        return value

    predecessor.load = compatibility_load
    try:
        freeze, workers, context = _ORIGINAL_VALIDATE_AUTHORITY(
            authority_path, predecessor_path, scene,
        )
    finally:
        predecessor.load = _ORIGINAL_LOAD
    context = copy.deepcopy(context)
    context["worker_cohort_transition"] = copy.deepcopy(authority)
    context["external_workload_policy"] = {
        "external_workloads_diagnostic_only": True,
        "external_workload_wait_required": False,
        "external_process_control_or_stop_prohibited": True,
        "mysql_fixed_cpu_affinity_retained": True,
        "numa_exclusive_scheduler_lock_required": False,
    }
    return freeze, workers, context


def fresh_state(**kwargs: Any):
    state = copy.deepcopy(_ORIGINAL_FRESH_STATE(**kwargs))
    state["external_workload_policy"] = {
        "external_workloads_diagnostic_only": True,
        "external_workload_wait_required": False,
        "external_process_control_or_stop_prohibited": True,
        "mysql_fixed_cpu_affinity_retained": True,
        "numa_exclusive_scheduler_lock_required": False,
        "physical_replay_parallelism_after_memory_completion": 4,
    }
    return state


def main() -> int:
    predecessor.base = current_base
    predecessor.configure_runtime = configure_runtime
    predecessor.validate_authority = validate_authority
    current_base.fresh_state = fresh_state
    current_base.runtime.coordination.rolling_quiet_gate = (
        diagnostic_gate.rolling_quiet_gate
    )
    return predecessor.main()


if __name__ == "__main__":
    raise SystemExit(main())
