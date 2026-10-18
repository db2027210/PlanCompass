#!/usr/bin/env python3
"""Release completed memory slots to XP using current TPC-H authority."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import monitor_mysql_algorithm_a_all_scene_to_xp4_v1 as predecessor


HISTORY = ROOT / "history/mysql_four_scene_20260809"
CONFIG = ROOT / "config/formal/frozen_mysql_algorithm_a_all_scene_to_xp4_20260823_v2"


def main() -> int:
    predecessor.CONTRACT_DIR = CONFIG
    predecessor.TPCH_STATE = (
        HISTORY
        / "v69_to_a_tpch_pg_wrapper_snapshot_resume_20260823_attempt001_tpch"
        / "algorithm_a_state.json"
    )
    predecessor.SUCCESSOR_FREEZE = CONFIG / "xp_four_worker_resource_successor.json"
    predecessor.EXECUTOR = (
        ROOT / "scripts/execute_mysql_algorithm_a_all_scene_to_xp_scene_v2.py"
    )
    return predecessor.main()


if __name__ == "__main__":
    raise SystemExit(main())
