#!/usr/bin/env python3
"""Refresh mixed XP authority with the nonblocking host-process gate v73."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import execute_mysql_algorithm_c_xp4_scene_handoff_v1 as handoff
from scripts import mysql_cross_project_wave_coordination_v17 as gate_v17
from scripts import prepare_mysql_xp4_live_authority_v1 as authority_base
from scripts import refresh_mysql_xp4_live_authority_from_reset_v1 as predecessor
from scripts import reset_mysql_xp_data_planes_mixed_four_worker_v2 as reset_v2


authority_base.FREEZE = ROOT / "config/formal/frozen_mysql_four_scene_mixed_disk_four_worker_20260827_v4/four_scene_effective_resource_successor.json"
handoff.dynamic_gate = gate_v17
predecessor.reset4 = reset_v2


if __name__ == "__main__":
    raise SystemExit(predecessor.main())
