#!/usr/bin/env python3
"""Run Top4 A on resource v4 with the current dynamic gate."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v8 as gate_v8
from scripts import run_mysql_algorithm_a_xp_top4_async_v2 as successor

successor.CONTRACT_ROOT = (
    ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_a_20260826_v3"
)


def main() -> int:
    successor.predecessor.no_lease.coordination = gate_v8
    return successor.main()


if __name__ == "__main__":
    raise SystemExit(main())
