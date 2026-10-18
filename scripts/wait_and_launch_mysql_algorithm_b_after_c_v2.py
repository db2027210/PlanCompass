#!/usr/bin/env python3
"""Launch Algorithm B after C using the sealed Outer-12 empty quarantine."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import wait_and_launch_mysql_algorithm_b_after_c_v1 as waiter


def main() -> int:
    waiter.QUARANTINE = (
        ROOT / "history/mysql_four_scene_20260809"
        / "algorithm_c_v47_replay_quarantine_v10_outer12_v13_20260816T1435p0800"
        / "replay_quarantine.json"
    )
    return waiter.main()


if __name__ == "__main__":
    raise SystemExit(main())
