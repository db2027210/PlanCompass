#!/usr/bin/env python3
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from scripts import capture_mysql_xp_io_combined_freeze_transition_v1 as p
p.FREEZE = ROOT / "config/formal/frozen_mysql_xp_cpu_disjoint_slots_20260826_v4/resource_successor.json"
p.SOURCE = ROOT / "history/mysql_four_scene_20260809/v216_to_xp_io_combined_freeze_transition_20260826/reset_binding.json"
p.OUTPUT = ROOT / "history/mysql_four_scene_20260809/v222_to_xp_io_combined_v4_transition_20260826/reset_binding.json"
if __name__ == "__main__": raise SystemExit(p.main())
