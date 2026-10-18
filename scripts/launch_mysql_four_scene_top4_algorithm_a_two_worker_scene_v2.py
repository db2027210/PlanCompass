#!/usr/bin/env python3
"""Launch Top4-A with the v13 handoff authority release."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import launch_mysql_four_scene_top4_algorithm_a_two_worker_scene_v1 as base

base.CONTROLLER = ROOT / "scripts/run_mysql_algorithm_a_four_scene_top4_two_worker_v2.py"
base.REFRESH = ROOT / "scripts/refresh_mysql_two_worker_scene_authority_v5.py"
base.CONTRACT = (
    ROOT / "config/formal/frozen_mysql_four_scene_top4_algorithm_a_20260826_v13"
    / "algorithm_a_top4_contract.json"
)

if __name__ == "__main__":
    raise SystemExit(base.main())
