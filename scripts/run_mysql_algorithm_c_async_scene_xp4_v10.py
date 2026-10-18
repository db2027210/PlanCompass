#!/usr/bin/env python3
"""Run independent Algorithm C XP with the v17 scoped-gate contract."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_async_scene_xp4_v1 as predecessor
from scripts import run_mysql_four_scene_algorithm_c_xp4_runtime_v7 as runtime_v7


CONTRACT_ROOT = (
    ROOT / "config/formal/frozen_mysql_algorithm_c_top4_io_xp4_recovery_20260826_v17"
)


def install_successor() -> None:
    predecessor.runtime = runtime_v7
    predecessor.CONTRACT_ROOT = CONTRACT_ROOT
    predecessor.ASYNC_CONTRACT = CONTRACT_ROOT / "algorithm_c_xp4_async_contract.json"
    predecessor.OPTIMIZATION_CONTRACT = CONTRACT_ROOT / "optimization_contract.json"
    predecessor.COORDINATION_CONTRACT = CONTRACT_ROOT / "coordination_contract.json"


def main() -> int:
    install_successor()
    return predecessor.main()


if __name__ == "__main__":
    raise SystemExit(main())
