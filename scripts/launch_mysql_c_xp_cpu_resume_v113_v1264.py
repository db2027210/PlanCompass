#!/usr/bin/env python3
"""Launch XP-CPU C with the refreshed fresh-R0 authority binding."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import launch_mysql_c_xp_cpu_resume_v113_v1262 as launch


launch.CAMPAIGN = launch.HISTORY / "v1264_mysql_c_xp_cpu_resume_v113_20260906"
launch.RUN_ID = "v1264_to_c_xp_cpu_legacy_stale_recovery_v113_20260906"
launch.RUN_ROOT = launch.HISTORY / launch.RUN_ID
launch.BARRIER = Path("/tmp/mysql_async_outer_start/v1264_mysql_c_xp_cpu_v113")
launch.FRESH_R0 = launch.HISTORY / (
    "v1263_c_xp_cpu_fresh_r0_authority_v113_20260906/fresh_r0_binding.json"
)


if __name__ == "__main__":
    raise SystemExit(launch.main())
