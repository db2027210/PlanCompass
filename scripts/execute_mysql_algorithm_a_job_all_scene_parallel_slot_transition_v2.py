#!/usr/bin/env python3
"""Expand JOB after the partial-round-preserving TPC-H successor completes."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import execute_mysql_algorithm_a_job_all_scene_parallel_slot_transition_v1 as predecessor


HISTORY = ROOT / "history/mysql_four_scene_20260809"
current = predecessor.current
current.previous.legacy.TPCH_STATE = (
    HISTORY / "v68_to_a_tpch_pg_wrapper_partial_resume_20260823_attempt001_tpch"
    / "algorithm_a_state.json"
)
current.previous.legacy.FREEZE_PATH = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_a_job_buffer_pool_dynamic_slots_20260823_v9"
    / "job_four_worker_resource_successor.json"
)
current.previous.legacy.CONTRACT_PATH = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_a_job_buffer_pool_dynamic_slots_20260823_v9"
    / "transition_contract.json"
)
current.previous.legacy.ORIGINAL_MONITOR_UNIT = (
    "mysql-alga-all8-v68-tpch-to-job4-monitor.service"
)


if __name__ == "__main__":
    raise SystemExit(current.previous.main())
