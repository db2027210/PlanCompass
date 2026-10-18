#!/usr/bin/env python3
"""Rebind all live workers to the v13 freeze after fresh XP recovery."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import rebind_mysql_four_scene_live_v8 as implementation


implementation.FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260811_v13"
    / "four_scene_effective_resource_successor.json"
)
implementation.V11_FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260810_v12"
    / "four_scene_effective_resource_successor.json"
)
implementation.PREFLIGHT = (
    ROOT / "preflight/mysql_four_scene_20260809/runtime_preflight_v24.json"
)
implementation.XP_PREPARED = (
    ROOT / "history/mysql_four_scene_20260809"
    / "xp_restore_after_failed_formal_v24_retry6_fresh_20260811T113000p0800"
    / "prepared_binding.json"
)
implementation.FREEZE_SCHEMA = (
    "mysql_disk2_four_scene_two_worker_effective_resource_successor_v13"
)
implementation.PREDECESSOR_FREEZE_SCHEMA = (
    "mysql_disk2_four_scene_two_worker_effective_resource_successor_v12"
)
implementation.OUTPUT_SCHEMA = "mysql_four_scene_live_rebind_v13"
implementation.AUTHORITY_VERSION = "v13"


if __name__ == "__main__":
    raise SystemExit(implementation.main())
