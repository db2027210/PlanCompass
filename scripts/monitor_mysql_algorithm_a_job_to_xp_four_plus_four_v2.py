#!/usr/bin/env python3
"""Monitor successor memory states and launch the frozen XP 4+4 transition."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import monitor_mysql_algorithm_a_job_to_xp_four_plus_four_v1 as current


HISTORY = ROOT / "history/mysql_four_scene_20260809"
current.EXECUTOR = (
    ROOT / "scripts/execute_mysql_algorithm_a_job_to_xp_four_plus_four_fresh_v2.py"
)
current.EXECUTOR_UNIT = "mysql-alga-xp-four-plus-four-transition-v2.service"
current.TPCH_STATE = (
    HISTORY / "v58_to_a_memory_nvme_serialized_20260823_attempt001_tpch"
    / "algorithm_a_state.json"
)
current.JOB_STATES = (
    HISTORY / "v59_to_a_storage_compatible_job4_20260823_attempt001_job"
    / "algorithm_a_state.json",
    HISTORY / "v58_to_a_memory_nvme_serialized_20260823_attempt001_job"
    / "algorithm_a_state.json",
)


if __name__ == "__main__":
    raise SystemExit(current.main())
