#!/usr/bin/env python3
"""Release completed v103 JOB and v100 TPC-H slots to active XP runs."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import monitor_mysql_algorithm_a_all_scene_to_xp4_v5 as predecessor


HISTORY = ROOT / "history/mysql_four_scene_20260809"
LOG_ROOT = HISTORY / "formal_launch_logs"
CONFIG = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_a_external_diagnostic_to_xp4_20260824_v2"
)
JOB_CONFIG = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_a_job_external_diagnostic_slots_20260823_v1"
)
JOB_RUN = "v103_to_a_job4_external_diagnostic_resume_20260823_attempt001_job"


predecessor.CONTRACT_DIR = CONFIG
predecessor.TPCH_STATE = (
    HISTORY
    / "v100_to_a_tpch_external_diagnostic_resume_20260823_attempt001_tpch"
    / "algorithm_a_state.json"
)
predecessor.JOB_RUN = JOB_RUN
predecessor.JOB_STATE = HISTORY / JOB_RUN / "algorithm_a_state.json"
predecessor.SOURCE_FREEZE = JOB_CONFIG / "job_four_worker_resource_successor.json"
predecessor.SUCCESSOR_FREEZE = CONFIG / "xp_four_worker_resource_successor.json"
predecessor.TRIGGER_PATH = (
    LOG_ROOT / "v103_to_a_external_diagnostic_xp4_20260823.trigger.json"
)
predecessor.EXECUTOR = (
    ROOT / "scripts/execute_mysql_algorithm_a_external_diagnostic_to_xp4_v1.py"
)


def main() -> int:
    base = predecessor.predecessor
    base.JOB = {
        "scene": "job_memory",
        "state": str(predecessor.JOB_STATE),
        "unit": "mysql-alga-dynamic-v103_to_a_job4_external_diagnostic_resume_20260823_attempt001_job.service",
        "output": str(HISTORY / JOB_RUN),
    }
    base.XP = {
        "hybench_xp_cpu": {
            "scene": "hybench_xp_cpu",
            "state": str(
                HISTORY
                / "v101_to_a_xp_cpu_external_diagnostic_resume_20260823_attempt001_xp_cpu"
                / "algorithm_a_state.json"
            ),
            "unit": "mysql-alga-all8-v101-xp-cpu-external-diagnostic-v1.service",
            "output": str(
                HISTORY
                / "v101_to_a_xp_cpu_external_diagnostic_resume_20260823_attempt001_xp_cpu"
            ),
            "result": str(
                LOG_ROOT / "v103_to_a_external_diagnostic_xp4_20260823.cpu_handoff.json"
            ),
        },
        "hybench_xp_io": {
            "scene": "hybench_xp_io",
            "state": str(
                HISTORY
                / "v107_to_a_xp_io_w1_reset_external_diagnostic_resume_20260824_attempt001_xp_io"
                / "algorithm_a_state.json"
            ),
            "unit": "mysql-alga-v107-xp-io-w1-reset-recovery-v1.service",
            "output": str(
                HISTORY
                / "v107_to_a_xp_io_w1_reset_external_diagnostic_resume_20260824_attempt001_xp_io"
            ),
            "result": str(
                LOG_ROOT / "v103_to_a_external_diagnostic_xp4_20260823.io_handoff.json"
            ),
        },
    }
    base.CONTRACT_PATH = CONFIG / "transition_contract.json"
    base.SOURCE_FREEZE = predecessor.SOURCE_FREEZE
    base.SUCCESSOR_FREEZE = predecessor.SUCCESSOR_FREEZE
    base.TRIGGER_PATH = predecessor.TRIGGER_PATH
    base.EXECUTOR = predecessor.EXECUTOR
    base.job_complete = predecessor.job_complete
    base.seal_trigger = predecessor.seal_trigger
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
