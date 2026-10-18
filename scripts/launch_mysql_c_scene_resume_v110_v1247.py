#!/usr/bin/env python3
"""Launch all four C scenes from completed outer boundaries on v110."""

from copy import deepcopy
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import launch_mysql_c_scene_resume_v109_v1245 as launch


launch.CAMPAIGN = (
    launch.HISTORY / "v1247_mysql_c_four_scene_resume_v110_20260906"
)
launch.MANIFEST = launch.CAMPAIGN / "launch_manifest.json"
launch.BARRIER = launch.Path(
    "/tmp/mysql_async_outer_start/v1247_mysql_c_four_scene_resume_v110"
)
launch.CONTROLLER = (
    launch.ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v20.py"
)
launch.TPCH_CONTROLLER = (
    launch.ROOT / "scripts/run_mysql_algorithm_c_tpch_resume_client_primary_v21.py"
)
launch.CONTRACT = launch.ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260906_v110/"
    "algorithm_c_pg_latest_outer_contract.json"
)
launch.EXPECTED_RELEASE = "v110-complete-lifecycle-recovery"
launch.SPECS = deepcopy(launch.SPECS)
for scene, spec in launch.SPECS.items():
    spec["run_id"] = (
        "v1247_to_c_complete_lifecycle_v110_20260906_" + str(spec["suffix"])
    )
launch.SPECS["tpch_sf4_memory"]["controller"] = launch.TPCH_CONTROLLER


if __name__ == "__main__":
    raise SystemExit(launch.main())
