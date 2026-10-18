#!/usr/bin/env python3
"""Launch XP4 handoffs after the v117 JOB partial continuation completes."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import execute_mysql_algorithm_a_external_diagnostic_to_xp4_v1 as predecessor


predecessor.CONFIG = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_external_diagnostic_to_xp4_20260824_v6"
)
predecessor.TRANSITION_ROOT = (
    ROOT / "history/mysql_four_scene_20260809/v117_to_a_external_diagnostic_xp4_20260824_transition_v1"
)
predecessor.RUN_IDS = {
    "hybench_xp_cpu": "v118_to_a_xp_cpu4_external_diagnostic_resume_20260824_attempt001_xp_cpu",
    "hybench_xp_io": "v119_to_a_xp_io4_external_diagnostic_resume_20260824_attempt001_xp_io",
}
predecessor.UNITS = {
    "hybench_xp_cpu": "mysql-alga-all8-v118-xp-cpu4-external-diagnostic.service",
    "hybench_xp_io": "mysql-alga-all8-v119-xp-io4-external-diagnostic.service",
}


if __name__ == "__main__":
    raise SystemExit(predecessor.main())
