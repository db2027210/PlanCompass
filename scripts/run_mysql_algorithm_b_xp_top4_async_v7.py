#!/usr/bin/env python3
"""Top4 B v16 frozen after the current-gap authority passed."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_b_xp_top4_async_v6 as predecessor


predecessor.CONTRACT_ROOT = (
    ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_b_20260826_v16"
)


if __name__ == "__main__":
    raise SystemExit(predecessor.main())

