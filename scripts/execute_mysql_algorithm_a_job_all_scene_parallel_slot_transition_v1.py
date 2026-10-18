#!/usr/bin/env python3
"""Expand the all-scene-parallel JOB from two to four workers after TPC-H."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import execute_mysql_algorithm_a_job_buffer_pool_slot_transition_v5 as current


HISTORY = ROOT / "history/mysql_four_scene_20260809"
JOB_RUN = "v63_to_a_all_scene_parallel_20260823_attempt001_job"
TPCH_RUN = "v63_to_a_all_scene_parallel_20260823_attempt001_tpch"
SUCCESSOR_RUN = "v64_to_a_all_scene_parallel_job4_20260823_attempt001_job"

current.previous.CAMPAIGN = "v63_to_a_all_scene_parallel_20260823"
current.previous.PREDECESSOR_RUN = JOB_RUN
current.previous.SUCCESSOR_RUN = SUCCESSOR_RUN
current.previous.legacy.CONTROLLER = (
    ROOT / "scripts/run_mysql_algorithm_a_job_buffer_pool_dynamic_resume_v3.py"
)
current.previous.legacy.TRANSITION_ROOT = (
    HISTORY / "v63_to_a_all_scene_parallel_20260823_job_dynamic_slots_v8"
)
current.previous.legacy.FREEZE_PATH = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_a_job_buffer_pool_dynamic_slots_20260823_v8"
    / "job_four_worker_resource_successor.json"
)
current.previous.legacy.CONTRACT_PATH = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_a_job_buffer_pool_dynamic_slots_20260823_v8"
    / "transition_contract.json"
)
current.previous.legacy.JOB_STATE = HISTORY / JOB_RUN / "algorithm_a_state.json"
current.previous.legacy.JOB_UNIT = "mysql-alga-all8-v63-job.service"
current.previous.legacy.TPCH_STATE = HISTORY / TPCH_RUN / "algorithm_a_state.json"
current.previous.legacy.ORIGINAL_MONITOR_UNIT = (
    "mysql-alga-all8-v63-tpch-to-job4-monitor.service"
)
current.previous.legacy.launch = current.previous.launch


if __name__ == "__main__":
    raise SystemExit(current.previous.main())
