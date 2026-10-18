#!/usr/bin/env python3
"""Launch one Top4-B scene with the v48 native-XP adapter release."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import launch_mysql_four_scene_top4_algorithm_b_two_worker_scene_v4 as predecessor


predecessor.CONTROLLER = ROOT / "scripts/run_mysql_algorithm_b_four_scene_top4_two_worker_v5.py"
predecessor.CONTRACT = (
    ROOT / "config/formal/frozen_mysql_four_scene_top4_algorithm_b_20260826_v48"
    / "algorithm_b_top4_contract.json"
)


if __name__ == "__main__":
    raise SystemExit(predecessor.main())
