#!/usr/bin/env python3
"""Chain C to the v50 Top4-B release and then to Top4-A per scene."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import monitor_mysql_four_scene_top4_abc_chain_v6 as predecessor


base = predecessor.predecessor.predecessor
base.B_LAUNCHER = ROOT / "scripts/launch_mysql_four_scene_top4_algorithm_b_two_worker_scene_v7.py"


if __name__ == "__main__":
    raise SystemExit(base.main())
