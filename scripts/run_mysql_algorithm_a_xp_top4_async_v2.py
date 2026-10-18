#!/usr/bin/env python3
"""Run fresh per-scene Top4 A with the v2 successor contract."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_a_xp_top4_async_v1 as predecessor


CONTRACT_ROOT = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_a_20260826_v2"


def install() -> None:
    predecessor.CONTRACT_ROOT = CONTRACT_ROOT
    predecessor.CONTRACT = CONTRACT_ROOT / "algorithm_a_top4_contract.json"
    predecessor.COORDINATION = CONTRACT_ROOT / "coordination_contract.json"


def main() -> int:
    install()
    return predecessor.main()


if __name__ == "__main__":
    raise SystemExit(main())
