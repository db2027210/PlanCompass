#!/usr/bin/env python3
"""Launch Algorithm C with the v53 watchdog-confirmation release."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import launch_mysql_algorithm_c_boundary_resume_v2 as launcher


def main() -> int:
    launcher.CONTROLLER = (
        ROOT / "scripts/run_mysql_four_scene_algorithm_c_boundary_resume_v12.py"
    )
    launcher.RESET_SOURCE = ROOT / "scripts/reset_mysql_xp_data_planes_v6.py"
    launcher.WATCHDOG_SOURCE = (
        ROOT / "scripts/watch_mysql_formal_external_postgres_interference_v4.py"
    )
    launcher.OPTIMIZATION_CONTRACT = (
        ROOT
        / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260817_v53"
        / "optimization_contract.json"
    )
    return launcher.main()


if __name__ == "__main__":
    raise SystemExit(main())
