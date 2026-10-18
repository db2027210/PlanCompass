#!/usr/bin/env python3
"""Launch Algorithm C with post-target stability confirmation semantics."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import launch_mysql_algorithm_c_boundary_resume_v19 as v19


def main() -> int:
    v19.launcher.CONTROLLER = (
        ROOT / "scripts/run_mysql_four_scene_algorithm_c_boundary_resume_v20.py"
    )
    v19.launcher.RESET_SOURCE = ROOT / "scripts/reset_mysql_xp_data_planes_v6.py"
    v19.launcher.WATCHDOG_SOURCE = (
        ROOT / "scripts/watch_mysql_formal_external_postgres_interference_v9.py"
    )
    v19.launcher.AFFINITY_GUARD_SOURCE = (
        ROOT / "scripts/observe_frozen_external_pg_client_affinity_v1.py"
    )
    v19.launcher.OPTIMIZATION_CONTRACT = (
        ROOT
        / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260817_v59"
        / "optimization_contract.json"
    )
    return v19.launcher.main()


if __name__ == "__main__":
    raise SystemExit(main())
