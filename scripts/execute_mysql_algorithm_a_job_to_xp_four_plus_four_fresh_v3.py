#!/usr/bin/env python3
"""Bind fresh XP CPU4+I/O4 transition to corrected v60/v61 memory states."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import execute_mysql_algorithm_a_job_to_xp_four_plus_four_fresh_v1 as current


HISTORY = ROOT / "history/mysql_four_scene_20260809"
current.CONTRACT = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_xp_four_plus_four_20260823_v2"
    / "transition_contract.json"
)
current.SOURCE_FREEZE = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_a_job_buffer_pool_dynamic_slots_20260823_v7"
    / "job_four_worker_resource_successor.json"
)
current.SUCCESSOR_FREEZE = current.CONTRACT.parent / "xp_four_worker_resource_successor.json"
current.TPCH_STATE = (
    HISTORY / "v60_to_a_cache_adapter_rebound_20260823_attempt001_tpch"
    / "algorithm_a_state.json"
)
current.JOB_STATES = (
    HISTORY / "v61_to_a_cache_adapter_rebound_job4_20260823_attempt001_job"
    / "algorithm_a_state.json",
    HISTORY / "v60_to_a_cache_adapter_rebound_20260823_attempt001_job"
    / "algorithm_a_state.json",
)
current.TRANSITION_ROOT = HISTORY / "v62_to_a_xp_four_plus_four_20260823_transition_v2"
current.RUNS = {
    "hybench_xp_cpu": "v62_to_a_xp_four_plus_four_fresh_20260823_attempt001_xp_cpu",
    "hybench_xp_io": "v62_to_a_xp_four_plus_four_fresh_20260823_attempt001_xp_io",
}


if __name__ == "__main__":
    raise SystemExit(current.main())
