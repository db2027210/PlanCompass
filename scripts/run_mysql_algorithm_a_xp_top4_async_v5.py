#!/usr/bin/env python3
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_a_xp_top4_async_v4 as predecessor

predecessor.predecessor.successor.CONTRACT_ROOT = (
    ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_a_20260826_v5"
)

if __name__ == "__main__":
    raise SystemExit(predecessor.predecessor.main())
