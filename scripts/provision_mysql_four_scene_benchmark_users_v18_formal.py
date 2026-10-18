#!/usr/bin/env python3
"""Verify benchmark accounts for a formal v18 prepared binding."""

from pathlib import Path
import os
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import provision_mysql_four_scene_benchmark_users_v5 as implementation


PREPARED = os.environ.get("MYSQL_V18_FORMAL_PREPARED")
if not PREPARED:
    raise SystemExit("MYSQL_V18_FORMAL_PREPARED is required")
implementation.PREPARED = Path(PREPARED).resolve()
implementation.FREEZE = ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260813_v18/four_scene_effective_resource_successor.json"
implementation.FREEZE_SCHEMA = "mysql_disk2_four_scene_two_worker_effective_resource_successor_v18"
implementation.OUTPUT_SCHEMA = "mysql_four_scene_benchmark_accounts_v18_formal"
implementation.AUTHORITY_VERSION = "v18_formal"


if __name__ == "__main__":
    raise SystemExit(implementation.main())
