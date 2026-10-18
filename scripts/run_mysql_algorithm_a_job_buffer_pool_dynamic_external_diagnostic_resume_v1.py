#!/usr/bin/env python3
"""Resume four-worker JOB while treating external workloads as diagnostics."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_external_workload_diagnostic_gate_v1 as diagnostic_gate
from scripts import mysql_job_buffer_pool_preconditioned_measurement_v10 as job_v10
from scripts import run_mysql_algorithm_a_job_dynamic_resume_v3 as successor


adapter = successor.adapter
current = successor.current
_ORIGINAL_CONFIGURE = adapter.configure_runtime
_ORIGINAL_VALIDATE_AUTHORITY = adapter.validate_authority
_ORIGINAL_LOAD = adapter.load
_ORIGINAL_FRESH_STATE = current.fresh_state


def configure_runtime(**kwargs: Any) -> None:
    _ORIGINAL_CONFIGURE(**kwargs)
    job_v10._validate_contract()
    current.runtime.alg.measure_batch = job_v10.measure_batch
    current.runtime.coordination.rolling_quiet_gate = (
        diagnostic_gate.rolling_quiet_gate
    )
    if current.runtime.alg.measure_batch is not job_v10.measure_batch:
        raise RuntimeError("four-worker JOB v10 measurement binding did not persist")


def validate_authority(
    authority_path: Path, predecessor_path: Path, scene: str,
):
    """Reuse all cohort checks while superseding only the old 60 s gate check."""
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
        raise RuntimeError("four-worker JOB external-diagnostic authority differs")

    resolved_authority = authority_path.resolve()

    def compatibility_load(path: Path):
        value = _ORIGINAL_LOAD(path)
        if path.resolve() == resolved_authority:
            # The predecessor validator's only superseded clause is its old
            # elapsed-60-second external gate.  Present a private compatibility
            # copy to that validator; persisted authority remains unchanged.
            value = copy.deepcopy(value)
            value["dynamic_noninterference_gate"]["observed_seconds"] = 60.0
        return value

    adapter.load = compatibility_load
    try:
        freeze, workers, context = _ORIGINAL_VALIDATE_AUTHORITY(
            authority_path, predecessor_path, scene,
        )
    finally:
        adapter.load = _ORIGINAL_LOAD
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
        "all_four_scenes_concurrent_before_slot_handoff": True,
    }
    return state


def main() -> int:
    job_v10._validate_contract()
    adapter.base = current
    adapter.configure_runtime = configure_runtime
    adapter.validate_authority = validate_authority
    current.fresh_state = fresh_state
    current.runtime.coordination.rolling_quiet_gate = diagnostic_gate.rolling_quiet_gate
    return adapter.main()


if __name__ == "__main__":
    raise SystemExit(main())
