#!/usr/bin/env python3
"""Bind XP4 executor to recovered v95 JOB4 authority."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import execute_mysql_algorithm_a_all_scene_to_xp_scene_v7 as predecessor


predecessor.predecessor.CONFIG = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_all_scene_to_xp4_20260823_v8"
)
predecessor.predecessor.RUN_IDS = {
    "hybench_xp_cpu": "v96_to_a_xp_cpu4_job_recovery_v4_20260823_attempt001_xp_cpu",
    "hybench_xp_io": "v97_to_a_xp_io4_job_recovery_v4_20260823_attempt001_xp_io",
}
predecessor.predecessor.UNITS = {
    "hybench_xp_cpu": "mysql-alga-all8-v96-xp-cpu4.service",
    "hybench_xp_io": "mysql-alga-all8-v97-xp-io4.service",
}


if __name__ == "__main__":
    raise SystemExit(predecessor.predecessor.main())
