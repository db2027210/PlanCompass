#!/usr/bin/env python3
"""Release completed memory slots to XP, watching the v145 CPU continuation."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import monitor_mysql_algorithm_a_all_scene_to_xp4_v5 as monitor


HISTORY = ROOT / "history/mysql_four_scene_20260809"
LOG_ROOT = HISTORY / "formal_launch_logs"
CONFIG = ROOT / "config/formal/frozen_mysql_algorithm_a_external_diagnostic_to_xp4_20260824_v10"
JOB_CONFIG = ROOT / "config/formal/frozen_mysql_algorithm_a_job_external_diagnostic_slots_20260823_v1"
JOB_RUN = "v139_to_a_job2_canonical_resume_20260824_attempt001_job"

monitor.CONTRACT_DIR = CONFIG
monitor.TPCH_STATE = HISTORY / "v100_to_a_tpch_external_diagnostic_resume_20260823_attempt001_tpch/algorithm_a_state.json"
monitor.JOB_RUN = JOB_RUN
monitor.JOB_STATE = HISTORY / JOB_RUN / "algorithm_a_state.json"
monitor.SOURCE_FREEZE = JOB_CONFIG / "job_four_worker_resource_successor.json"
monitor.SUCCESSOR_FREEZE = CONFIG / "xp_four_worker_resource_successor.json"
monitor.TRIGGER_PATH = LOG_ROOT / "v145_to_a_external_diagnostic_xp4_20260824.trigger.json"
monitor.EXECUTOR = ROOT / "scripts/execute_mysql_algorithm_a_external_diagnostic_to_xp4_v7.py"


def main() -> int:
    base = monitor.predecessor
    base.JOB = {
        "scene": "job_memory",
        "state": str(monitor.JOB_STATE),
        "unit": "mysql-alga-v139-job2-canonical-resume.service",
        "output": str(HISTORY / JOB_RUN),
    }
    base.XP = {
        "hybench_xp_cpu": {
            "scene": "hybench_xp_cpu",
            "state": str(HISTORY / "v145_to_a_xp_cpu_w2_reset_external_diagnostic_resume_20260824_attempt001_xp_cpu/algorithm_a_state.json"),
            "unit": "mysql-alga-v145-xp-cpu-w2-reset-recovery-v2.service",
            "output": str(HISTORY / "v145_to_a_xp_cpu_w2_reset_external_diagnostic_resume_20260824_attempt001_xp_cpu"),
            "result": str(LOG_ROOT / "v145_to_a_external_diagnostic_xp4_20260824.cpu_handoff.json"),
        },
        "hybench_xp_io": {
            "scene": "hybench_xp_io",
            "state": str(HISTORY / "v107_to_a_xp_io_w1_reset_external_diagnostic_resume_20260824_attempt001_xp_io/algorithm_a_state.json"),
            "unit": "mysql-alga-v107-xp-io-w1-reset-recovery-v1.service",
            "output": str(HISTORY / "v107_to_a_xp_io_w1_reset_external_diagnostic_resume_20260824_attempt001_xp_io"),
            "result": str(LOG_ROOT / "v145_to_a_external_diagnostic_xp4_20260824.io_handoff.json"),
        },
    }
    base.CONTRACT_PATH = CONFIG / "transition_contract.json"
    base.SOURCE_FREEZE = monitor.SOURCE_FREEZE
    base.SUCCESSOR_FREEZE = monitor.SUCCESSOR_FREEZE
    base.TRIGGER_PATH = monitor.TRIGGER_PATH
    base.EXECUTOR = monitor.EXECUTOR
    base.job_complete = monitor.job_complete
    base.seal_trigger = monitor.seal_trigger
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
