#!/usr/bin/env python3
"""Retry v111 after binding the run-scoped reset optimization contract."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import launch_mysql_c_scene_resume_v111_v1251 as prior


launch = prior.launch
launch.CAMPAIGN = (
    launch.HISTORY / "v1252_mysql_c_four_scene_resume_v111_20260906"
)
launch.MANIFEST = launch.CAMPAIGN / "launch_manifest.json"
launch.BARRIER = launch.Path(
    "/tmp/mysql_async_outer_start/v1252_mysql_c_four_scene_resume_v111"
)
for scene, spec in launch.SPECS.items():
    spec["run_id"] = (
        "v1252_to_c_full_docker_path_resilience_v111_20260906_"
        + str(spec["suffix"])
    )


if __name__ == "__main__":
    raise SystemExit(launch.main())
