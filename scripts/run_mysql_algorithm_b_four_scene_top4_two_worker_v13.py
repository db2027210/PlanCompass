#!/usr/bin/env python3
"""v56 entry point for the isolated-client continuation runtime."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_b_four_scene_top4_two_worker_v11 as predecessor

predecessor.CONTRACT_ROOT = ROOT / "config/formal/frozen_mysql_four_scene_top4_algorithm_b_20260826_v56"
predecessor.CONTRACT = predecessor.CONTRACT_ROOT / "algorithm_b_top4_contract.json"
predecessor.COORDINATION = predecessor.CONTRACT_ROOT / "coordination_contract.json"

if __name__ == "__main__":
    raise SystemExit(predecessor.main())
