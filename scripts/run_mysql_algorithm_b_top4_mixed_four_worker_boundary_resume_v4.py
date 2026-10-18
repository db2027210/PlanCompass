#!/usr/bin/env python3
"""Four-worker boundary finalizer using the running-inventory v72 gate."""

from scripts import mysql_cross_project_wave_coordination_v16 as gate_v16
from scripts import run_mysql_algorithm_b_top4_mixed_four_worker_boundary_resume_v2 as predecessor


def main() -> int:
    predecessor.gate_v14 = gate_v16
    return int(predecessor.main())


if __name__ == "__main__":
    raise SystemExit(main())
