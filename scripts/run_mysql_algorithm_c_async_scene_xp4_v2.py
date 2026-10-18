#!/usr/bin/env python3
"""Run one independent Algorithm C XP scene with the v2 XP4 runtime."""

from __future__ import annotations

from pathlib import Path

from scripts import run_mysql_algorithm_c_async_scene_xp4_v1 as predecessor
from scripts import run_mysql_four_scene_algorithm_c_xp4_runtime_v2 as runtime_v2


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_ROOT = (
    ROOT / "config/formal/frozen_mysql_algorithm_c_top4_io_xp4_recovery_20260826_v9"
)


def install_successor() -> None:
    predecessor.runtime = runtime_v2
    predecessor.CONTRACT_ROOT = CONTRACT_ROOT
    predecessor.ASYNC_CONTRACT = CONTRACT_ROOT / "algorithm_c_xp4_async_contract.json"
    predecessor.OPTIMIZATION_CONTRACT = CONTRACT_ROOT / "optimization_contract.json"
    predecessor.COORDINATION_CONTRACT = CONTRACT_ROOT / "coordination_contract.json"


def main() -> int:
    install_successor()
    return predecessor.main()


if __name__ == "__main__":
    raise SystemExit(main())
