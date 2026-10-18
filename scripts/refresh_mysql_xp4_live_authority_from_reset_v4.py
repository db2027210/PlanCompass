#!/usr/bin/env python3
"""Refresh XP4 authority with dynamic actual-container isolation."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import execute_mysql_algorithm_c_xp4_scene_handoff_v1 as handoff
from scripts import mysql_cross_project_wave_coordination_v7 as gate_v7
from scripts import refresh_mysql_xp4_live_authority_from_reset_v1 as predecessor


def main() -> int:
    handoff.dynamic_gate = gate_v7
    return predecessor.main()


if __name__ == "__main__":
    raise SystemExit(main())

