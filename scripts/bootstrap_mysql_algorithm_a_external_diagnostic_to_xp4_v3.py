#!/usr/bin/env python3
"""Bind the final JOB4 freeze, then monitor memory-to-XP4 completion."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import bootstrap_mysql_algorithm_a_all_scene_to_xp4_monitor_v1 as predecessor


CONFIG = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_a_external_diagnostic_to_xp4_20260824_v3"
)
JOB_CONFIG = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_a_job_external_diagnostic_slots_20260823_v1"
)
MONITOR = ROOT / "scripts/monitor_mysql_algorithm_a_external_diagnostic_to_xp4_v1.py"
JOB_STATE = (
    ROOT
    / "history/mysql_four_scene_20260809"
    / "v103_to_a_job4_external_diagnostic_resume_20260823_attempt001_job"
    / "algorithm_a_state.json"
)
_ORIGINAL_BUILD = predecessor.build_transition_contract


def build_transition_contract(bootstrap: Mapping[str, Any]) -> dict[str, Any]:
    job_state = predecessor.load(JOB_STATE)
    if not (
        job_state.get("algorithm") == "A"
        and job_state.get("async_scene") == "job_memory"
        and job_state.get("status") in {"RUNNING", "COMPLETE"}
        and int(job_state.get("physical_replay_parallelism") or 0) == 4
        and int(job_state.get("accepted") or 0) >= 2
    ):
        raise RuntimeError("v103 four-worker JOB state is not ready")
    value = _ORIGINAL_BUILD(bootstrap)
    value["release_version"] = "external_diagnostic_xp4_v3"
    value["current_tpch_v100_required"] = True
    value["job4_v103_authority_required"] = True
    value["xp_io_current_run"] = (
        "v107_to_a_xp_io_w1_reset_external_diagnostic_resume_"
        "20260824_attempt001_xp_io"
    )
    value["xp_io_w1_reset_recovery_contract_required"] = True
    value["management_controller_cpuset"] = "240-241,496-497"
    value["management_controller_taskset_required"] = True
    value["external_workloads_diagnostic_only"] = True
    value["external_workload_wait_required"] = False
    value["external_process_control_or_stop_prohibited"] = True
    value["rolling_dynamic_noninterference_gate_retained"] = False
    value["mysql_fixed_cpu_affinity_retained"] = True
    value["numa_exclusive_scheduler_lock_required"] = False
    value["job4_state_observed_at_bootstrap"] = {
        "path": str(JOB_STATE.relative_to(ROOT)),
        "status": job_state.get("status"),
        "accepted": int(job_state.get("accepted") or 0),
        "physical_replay_parallelism": 4,
    }
    return value


def main() -> int:
    predecessor.CONFIG = CONFIG
    predecessor.BOOTSTRAP_CONTRACT = CONFIG / "bootstrap_contract.json"
    predecessor.TRANSITION_CONTRACT = CONFIG / "transition_contract.json"
    predecessor.SOURCE_FREEZE = JOB_CONFIG / "job_four_worker_resource_successor.json"
    predecessor.UPSTREAM_CONTRACT = JOB_CONFIG / "transition_contract.json"
    predecessor.MONITOR = MONITOR
    predecessor.build_transition_contract = build_transition_contract
    return predecessor.main()


if __name__ == "__main__":
    raise SystemExit(main())
