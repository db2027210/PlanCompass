#!/usr/bin/env python3
"""Launch the v71 XP technical recovery."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import launch_mysql_algorithm_b_client_primary_xp_recovery_v1 as predecessor


def main() -> int:
    predecessor.CONTROLLER = (
        ROOT / "scripts/run_mysql_algorithm_b_four_scene_client_primary_v5.py"
    )
    predecessor.CONTRACT = ROOT / (
        "config/formal/"
        "frozen_mysql_four_scene_algorithm_b_client_primary_20260903_v71/"
        "algorithm_b_top4_contract.json"
    )
    return predecessor.main()


if __name__ == "__main__":
    raise SystemExit(main())
