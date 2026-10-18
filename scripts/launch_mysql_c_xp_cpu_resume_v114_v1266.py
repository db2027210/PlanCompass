#!/usr/bin/env python3
"""Launch XP-CPU C v114 with the required frozen scene suffix."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import launch_mysql_c_xp_cpu_resume_v114_v1265 as launch


launch.CAMPAIGN = launch.HISTORY / "v1266_mysql_c_xp_cpu_resume_v114_20260907"
launch.RUN_ID = "v1266_to_c_live_authority_gate_v114_20260907_xp_cpu"
launch.RUN_ROOT = launch.HISTORY / launch.RUN_ID
launch.BARRIER = Path("/tmp/mysql_async_outer_start/v1266_mysql_c_xp_cpu_v114")


if __name__ == "__main__":
    raise SystemExit(launch.main())
