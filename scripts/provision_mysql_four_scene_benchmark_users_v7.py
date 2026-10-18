#!/usr/bin/env python3
"""Provision benchmark users for the post-formal-v7 live binding."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import provision_mysql_four_scene_benchmark_users_v5 as implementation


implementation.PREPARED = (
    ROOT
    / "history/mysql_four_scene_20260809"
    / "four_scene_live_rebind_v21_20260811T003400p0800"
    / "prepared_binding.json"
)


if __name__ == "__main__":
    raise SystemExit(implementation.main())
