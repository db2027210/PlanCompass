#!/usr/bin/env python3
"""Bind the JOB 2-to-4 transition to the storage-compatible v54 campaign."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import execute_mysql_algorithm_a_job_buffer_pool_slot_transition_v3 as previous

HISTORY = ROOT / "history/mysql_four_scene_20260809"
previous.CAMPAIGN = "v54_to_a_storage_compatible_20260823"
previous.PREDECESSOR_RUN = "v54_to_a_storage_compatible_20260823_attempt001_job"
previous.SUCCESSOR_RUN = "v55_to_a_storage_compatible_job4_20260823_attempt001_job"
previous.legacy.CONTROLLER = ROOT / "scripts/run_mysql_algorithm_a_job_buffer_pool_dynamic_resume_v2.py"
previous.legacy.TRANSITION_ROOT = HISTORY / f"{previous.CAMPAIGN}_job_dynamic_slots_v6"
previous.legacy.FREEZE_PATH = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_job_buffer_pool_dynamic_slots_20260823_v6"
    / "job_four_worker_resource_successor.json"
)
previous.legacy.CONTRACT_PATH = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_job_buffer_pool_dynamic_slots_20260823_v6"
    / "transition_contract.json"
)
previous.legacy.JOB_STATE = HISTORY / previous.PREDECESSOR_RUN / "algorithm_a_state.json"
previous.legacy.JOB_UNIT = f"mysql-alga-cachefix-{previous.PREDECESSOR_RUN}.service"
previous.legacy.TPCH_STATE = (
    HISTORY / "v54_to_a_storage_compatible_20260823_attempt001_tpch"
    / "algorithm_a_state.json"
)
previous.legacy.ORIGINAL_MONITOR_UNIT = (
    "mysql-alga-storage-v54-tpch-to-job4-monitor-v6.service"
)
previous.legacy.launch = previous.launch


if __name__ == "__main__":
    raise SystemExit(previous.main())
