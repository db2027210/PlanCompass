#!/usr/bin/env python3
"""Execute the all-scene XP4 handoff with inventory-time PG proof."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_co_resident_pg_isolation_v15 as pg_wrapper
from scripts import execute_mysql_algorithm_a_all_scene_to_xp_scene_v1 as predecessor


CONFIG = ROOT / "config/formal/frozen_mysql_algorithm_a_all_scene_to_xp4_20260823_v2"


def main() -> int:
    predecessor.CONTRACT_PATH = CONFIG / "transition_contract.json"
    predecessor.CONTROLLER = ROOT / "scripts/run_mysql_algorithm_a_job_to_xp_resume_v3.py"
    predecessor.pg_wrapper = pg_wrapper
    return predecessor.main()


if __name__ == "__main__":
    raise SystemExit(main())
