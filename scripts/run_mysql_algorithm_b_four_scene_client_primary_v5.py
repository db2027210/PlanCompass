#!/usr/bin/env python3
"""Run client-primary B with v3 reset cleanup and scoped tokens."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import reset_mysql_xp_data_planes_client_primary_v3 as xp_reset
from scripts import run_mysql_algorithm_b_four_scene_client_primary_v4 as predecessor


CONTRACT_ROOT = ROOT / (
    "config/formal/"
    "frozen_mysql_four_scene_algorithm_b_client_primary_20260903_v71"
)
CONTRACT = CONTRACT_ROOT / "algorithm_b_top4_contract.json"
COORDINATION = CONTRACT_ROOT / "coordination_contract.json"


def install_runtime() -> Any:
    predecessor.CONTRACT_ROOT = CONTRACT_ROOT
    predecessor.CONTRACT = CONTRACT
    predecessor.COORDINATION = COORDINATION
    v2 = predecessor.predecessor
    v2.CONTRACT_ROOT = CONTRACT_ROOT
    v2.CONTRACT = CONTRACT
    v2.COORDINATION = COORDINATION
    v2.xp_reset = xp_reset
    v2.predecessor.CONTRACT_ROOT = CONTRACT_ROOT
    v2.predecessor.CONTRACT = CONTRACT
    v2.predecessor.COORDINATION = COORDINATION
    v2.predecessor.xp_reset = xp_reset
    return predecessor.install_runtime()


def main() -> int:
    return int(install_runtime()._implementation_main())


if __name__ == "__main__":
    raise SystemExit(main())

