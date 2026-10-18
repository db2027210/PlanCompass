#!/usr/bin/env python3
"""Run Top4-A under the corrected v8 CPU-B sequence contract."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_a_xp_top4_async_v3 as predecessor

predecessor.successor.CONTRACT_ROOT = (
    ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_a_20260826_v8"
)

if __name__ == "__main__":
    raise SystemExit(predecessor.main())
