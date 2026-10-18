#!/usr/bin/env python3
"""Launch v129-aware XP4 successors with parallel scene resets."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import execute_mysql_algorithm_a_external_diagnostic_to_xp4_v1 as executor


executor.CONFIG = ROOT / "config/formal/frozen_mysql_algorithm_a_external_diagnostic_to_xp4_20260824_v8"
executor.TRANSITION_ROOT = ROOT / "history/mysql_four_scene_20260809/v129_to_a_external_diagnostic_xp4_fast_reset_20260824_transition_v1"
executor.CONTROLLER = ROOT / "scripts/run_mysql_algorithm_a_job_to_xp_external_diagnostic_fast_reset_resume_v1.py"
executor.RUN_IDS = {
    "hybench_xp_cpu": "v136_to_a_xp_cpu4_fast_reset_resume_20260824_attempt001_xp_cpu",
    "hybench_xp_io": "v137_to_a_xp_io4_fast_reset_resume_20260824_attempt001_xp_io",
}
executor.UNITS = {
    "hybench_xp_cpu": "mysql-alga-v136-xp-cpu4-fast-reset.service",
    "hybench_xp_io": "mysql-alga-v137-xp-io4-fast-reset.service",
}


if __name__ == "__main__":
    raise SystemExit(executor.main())
