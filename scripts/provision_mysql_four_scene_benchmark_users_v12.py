#!/usr/bin/env python3
"""Provision benchmark users for the v15 live binding."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import provision_mysql_four_scene_benchmark_users_v5 as implementation


implementation.PREPARED = (
    ROOT / "history/mysql_four_scene_20260809"
    / "four_scene_live_rebind_v26_20260811T143500p0800"
    / "prepared_binding.json"
)
implementation.FREEZE = (
    ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260811_v15"
    / "four_scene_effective_resource_successor.json"
)
implementation.FREEZE_SCHEMA = (
    "mysql_disk2_four_scene_two_worker_effective_resource_successor_v15"
)
implementation.OUTPUT_SCHEMA = "mysql_four_scene_benchmark_accounts_v15"
implementation.AUTHORITY_VERSION = "v15"


if __name__ == "__main__":
    raise SystemExit(implementation.main())
