#!/usr/bin/env python3
"""Launch the CPU/XP replacement lane with strict Docker-inspect retries."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import launch_mysql_c_xp_blkio_recovery_v1227 as base


base.CAMPAIGN = base.HISTORY / "v1228_mysql_c_xp_cpu_preflight_retry_20260905"
base.MANIFEST = base.CAMPAIGN / "launch_manifest.json"
base.BARRIER = Path("/tmp/mysql_async_outer_start/v1228_mysql_c_xp_cpu_retry")
base.SPECS = {
    "hybench_xp_cpu": "v1228_to_c_pg_latest_v103_20260905_xp_cpu",
}


if __name__ == "__main__":
    raise SystemExit(base.main())
