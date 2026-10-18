#!/usr/bin/env python3
"""Refresh CPU XP4 authority after the disjoint slot transition."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import execute_mysql_algorithm_c_xp4_scene_handoff_v1 as handoff
from scripts import mysql_cross_project_wave_coordination_v6 as gate_v6
from scripts import prepare_mysql_xp4_live_authority_v1 as authority_base
from scripts import refresh_mysql_xp4_live_authority_from_reset_v1 as predecessor


FREEZE = (
    ROOT / "config/formal/frozen_mysql_xp_cpu_disjoint_slots_20260826_v1"
    / "resource_successor.json"
)


def main() -> int:
    authority_base.FREEZE = FREEZE
    handoff.dynamic_gate = gate_v6
    return predecessor.main()


if __name__ == "__main__":
    raise SystemExit(main())

