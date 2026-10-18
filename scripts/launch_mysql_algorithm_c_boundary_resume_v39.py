#!/usr/bin/env python3
"""Launch the v38 Rank-1 continuation with bounded state-publication grace."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import launch_mysql_algorithm_c_boundary_resume_v35 as v35


def main() -> int:
    launcher = v35.v31.v29.v28.v25.v24.v23.v22.v20.v19.launcher
    launcher.CONTROLLER = (
        ROOT / "scripts/run_mysql_four_scene_algorithm_c_boundary_resume_v38.py"
    )
    launcher.RESET_SOURCE = ROOT / "scripts/reset_mysql_xp_data_planes_v7.py"
    launcher.WATCHDOG_SOURCE = (
        ROOT / "scripts/watch_mysql_formal_external_postgres_interference_v11.py"
    )
    launcher.AFFINITY_GUARD_SOURCE = (
        ROOT / "scripts/observe_frozen_external_pg_client_affinity_v1.py"
    )
    launcher.OPTIMIZATION_CONTRACT = (
        ROOT
        / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260817_v61"
        / "optimization_contract.json"
    )
    return launcher.main()


if __name__ == "__main__":
    raise SystemExit(main())
