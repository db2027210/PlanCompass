#!/usr/bin/env python3
"""Rebind all live v18 workers after a passing formal preflight."""

from pathlib import Path
import os
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import rebind_mysql_four_scene_live_v8 as implementation


PREFLIGHT = os.environ.get("MYSQL_V18_PASS_PREFLIGHT")
if not PREFLIGHT:
    raise SystemExit("MYSQL_V18_PASS_PREFLIGHT is required")
implementation.FREEZE = ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260813_v18/four_scene_effective_resource_successor.json"
implementation.V11_FREEZE = ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260813_v17/four_scene_effective_resource_successor.json"
implementation.PREFLIGHT = Path(PREFLIGHT).resolve()
implementation.XP_PREPARED = ROOT / "history/mysql_four_scene_20260809/xp_reset_v18_after_incumbent_pilot_20260813T1650p0800/reset_binding.json"
implementation.FREEZE_SCHEMA = "mysql_disk2_four_scene_two_worker_effective_resource_successor_v18"
implementation.PREDECESSOR_FREEZE_SCHEMA = "mysql_disk2_four_scene_two_worker_effective_resource_successor_v17"
implementation.OUTPUT_SCHEMA = "mysql_four_scene_live_rebind_v18_formal"
implementation.AUTHORITY_VERSION = "v18_formal"
implementation.XP_ALLOWED_STATUSES = {"PASS"}
implementation.PREFLIGHT_ALLOWED_STATUSES = {"PASS"}
implementation.PREFLIGHT_FORMAL_LAUNCH_AUTHORIZED = True


if __name__ == "__main__":
    raise SystemExit(implementation.main())
