#!/usr/bin/env python3
"""Run fresh-search v95 C with the v25 gate and standard checkpoint verifier."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v25 as gate_v25
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v6 as predecessor


def main() -> int:
    v5 = predecessor.predecessor
    v4 = v5.predecessor
    v3 = v4.predecessor
    v1 = v3.predecessor
    v74 = v1.predecessor
    v73 = v74.predecessor
    original_gate = v73.gate_v11
    v73.gate_v11 = gate_v25
    try:
        return int(predecessor.main())
    finally:
        v73.gate_v11 = original_gate


if __name__ == "__main__":
    raise SystemExit(main())
