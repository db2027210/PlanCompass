#!/usr/bin/env python3
"""Run one zero-credit native-XP stability wave on both v17 XP/I/O lanes."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_xp_io_v16_stability_pilot as implementation


implementation.FREEZE = ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260813_v17/four_scene_effective_resource_successor.json"
implementation.RESET = ROOT / "history/mysql_four_scene_20260809/xp_reset_v17_800k_stability_candidate_20260813T1430p0800/reset_binding.json"
implementation.BLOCKED_PREFLIGHT = ROOT / "preflight/mysql_four_scene_20260809/runtime_preflight_v17_attempt3_20260813T1442p0800.json"
implementation.FREEZE_SCHEMA = "mysql_disk2_four_scene_two_worker_effective_resource_successor_v17"
implementation.XP_IO_WRITE_BPS = 800000
implementation.RESULT_SCHEMA = "mysql_xp_io_v17_stability_pilot_v1"


if __name__ == "__main__":
    raise SystemExit(implementation.main())
