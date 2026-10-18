#!/usr/bin/env python3
"""Rebind v18 live workers for a zero-credit interference-override pilot."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import rebind_mysql_four_scene_live_v8 as implementation


implementation.FREEZE = ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260813_v18/four_scene_effective_resource_successor.json"
implementation.V11_FREEZE = ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260813_v17/four_scene_effective_resource_successor.json"
implementation.PREFLIGHT = ROOT / "preflight/mysql_four_scene_20260809/runtime_preflight_v18_attempt1_20260813T1530p0800.json"
implementation.XP_PREPARED = ROOT / "history/mysql_four_scene_20260809/xp_reset_v18_between_stability_waves_20260813T1550p0800/reset_binding.json"
implementation.FREEZE_SCHEMA = "mysql_disk2_four_scene_two_worker_effective_resource_successor_v18"
implementation.PREDECESSOR_FREEZE_SCHEMA = "mysql_disk2_four_scene_two_worker_effective_resource_successor_v17"
implementation.OUTPUT_SCHEMA = "mysql_four_scene_live_rebind_v18_pilot"
implementation.AUTHORITY_VERSION = "v18_pilot"
implementation.XP_ALLOWED_STATUSES = {"PASS"}
implementation.PREFLIGHT_ALLOWED_STATUSES = {"BLOCKED_FAIL_CLOSED"}
implementation.PREFLIGHT_FORMAL_LAUNCH_AUTHORIZED = False


if __name__ == "__main__":
    raise SystemExit(implementation.main())
