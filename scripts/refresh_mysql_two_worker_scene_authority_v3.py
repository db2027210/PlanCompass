#!/usr/bin/env python3
"""Self-contained scene-authority refresh using the storage-scoped gate."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v9 as gate_v9
from scripts import refresh_mysql_two_worker_scene_authority_v1 as predecessor


predecessor.dynamic_gate = gate_v9


if __name__ == "__main__":
    raise SystemExit(predecessor.main())
