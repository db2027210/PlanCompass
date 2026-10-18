#!/usr/bin/env python3
"""Mixed Top4-B v24 runtime with JOB on the nonblocking gate v73."""

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v17 as gate_v17
from scripts import reset_mysql_xp_data_planes_mixed_four_worker_v2 as reset_v2
from scripts import run_mysql_algorithm_b_four_scene_top4_mixed_four_worker_v1 as predecessor


FREEZE = ROOT / "config/formal/frozen_mysql_four_scene_mixed_disk_four_worker_20260827_v4/four_scene_effective_resource_successor.json"
FREEZE_SCHEMA = "mysql_four_scene_mixed_disk_four_worker_successor_v4"
CONTRACT_ROOT = ROOT / "config/formal/frozen_mysql_four_scene_top4_algorithm_b_mixed_four_worker_20260827_v24"
CONTRACT = CONTRACT_ROOT / "algorithm_b_top4_contract.json"
COORDINATION = CONTRACT_ROOT / "coordination_contract.json"


def _configure_predecessor() -> None:
    predecessor.FREEZE = FREEZE
    predecessor.FREEZE_SCHEMA = FREEZE_SCHEMA
    predecessor.CONTRACT_ROOT = CONTRACT_ROOT
    predecessor.CONTRACT = CONTRACT
    predecessor.COORDINATION = COORDINATION
    predecessor.reset_mixed = reset_v2
    predecessor.gate_v14 = gate_v17


def install_runtime() -> Any:
    _configure_predecessor()
    return predecessor.install_runtime()


if __name__ == "__main__":
    _configure_predecessor()
    raise SystemExit(predecessor.main())
