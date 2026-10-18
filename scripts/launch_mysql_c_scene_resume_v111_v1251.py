#!/usr/bin/env python3
"""Resume all four C scenes on v111 from their last complete outer boundary."""

from copy import deepcopy
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import launch_mysql_c_scene_resume_v109_v1245 as launch


launch.CAMPAIGN = (
    launch.HISTORY / "v1251_mysql_c_four_scene_resume_v111_20260906"
)
launch.MANIFEST = launch.CAMPAIGN / "launch_manifest.json"
launch.BARRIER = launch.Path(
    "/tmp/mysql_async_outer_start/v1251_mysql_c_four_scene_resume_v111"
)
launch.CONTROLLER = (
    launch.ROOT / "scripts/run_mysql_algorithm_c_four_scene_client_primary_v21.py"
)
launch.TPCH_CONTROLLER = (
    launch.ROOT / "scripts/run_mysql_algorithm_c_tpch_resume_client_primary_v22.py"
)
launch.CONTRACT = launch.ROOT / (
    "config/formal/frozen_mysql_algorithm_c_pg_latest_outer_20260906_v111/"
    "algorithm_c_pg_latest_outer_contract.json"
)
launch.EXPECTED_RELEASE = "v111-full-docker-path-resilience"
launch.SPECS = deepcopy(launch.SPECS)
for scene, spec in launch.SPECS.items():
    spec["run_id"] = (
        "v1251_to_c_full_docker_path_resilience_v111_20260906_"
        + str(spec["suffix"])
    )
launch.SPECS["job_memory"].update({
    "checkpoint": "v1250_c_job_resume_checkpoint_v111_20260906",
    "resume_after_outer": 13,
})
launch.SPECS["tpch_sf4_memory"]["controller"] = launch.TPCH_CONTROLLER


if __name__ == "__main__":
    raise SystemExit(launch.main())
