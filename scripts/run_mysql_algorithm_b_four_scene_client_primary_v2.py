#!/usr/bin/env python3
"""Run client-primary B with the v20 container-local no-swap reset."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import reset_mysql_xp_data_planes_client_primary_v2 as xp_reset
from scripts import run_mysql_algorithm_b_four_scene_client_primary_v1 as predecessor


CONTRACT_ROOT = ROOT / (
    "config/formal/"
    "frozen_mysql_four_scene_algorithm_b_client_primary_20260903_v68"
)
CONTRACT = CONTRACT_ROOT / "algorithm_b_top4_contract.json"
COORDINATION = CONTRACT_ROOT / "coordination_contract.json"


def install_runtime() -> Any:
    predecessor.CONTRACT_ROOT = CONTRACT_ROOT
    predecessor.CONTRACT = CONTRACT
    predecessor.COORDINATION = COORDINATION
    predecessor.xp_reset = xp_reset
    return predecessor.install_runtime()


def main() -> int:
    return int(install_runtime()._implementation_main())


if __name__ == "__main__":
    raise SystemExit(main())
