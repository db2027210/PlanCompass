#!/usr/bin/env python3
"""Bind XP4 executor to the corrected v82/v83 recovery campaign."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import execute_mysql_algorithm_a_all_scene_to_xp_scene_v5 as predecessor


predecessor.CONFIG = ROOT / "config/formal/frozen_mysql_algorithm_a_all_scene_to_xp4_20260823_v6"
predecessor.RUN_IDS = {
    "hybench_xp_cpu": "v85_to_a_xp_cpu4_external_sssp_recovery_v2_20260823_attempt001_xp_cpu",
    "hybench_xp_io": "v86_to_a_xp_io4_external_sssp_recovery_v2_20260823_attempt001_xp_io",
}
predecessor.UNITS = {
    "hybench_xp_cpu": "mysql-alga-all8-v85-xp-cpu4.service",
    "hybench_xp_io": "mysql-alga-all8-v86-xp-io4.service",
}


if __name__ == "__main__":
    raise SystemExit(predecessor.main())
