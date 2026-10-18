#!/usr/bin/env python3
"""Expand v82 JOB after v83 TPC-H completes."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import execute_mysql_algorithm_a_job_all_scene_parallel_slot_transition_v1 as predecessor


HISTORY = ROOT / "history/mysql_four_scene_20260809"
JOB_RUN = "v82_to_a_job_external_sssp_memory_recovery_v2_20260823_attempt001_job"
TPCH_RUN = "v83_to_a_tpch_external_sssp_memory_recovery_v2_20260823_attempt001_tpch"
SUCCESSOR_RUN = "v84_to_a_job4_external_sssp_recovery_v2_20260823_attempt001_job"
CONFIG = ROOT / "config/formal/frozen_mysql_algorithm_a_job_buffer_pool_dynamic_slots_20260823_v12"

current = predecessor.current
current.previous.CAMPAIGN = "v82_to_a_external_sssp_memory_recovery_v2_20260823"
current.previous.PREDECESSOR_RUN = JOB_RUN
current.previous.SUCCESSOR_RUN = SUCCESSOR_RUN
current.previous.legacy.CONTROLLER = ROOT / "scripts/run_mysql_algorithm_a_job_buffer_pool_dynamic_resume_v3.py"
current.previous.legacy.TRANSITION_ROOT = (
    HISTORY / "v82_to_a_external_sssp_memory_recovery_v2_20260823_job_dynamic_slots_v12"
)
current.previous.legacy.FREEZE_PATH = CONFIG / "job_four_worker_resource_successor.json"
current.previous.legacy.CONTRACT_PATH = CONFIG / "transition_contract.json"
current.previous.legacy.JOB_STATE = HISTORY / JOB_RUN / "algorithm_a_state.json"
current.previous.legacy.JOB_UNIT = "mysql-alga-all8-v82-job-recovery-v2.service"
current.previous.legacy.TPCH_STATE = HISTORY / TPCH_RUN / "algorithm_a_state.json"
current.previous.legacy.ORIGINAL_MONITOR_UNIT = "mysql-alga-all8-v83-tpch-to-job4-monitor-v4.service"
current.previous.legacy.launch = current.previous.launch


if __name__ == "__main__":
    raise SystemExit(current.previous.main())
