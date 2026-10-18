#!/usr/bin/env python3
"""Launch the import-safe Top4 IO Algorithm C outer-4 recovery."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import launch_mysql_algorithm_c_top4_io_xp4_recovery_v1 as predecessor


predecessor.OUTPUT = (
    predecessor.HISTORY / "v174_to_c_top4_io_xp4_recovery_20260826"
)
predecessor.RUN_ID = "v174_to_c_top4_io_xp4_20260826_attempt008_xp_io"
predecessor.CONTROLLER = (
    predecessor.ROOT / "scripts/run_mysql_algorithm_c_async_scene_xp4_v3.py"
)
predecessor.UNIT = "mysql-algc-top4-io-xp4-v174.service"


def main() -> int:
    return predecessor.main()


if __name__ == "__main__":
    raise SystemExit(main())
