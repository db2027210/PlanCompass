#!/usr/bin/env python3
"""Monitor the all-scene-parallel TPC-H run and trigger JOB 2-to-4."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import monitor_mysql_algorithm_a_tpch_to_job4_cache_v1 as legacy


HISTORY = ROOT / "history/mysql_four_scene_20260809"
legacy.TPCH_STATE = (
    HISTORY / "v63_to_a_all_scene_parallel_20260823_attempt001_tpch"
    / "algorithm_a_state.json"
)
legacy.JOB_STATE = (
    HISTORY / "v63_to_a_all_scene_parallel_20260823_attempt001_job"
    / "algorithm_a_state.json"
)
legacy.EXECUTOR = (
    ROOT
    / "scripts/execute_mysql_algorithm_a_job_all_scene_parallel_slot_transition_v1.py"
)


if __name__ == "__main__":
    raise SystemExit(legacy.main())
