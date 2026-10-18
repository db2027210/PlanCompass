#!/usr/bin/env python3
"""Chain C to the v49 Top4-B release and then to Top4-A per scene."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import monitor_mysql_four_scene_top4_abc_chain_v5 as predecessor


predecessor.predecessor.B_LAUNCHER = (
    ROOT / "scripts/launch_mysql_four_scene_top4_algorithm_b_two_worker_scene_v6.py"
)


if __name__ == "__main__":
    raise SystemExit(predecessor.predecessor.main())
