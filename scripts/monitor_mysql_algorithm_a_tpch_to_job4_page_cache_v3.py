#!/usr/bin/env python3
"""Trigger the v46 page-cache JOB 2-to-4 handoff after TPC-H converges."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import monitor_mysql_algorithm_a_tpch_to_job4_cache_v1 as legacy

HISTORY = ROOT / "history/mysql_four_scene_20260809"
legacy.JOB_STATE = (
    HISTORY / "v46_to_a_job_page_cache_fixed_20260823_attempt001_job"
    / "algorithm_a_state.json"
)
legacy.EXECUTOR = ROOT / "scripts/execute_mysql_algorithm_a_job_page_cache_slot_transition_v2.py"


if __name__ == "__main__":
    raise SystemExit(legacy.main())
