#!/usr/bin/env python3
"""Rotate benchmark users for the fresh outer-1 resume binding."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import provision_mysql_four_scene_benchmark_users_v15 as implementation


implementation.implementation.PREPARED = (
    ROOT / "history/mysql_four_scene_20260809"
    / "four_scene_live_rebind_v29_20260812T005100p0800"
    / "prepared_binding.json"
)
implementation.implementation.OUTPUT_SCHEMA = (
    "mysql_four_scene_benchmark_accounts_v15_outer1_resume_fresh_v1"
)
implementation.implementation.AUTHORITY_VERSION = (
    "v15_algorithm_c_outer1_resume_fresh_v1"
)


if __name__ == "__main__":
    raise SystemExit(implementation.implementation.main())
