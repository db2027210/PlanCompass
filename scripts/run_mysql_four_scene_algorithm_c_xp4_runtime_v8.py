#!/usr/bin/env python3
"""XP4 C runtime using actual external CPU-affinity isolation."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v6 as gate_v6
from scripts import run_mysql_four_scene_algorithm_c_xp4_runtime_v6 as predecessor


alg = predecessor.alg
OPTIMIZATION_CONTRACT = predecessor.OPTIMIZATION_CONTRACT
WAVE_COORDINATION_CONTRACT = predecessor.WAVE_COORDINATION_CONTRACT


def install() -> None:
    predecessor.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    predecessor.WAVE_COORDINATION_CONTRACT = WAVE_COORDINATION_CONTRACT
    predecessor.runtime_v1.coordination = gate_v6
    predecessor.install()


def main() -> int:
    install()
    return predecessor.runtime_v1.main()


if __name__ == "__main__":
    raise SystemExit(main())

