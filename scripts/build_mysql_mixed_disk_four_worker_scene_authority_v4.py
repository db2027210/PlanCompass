#!/usr/bin/env python3
"""Build mixed four-worker authority using the running-inventory v72 gate."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_mixed_disk_four_worker_scene_authority_v1 as predecessor
from scripts import mysql_cross_project_wave_coordination_v16 as gate_v16


predecessor.FREEZE = ROOT / "config/formal/frozen_mysql_four_scene_mixed_disk_four_worker_20260827_v4/four_scene_effective_resource_successor.json"
predecessor.FREEZE_SCHEMA = "mysql_four_scene_mixed_disk_four_worker_successor_v4"
predecessor.gate_v14 = gate_v16


if __name__ == "__main__":
    raise SystemExit(predecessor.main())
