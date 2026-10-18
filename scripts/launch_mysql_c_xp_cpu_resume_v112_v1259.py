#!/usr/bin/env python3
"""Resume XP-CPU C with the checkpoint-bound v112 replay quarantine."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import launch_mysql_c_xp_cpu_resume_v112_v1257 as launch


launch.CAMPAIGN = launch.HISTORY / "v1259_mysql_c_xp_cpu_resume_v112_20260906"
launch.RUN_ID = "v1259_to_c_xp_cpu_stale_task_recovery_v112_20260906"
launch.RUN_ROOT = launch.HISTORY / launch.RUN_ID
launch.BARRIER = Path("/tmp/mysql_async_outer_start/v1259_mysql_c_xp_cpu_v112")
launch.QUARANTINE = launch.HISTORY / (
    "v1258_c_xp_cpu_resume_quarantine_v112_20260906/replay_quarantine.json"
)


if __name__ == "__main__":
    raise SystemExit(launch.main())
