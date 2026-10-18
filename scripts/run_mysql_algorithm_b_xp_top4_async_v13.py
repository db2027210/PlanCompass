#!/usr/bin/env python3
"""Algorithm B v22 with corrected installation and the v8 rolling gate."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v8 as gate_v8
from scripts import run_mysql_algorithm_b_xp_top4_async_v6 as successor

successor.CONTRACT_ROOT = (
    ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_b_20260826_v22"
)
successor.gate_v7 = gate_v8


def main() -> int:
    successor.predecessor.install()
    successor.install()
    return int(successor.predecessor._implementation_main())


if __name__ == "__main__":
    raise SystemExit(main())
