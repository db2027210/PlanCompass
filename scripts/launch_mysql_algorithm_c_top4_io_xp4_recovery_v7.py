#!/usr/bin/env python3
"""Launch v13 IO C after refreshing the four-worker live authority."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import launch_mysql_algorithm_c_top4_io_xp4_recovery_v1 as predecessor


predecessor.LIVE = (
    predecessor.HISTORY
    / "v182_to_xp4_io_live_authority_refresh_20260826/hybench_xp_io"
)
predecessor.OUTPUT = (
    predecessor.HISTORY / "v183_to_c_top4_io_xp4_recovery_20260826"
)
predecessor.RUN_ID = "v183_to_c_top4_io_xp4_20260826_attempt012_xp_io"
predecessor.CONTROLLER = (
    predecessor.ROOT / "scripts/run_mysql_algorithm_c_async_scene_xp4_v6.py"
)
predecessor.UNIT = "mysql-algc-top4-io-xp4-v183.service"


def main() -> int:
    return predecessor.main()


if __name__ == "__main__":
    raise SystemExit(main())
