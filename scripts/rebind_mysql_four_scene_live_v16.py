#!/usr/bin/env python3
"""Rebind v15 workers to the sealed Algorithm C outer-1 checkpoint."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import rebind_mysql_four_scene_live_v8 as implementation


implementation.FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260811_v15"
    / "four_scene_effective_resource_successor.json"
)
implementation.V11_FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260811_v14"
    / "four_scene_effective_resource_successor.json"
)
implementation.PREFLIGHT = (
    ROOT / "preflight/mysql_four_scene_20260809/runtime_preflight_v30.json"
)
implementation.XP_PREPARED = (
    ROOT / "history/mysql_four_scene_20260809"
    / "algorithm_c_outer1_checkpoint_v1_20260811T184000p0800"
    / "prepared_binding.json"
)
implementation.FREEZE_SCHEMA = (
    "mysql_disk2_four_scene_two_worker_effective_resource_successor_v15"
)
implementation.PREDECESSOR_FREEZE_SCHEMA = (
    "mysql_disk2_four_scene_two_worker_effective_resource_successor_v14"
)
implementation.OUTPUT_SCHEMA = "mysql_four_scene_live_rebind_v15_outer1_resume"
implementation.AUTHORITY_VERSION = "v15_algorithm_c_outer1_resume"


if __name__ == "__main__":
    raise SystemExit(implementation.main())
