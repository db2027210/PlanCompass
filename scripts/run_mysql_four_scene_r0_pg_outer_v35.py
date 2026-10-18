#!/usr/bin/env python3
"""Run formal v18 R0 under the v35 PG-outer coordination freeze."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v1 as coordination
from scripts import mysql_async_shared_prestart_authority_v1 as shared_prestart


coordination.CONTRACT_PATH = (
    ROOT / "config/formal/frozen_mysql_pg_outer_20260819_v35"
    / "coordination_contract.json"
)
shared_prestart.install_shared_lock_verifier(coordination)

from scripts import run_mysql_four_scene_r0_v18 as v18


if __name__ == "__main__":
    raise SystemExit(v18.implementation.main())
