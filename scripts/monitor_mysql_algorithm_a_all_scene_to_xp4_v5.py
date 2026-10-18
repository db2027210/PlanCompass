#!/usr/bin/env python3
"""Release recovered JOB/TPC-H slots to recovered XP scenes."""

from __future__ import annotations

import copy
from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import monitor_mysql_algorithm_a_job_to_xp_slots_v1 as predecessor


HISTORY = ROOT / "history/mysql_four_scene_20260809"
LOG_ROOT = HISTORY / "formal_launch_logs"
CONTRACT_DIR = ROOT / "config/formal/frozen_mysql_algorithm_a_all_scene_to_xp4_20260823_v5"
TPCH_STATE = (
    HISTORY / "v76_to_a_tpch_external_sssp_memory_recovery_20260823_attempt001_tpch"
    / "algorithm_a_state.json"
)
JOB_RUN = "v79_to_a_job4_external_sssp_recovery_20260823_attempt001_job"
JOB_STATE = HISTORY / JOB_RUN / "algorithm_a_state.json"
SOURCE_FREEZE = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_job_buffer_pool_dynamic_slots_20260823_v11"
    / "job_four_worker_resource_successor.json"
)
SUCCESSOR_FREEZE = CONTRACT_DIR / "xp_four_worker_resource_successor.json"
TRIGGER_PATH = LOG_ROOT / "v79_to_a_all_scene_parallel_xp4_20260823.v5.trigger.json"
EXECUTOR = ROOT / "scripts/execute_mysql_algorithm_a_all_scene_to_xp_scene_v5.py"
_ORIGINAL_JOB_COMPLETE = predecessor.job_complete
_ORIGINAL_SEAL_TRIGGER = predecessor.seal_trigger


def strict_complete(state: Mapping[str, Any], scene: str) -> bool:
    return bool(
        state.get("algorithm") == "A"
        and state.get("async_scene") == scene
        and state.get("formal_scope") == "mysql_only"
        and state.get("cross_engine_comparison_prohibited") is True
        and state.get("status") == "COMPLETE"
        and state.get("stage") == "COMPLETE"
        and state.get("converged") is True
        and int(state.get("accepted") or 0) == 10
        and state.get("round_checkpoint") is None
    )


def tpch_evidence() -> dict[str, Any]:
    state = predecessor.load(TPCH_STATE)
    if not strict_complete(state, "tpch_sf4_memory"):
        raise RuntimeError("recovered TPC-H slots are not formally complete")
    return {
        "complete": True, "accepted": 10, "state_path": str(TPCH_STATE),
        "state_sha256": predecessor.sha(TPCH_STATE), "run_id": state.get("run_id"),
        "physical_replay_parallelism": int(state.get("physical_replay_parallelism") or 0),
    }


def job_complete(state: Mapping[str, Any]) -> bool:
    if not _ORIGINAL_JOB_COMPLETE(state):
        return False
    try:
        tpch_evidence()
    except RuntimeError:
        return False
    return True


def seal_trigger(job_state: Mapping[str, Any]) -> dict[str, Any]:
    trigger = _ORIGINAL_SEAL_TRIGGER(job_state)
    tpc = tpch_evidence()
    successor = predecessor.load(SUCCESSOR_FREEZE)
    successor["completed_memory_scene_evidence"] = {
        "job_memory": copy.deepcopy(trigger["job_terminal_evidence"]),
        "tpch_sf4_memory": copy.deepcopy(tpc),
    }
    successor["numa_exclusive_scheduler_lock_required"] = False
    successor["fixed_disjoint_cpu_affinity_required"] = True
    predecessor.atomic(SUCCESSOR_FREEZE, successor)
    trigger["tpch_terminal_evidence"] = copy.deepcopy(tpc)
    trigger["both_memory_scenes_complete_required"] = True
    trigger["successor_freeze_sha256"] = predecessor.sha(SUCCESSOR_FREEZE)
    trigger["numa_exclusive_scheduler_lock_required"] = False
    trigger["fixed_disjoint_cpu_affinity_required"] = True
    predecessor.atomic(TRIGGER_PATH, trigger)
    return trigger


def main() -> int:
    predecessor.CONTRACT_PATH = CONTRACT_DIR / "transition_contract.json"
    predecessor.SOURCE_FREEZE = SOURCE_FREEZE
    predecessor.SUCCESSOR_FREEZE = SUCCESSOR_FREEZE
    predecessor.TRIGGER_PATH = TRIGGER_PATH
    predecessor.EXECUTOR = EXECUTOR
    predecessor.JOB = {
        "scene": "job_memory", "state": str(JOB_STATE),
        "unit": f"mysql-alga-dynamic-{JOB_RUN}.service", "output": str(HISTORY / JOB_RUN),
    }
    predecessor.XP = {
        "hybench_xp_cpu": {
            "scene": "hybench_xp_cpu",
            "state": str(HISTORY / "v77_to_a_xp_cpu_external_sssp_resume_20260823_attempt001_xp_cpu" / "algorithm_a_state.json"),
            "unit": "mysql-alga-all8-v77-xp-cpu-recovery.service",
            "output": str(HISTORY / "v77_to_a_xp_cpu_external_sssp_resume_20260823_attempt001_xp_cpu"),
            "result": str(LOG_ROOT / "v79_to_a_all_scene_parallel_xp4_20260823.v5.cpu_handoff.json"),
        },
        "hybench_xp_io": {
            "scene": "hybench_xp_io",
            "state": str(HISTORY / "v78_to_a_xp_io_external_sssp_resume_20260823_attempt001_xp_io" / "algorithm_a_state.json"),
            "unit": "mysql-alga-all8-v78-xp-io-recovery.service",
            "output": str(HISTORY / "v78_to_a_xp_io_external_sssp_resume_20260823_attempt001_xp_io"),
            "result": str(LOG_ROOT / "v79_to_a_all_scene_parallel_xp4_20260823.v5.io_handoff.json"),
        },
    }
    predecessor.job_complete = job_complete
    predecessor.seal_trigger = seal_trigger
    return predecessor.main()


if __name__ == "__main__":
    raise SystemExit(main())
