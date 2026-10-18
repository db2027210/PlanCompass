#!/usr/bin/env python3
"""Provision benchmark users for the v14 live binding."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import provision_mysql_four_scene_benchmark_users_v5 as implementation


implementation.PREPARED = (
    ROOT / "history/mysql_four_scene_20260809"
    / "four_scene_live_rebind_v25_20260811T130000p0800"
    / "prepared_binding.json"
)
implementation.FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260811_v14"
    / "four_scene_effective_resource_successor.json"
)
implementation.FREEZE_SCHEMA = (
    "mysql_disk2_four_scene_two_worker_effective_resource_successor_v14"
)
implementation.OUTPUT_SCHEMA = "mysql_four_scene_benchmark_accounts_v14"
implementation.AUTHORITY_VERSION = "v14"


if __name__ == "__main__":
    raise SystemExit(implementation.main())
