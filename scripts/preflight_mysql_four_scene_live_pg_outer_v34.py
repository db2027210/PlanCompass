#!/usr/bin/env python3
"""Run the v18 live gate under the v34 PG-outer coordination freeze."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v1 as coordination


coordination.CONTRACT_PATH = (
    ROOT / "config/formal/frozen_mysql_pg_outer_20260819_v34"
    / "coordination_contract.json"
)

from scripts import preflight_mysql_four_scene_live_v18 as v18


if __name__ == "__main__":
    raise SystemExit(v18.implementation.main())
