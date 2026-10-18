#!/usr/bin/env python3
"""Expand v99 JOB to four workers after v100 TPC-H completes."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_external_workload_diagnostic_gate_v1 as diagnostic_gate
from scripts import execute_mysql_algorithm_a_job_buffer_pool_slot_transition_v3 as current


HISTORY = ROOT / "history/mysql_four_scene_20260809"
JOB_RUN = "v99_to_a_job_external_diagnostic_hard_timeout_resume_20260823_attempt001_job"
TPCH_RUN = "v100_to_a_tpch_external_diagnostic_resume_20260823_attempt001_tpch"
SUCCESSOR_RUN = "v103_to_a_job4_external_diagnostic_resume_20260823_attempt001_job"
CONFIG = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_a_job_external_diagnostic_slots_20260823_v1"
)


current.CAMPAIGN = "v99_to_a_external_diagnostic_resume_20260823"
current.PREDECESSOR_RUN = JOB_RUN
current.SUCCESSOR_RUN = SUCCESSOR_RUN
current.legacy.CONTROLLER = (
    ROOT
    / "scripts/run_mysql_algorithm_a_job_buffer_pool_dynamic_external_diagnostic_resume_v1.py"
)
current.legacy.TRANSITION_ROOT = (
    HISTORY / "v99_to_a_external_diagnostic_resume_20260823_job_dynamic_slots_v1"
)
current.legacy.FREEZE_PATH = CONFIG / "job_four_worker_resource_successor.json"
current.legacy.CONTRACT_PATH = CONFIG / "transition_contract.json"
current.legacy.JOB_STATE = HISTORY / JOB_RUN / "algorithm_a_state.json"
current.legacy.JOB_UNIT = "mysql-alga-all8-v99-job-external-diagnostic-v1.service"
current.legacy.TPCH_STATE = HISTORY / TPCH_RUN / "algorithm_a_state.json"
current.legacy.ORIGINAL_MONITOR_UNIT = (
    "mysql-alga-all8-v100-tpch-to-job4-external-diagnostic-v1.service"
)
current.legacy.launch = current.launch


def main() -> int:
    current.c49.validate_v65_wave_coordination_contract()
    current.c49.install()
    current.legacy.coordination.predecessor.CONTRACT_PATH = (
        current.c49.COORDINATION_CONTRACT
    )
    # The user's active policy supersedes only external-process waiting.
    # Resource read-back, fixed CPU placement and donor ownership still use
    # the existing audited transition implementation.
    current.legacy.coordination.rolling_quiet_gate = (
        diagnostic_gate.rolling_quiet_gate
    )
    return current.legacy.main()


if __name__ == "__main__":
    raise SystemExit(main())
