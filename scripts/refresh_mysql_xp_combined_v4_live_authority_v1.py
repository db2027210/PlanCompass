#!/usr/bin/env python3
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from scripts import execute_mysql_algorithm_c_xp4_scene_handoff_v1 as handoff
from scripts import mysql_cross_project_wave_coordination_v7 as gate
from scripts import prepare_mysql_xp4_live_authority_v1 as base
from scripts import refresh_mysql_xp4_live_authority_from_reset_v1 as p
base.FREEZE = ROOT / "config/formal/frozen_mysql_xp_cpu_disjoint_slots_20260826_v4/resource_successor.json"
handoff.dynamic_gate = gate
if __name__ == "__main__": raise SystemExit(p.main())
