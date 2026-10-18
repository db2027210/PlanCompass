#!/usr/bin/env python3
"""Bind JOB 2-to-4 handoff to the memory-NVMe serialized successor."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import execute_mysql_algorithm_a_job_buffer_pool_slot_transition_v5 as current


HISTORY = ROOT / "history/mysql_four_scene_20260809"
JOB_RUN = "v58_to_a_memory_nvme_serialized_20260823_attempt001_job"
TPCH_RUN = "v58_to_a_memory_nvme_serialized_20260823_attempt001_tpch"
SUCCESSOR_RUN = "v59_to_a_storage_compatible_job4_20260823_attempt001_job"

current.previous.CAMPAIGN = "v58_to_a_memory_nvme_serialized_20260823"
current.previous.PREDECESSOR_RUN = JOB_RUN
current.previous.SUCCESSOR_RUN = SUCCESSOR_RUN
current.previous.legacy.TRANSITION_ROOT = (
    HISTORY / "v58_to_a_memory_nvme_serialized_20260823_job_dynamic_slots_v6"
)
current.previous.legacy.JOB_STATE = HISTORY / JOB_RUN / "algorithm_a_state.json"
current.previous.legacy.JOB_UNIT = (
    "mysql-alga-memory-nvme-v58-job.service"
)
current.previous.legacy.TPCH_STATE = HISTORY / TPCH_RUN / "algorithm_a_state.json"
current.previous.legacy.ORIGINAL_MONITOR_UNIT = (
    "mysql-alga-memory-nvme-v58-tpch-to-job4-monitor-v7.service"
)
current.previous.legacy.launch = current.previous.launch


if __name__ == "__main__":
    raise SystemExit(current.previous.main())
