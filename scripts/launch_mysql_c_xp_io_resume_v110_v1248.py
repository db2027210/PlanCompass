#!/usr/bin/env python3
"""Recover only the failed XP-I/O C scene from completed outer 6."""

from copy import deepcopy
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import launch_mysql_c_scene_resume_v110_v1247 as launch_v110


launch = launch_v110.launch
launch.CAMPAIGN = launch.HISTORY / "v1248_mysql_c_xp_io_resume_v110_20260906"
launch.MANIFEST = launch.CAMPAIGN / "launch_manifest.json"
launch.BARRIER = launch.Path(
    "/tmp/mysql_async_outer_start/v1248_mysql_c_xp_io_resume_v110"
)
io_spec = deepcopy(launch.SPECS["hybench_xp_io"])
io_spec["run_id"] = "v1248_to_c_complete_lifecycle_v110_20260906_xp_io"
launch.SPECS = {"hybench_xp_io": io_spec}


if __name__ == "__main__":
    raise SystemExit(launch.main())
