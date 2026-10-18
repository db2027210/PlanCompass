#!/usr/bin/env python3
"""Refresh scene authority with the isolated external-client gate."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v10 as gate_v10
from scripts import refresh_mysql_two_worker_scene_authority_v1 as predecessor

predecessor.dynamic_gate = gate_v10

if __name__ == "__main__":
    raise SystemExit(predecessor.main())
