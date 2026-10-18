#!/usr/bin/env python3
"""XP4 C runtime with one v7 gate for authority and candidate waves."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v7 as gate_v7
from scripts import run_mysql_four_scene_algorithm_c_xp4_runtime_v6 as predecessor


alg = predecessor.alg
OPTIMIZATION_CONTRACT = predecessor.OPTIMIZATION_CONTRACT
WAVE_COORDINATION_CONTRACT = predecessor.WAVE_COORDINATION_CONTRACT


def install() -> None:
    predecessor.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    predecessor.WAVE_COORDINATION_CONTRACT = WAVE_COORDINATION_CONTRACT
    predecessor.runtime_v1.coordination = gate_v7
    predecessor.runtime_v1.alg.r0mod.dynamic_gate = gate_v7
    predecessor.install()
    predecessor.runtime_v1.coordination = gate_v7
    predecessor.runtime_v1.alg.r0mod.dynamic_gate = gate_v7


def main() -> int:
    install()
    return predecessor.runtime_v1.main()


if __name__ == "__main__":
    raise SystemExit(main())

