#!/usr/bin/env python3
"""Top4 B v14 with four-lane scheduling and original-baseline lineage."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_algorithm_b_fresh_parallel_4x_scheduler_v26 as scheduler_v26
from scripts import run_mysql_algorithm_b_xp_top4_async_v3 as predecessor


CONTRACT_ROOT = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_b_20260826_v14"


def install() -> None:
    predecessor.CONTRACT_ROOT = CONTRACT_ROOT
    predecessor._impl["scheduler"] = scheduler_v26


def main() -> int:
    install()
    return predecessor.main()


if __name__ == "__main__":
    raise SystemExit(main())
