#!/usr/bin/env python3
"""Reset XP onto the v14 write-I/O envelope after two failed v13 R0 runs."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import reset_mysql_xp_data_planes_v5 as implementation


implementation.FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260811_v14"
    / "four_scene_effective_resource_successor.json"
)
implementation.PREPARED = (
    ROOT / "history/mysql_four_scene_20260809"
    / "four_scene_live_rebind_v24_20260811T121000p0800"
    / "prepared_binding.json"
)
implementation.INVALID_R0 = (
    ROOT / "history/mysql_four_scene_20260809"
    / "four_scene_native_iq1_iq7_r0_v24_20260811T121500p0800"
    / "r0_binding.json"
)
implementation.RESET_SAME_FREEZE = False
implementation.OUTPUT_SCHEMA = "mysql_four_scene_v14_xp_reprepared_after_r0_v1"
implementation.AUTHORITY_VERSION = "v14"


if __name__ == "__main__":
    raise SystemExit(implementation.main())
