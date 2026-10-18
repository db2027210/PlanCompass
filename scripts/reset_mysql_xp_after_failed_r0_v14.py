#!/usr/bin/env python3
"""Reset v13 XP workers after the boundary-only failed fresh R0."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import reset_mysql_xp_data_planes_v5 as implementation


implementation.FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260811_v13"
    / "four_scene_effective_resource_successor.json"
)
implementation.PREPARED = (
    ROOT / "history/mysql_four_scene_20260809"
    / "four_scene_live_rebind_v23_20260811T114500p0800"
    / "prepared_binding.json"
)
implementation.INVALID_R0 = (
    ROOT / "history/mysql_four_scene_20260809"
    / "four_scene_native_iq1_iq7_r0_v23_20260811T115000p0800"
    / "r0_binding.json"
)
implementation.RESET_SAME_FREEZE = True
implementation.OUTPUT_SCHEMA = "mysql_four_scene_v13_xp_reprepared_after_r0_v1"
implementation.AUTHORITY_VERSION = "v13"


if __name__ == "__main__":
    raise SystemExit(implementation.main())
