#!/usr/bin/env python3
"""Monitor v100 TPC-H and trigger the v99 JOB two-to-four handoff."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import monitor_mysql_algorithm_a_tpch_to_job4_cache_v1 as predecessor


HISTORY = ROOT / "history/mysql_four_scene_20260809"
predecessor.TPCH_STATE = (
    HISTORY
    / "v100_to_a_tpch_external_diagnostic_resume_20260823_attempt001_tpch"
    / "algorithm_a_state.json"
)
predecessor.JOB_STATE = (
    HISTORY
    / "v99_to_a_job_external_diagnostic_hard_timeout_resume_20260823_attempt001_job"
    / "algorithm_a_state.json"
)
predecessor.EXECUTOR = (
    ROOT
    / "scripts/execute_mysql_algorithm_a_job_external_diagnostic_slot_transition_v1.py"
)


if __name__ == "__main__":
    raise SystemExit(predecessor.main())
