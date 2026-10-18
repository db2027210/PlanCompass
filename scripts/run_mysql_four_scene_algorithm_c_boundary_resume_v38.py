#!/usr/bin/env python3
"""Resume the Rank-1 baseline reuse under the v61 launcher-grace contract."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v34 as v34
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v36 as v36


OPTIMIZATION_CONTRACT = (
    ROOT
    / "config/formal/frozen_mysql_algorithm_c_outer_runtime_20260817_v61"
    / "optimization_contract.json"
)


def main() -> int:
    # v34 is the propagation point that binds the optimization contract into
    # the base runtime immediately before the formal controller starts.
    v34.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    return v36.main()


if __name__ == "__main__":
    raise SystemExit(main())
