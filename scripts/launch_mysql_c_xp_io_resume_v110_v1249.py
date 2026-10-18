#!/usr/bin/env python3
"""Retry XP-I/O C from completed outer 6 after archiving v1246 residue."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import launch_mysql_c_xp_io_resume_v110_v1248 as prior


launch = prior.launch
launch.CAMPAIGN = launch.HISTORY / "v1249_mysql_c_xp_io_resume_v110_20260906"
launch.MANIFEST = launch.CAMPAIGN / "launch_manifest.json"
launch.BARRIER = launch.Path(
    "/tmp/mysql_async_outer_start/v1249_mysql_c_xp_io_resume_v110"
)
launch.SPECS["hybench_xp_io"]["run_id"] = (
    "v1249_to_c_complete_lifecycle_v110_20260906_xp_io"
)


if __name__ == "__main__":
    raise SystemExit(launch.main())
