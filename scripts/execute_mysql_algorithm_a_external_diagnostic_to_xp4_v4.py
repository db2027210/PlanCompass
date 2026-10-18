#!/usr/bin/env python3
"""Launch XP4 handoffs after the v129 JOB recovery completes."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import execute_mysql_algorithm_a_external_diagnostic_to_xp4_v3 as prior

prior.predecessor.CONFIG = ROOT / "config/formal/frozen_mysql_algorithm_a_external_diagnostic_to_xp4_20260824_v7"
prior.predecessor.TRANSITION_ROOT = ROOT / "history/mysql_four_scene_20260809/v129_to_a_external_diagnostic_xp4_20260824_transition_v1"
prior.predecessor.RUN_IDS = {
    "hybench_xp_cpu": "v123_to_a_xp_cpu4_external_diagnostic_resume_20260824_attempt001_xp_cpu",
    "hybench_xp_io": "v124_to_a_xp_io4_external_diagnostic_resume_20260824_attempt001_xp_io",
}
prior.predecessor.UNITS = {
    "hybench_xp_cpu": "mysql-alga-all8-v123-xp-cpu4-external-diagnostic.service",
    "hybench_xp_io": "mysql-alga-all8-v124-xp-io4-external-diagnostic.service",
}

if __name__ == "__main__":
    raise SystemExit(prior.predecessor.main())
