#!/usr/bin/env python3
"""Provision benchmark users for the v13 live binding."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import provision_mysql_four_scene_benchmark_users_v5 as implementation


implementation.PREPARED = (
    ROOT / "history/mysql_four_scene_20260809"
    / "four_scene_live_rebind_v23_20260811T114500p0800"
    / "prepared_binding.json"
)
implementation.FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260811_v13"
    / "four_scene_effective_resource_successor.json"
)
implementation.FREEZE_SCHEMA = (
    "mysql_disk2_four_scene_two_worker_effective_resource_successor_v13"
)
implementation.OUTPUT_SCHEMA = "mysql_four_scene_benchmark_accounts_v13"
implementation.AUTHORITY_VERSION = "v13"


if __name__ == "__main__":
    raise SystemExit(implementation.main())
