#!/usr/bin/env python3
"""Bind memory-to-XP4 monitor to recovered v82/v90 states."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import monitor_mysql_algorithm_a_all_scene_to_xp4_v5 as predecessor


HISTORY = ROOT / "history/mysql_four_scene_20260809"
LOG_ROOT = HISTORY / "formal_launch_logs"
predecessor.CONTRACT_DIR = ROOT / "config/formal/frozen_mysql_algorithm_a_all_scene_to_xp4_20260823_v7"
predecessor.TPCH_STATE = HISTORY / "v90_to_a_tpch_completed_recovery_evidence_resume_20260823_attempt001_tpch" / "algorithm_a_state.json"
predecessor.JOB_RUN = "v91_to_a_job4_external_sssp_recovery_v3_20260823_attempt001_job"
predecessor.JOB_STATE = HISTORY / predecessor.JOB_RUN / "algorithm_a_state.json"
predecessor.SOURCE_FREEZE = ROOT / "config/formal/frozen_mysql_algorithm_a_job_buffer_pool_dynamic_slots_20260823_v13" / "job_four_worker_resource_successor.json"
predecessor.SUCCESSOR_FREEZE = predecessor.CONTRACT_DIR / "xp_four_worker_resource_successor.json"
predecessor.TRIGGER_PATH = LOG_ROOT / "v91_to_a_all_scene_parallel_xp4_20260823.v7.trigger.json"
predecessor.EXECUTOR = ROOT / "scripts/execute_mysql_algorithm_a_all_scene_to_xp_scene_v7.py"


if __name__ == "__main__":
    raise SystemExit(predecessor.main())
