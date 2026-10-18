#!/usr/bin/env python3
"""Run the final Top4 Algorithm-B v39 release."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_b_xp_top4_async_v30 as predecessor


CONTRACT_ROOT = (
    ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_b_20260826_v39"
)


def main() -> int:
    predecessor.CONTRACT_ROOT = CONTRACT_ROOT
    predecessor.validate_contract()
    core = predecessor.install_runtime()
    return int(core._implementation_main())


if __name__ == "__main__":
    raise SystemExit(main())
