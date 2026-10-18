#!/usr/bin/env python3
"""Rebuild all XP planes under the sealed v17 resource envelope."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import reset_mysql_xp_for_v16 as implementation


implementation.FREEZE = ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260813_v17/four_scene_effective_resource_successor.json"
implementation.PREDECESSOR = ROOT / "history/mysql_four_scene_20260809/xp_reset_v16_after_512k_stability_failure_20260813T1252p0800/reset_binding.json"
implementation.FREEZE_SCHEMA = "mysql_disk2_four_scene_two_worker_effective_resource_successor_v17"
implementation.XP_IO_WRITE_BPS = 800000
implementation.OUTPUT_SCHEMA = "mysql_four_scene_xp_v17_reset_binding_v1"


if __name__ == "__main__":
    raise SystemExit(implementation.main())
