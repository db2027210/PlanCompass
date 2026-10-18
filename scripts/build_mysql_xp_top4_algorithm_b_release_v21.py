#!/usr/bin/env python3
"""Freeze corrected CPU-B v31 after the premeasurement v30 failure."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_mysql_xp_top4_algorithm_b_release_v20 as predecessor


predecessor.SOURCE = (
    ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_b_20260826_v30"
)
predecessor.OUTPUT = (
    ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_b_20260826_v31"
)
predecessor.CONTROLLER = ROOT / "scripts/run_mysql_algorithm_b_xp_top4_async_v22.py"
predecessor.LAUNCHER = (
    ROOT / "scripts/launch_mysql_algorithm_b_cpu_rank13_continuation_v9.py"
)


if __name__ == "__main__":
    raise SystemExit(predecessor.main())
