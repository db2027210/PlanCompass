#!/usr/bin/env python3
"""Resume one live XP lane on four workers with current Algorithm A semantics."""

from __future__ import annotations

import copy
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_co_resident_pg_isolation_v14 as pg_wrapper
from scripts import run_mysql_algorithm_a_async_scene_v3 as current_base
from scripts import run_mysql_algorithm_a_job_to_xp_resume_v1 as predecessor


_ORIGINAL_CONFIGURE = predecessor.configure_runtime
_ORIGINAL_FRESH_STATE = current_base.fresh_state


def configure_runtime(**kwargs):
    result = _ORIGINAL_CONFIGURE(**kwargs)
    # configure_runtime installs the base coordination evaluator.  Install the
    # bounded successor afterwards so the rolling gate keeps recognizing only
    # the exact, resource-bounded PG capture-wrapper lineage.
    pg_wrapper.install()
    return result


def fresh_state(**kwargs):
    state = copy.deepcopy(_ORIGINAL_FRESH_STATE(**kwargs))
    state["all_scene_parallel_execution"] = {
        "concurrent_scenes_before_memory_completion": [
            "job_memory", "tpch_sf4_memory",
            "hybench_xp_cpu", "hybench_xp_io",
        ],
        "initial_physical_replay_parallelism_per_scene": 2,
        "physical_replay_parallelism_after_memory_completion": 4,
        "four_scene_wave_barrier": False,
        "scene_may_advance_immediately": True,
        "fixed_disjoint_cpu_affinity_required": True,
        "numa_exclusive_scheduler_lock_required": False,
        "effective_numa_and_memory_values_read_back": True,
        "postgresql_process_control_or_stop_prohibited": True,
    }
    state["pg_capture_wrapper_allowance"] = {
        "contract_path": str(pg_wrapper.CONTRACT),
        "contract_sha256": pg_wrapper.sha(pg_wrapper.CONTRACT),
        "rolling_dynamic_noninterference_gate_retained": True,
        "postgresql_process_control_or_stop_prohibited": True,
    }
    return state


def main() -> int:
    pg_wrapper.load_contract()
    predecessor.base = current_base
    predecessor.configure_runtime = configure_runtime
    current_base.fresh_state = fresh_state
    return predecessor.main()


if __name__ == "__main__":
    raise SystemExit(main())
