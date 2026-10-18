#!/usr/bin/env python3
"""Monitor the current CPU/IO Top4 C -> B -> A successor paths."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import monitor_mysql_four_scene_abc_pipeline_v1 as monitor


monitor.B_LAUNCHER = ROOT / "scripts/launch_mysql_xp_top4_algorithm_b_successor_v1.py"
monitor.XP["hybench_xp_io"]["c"] = (
    monitor.HISTORY
    / "v172_to_c_top4_io_xp4_20260826_attempt006_xp_io/run_state.json"
)


if __name__ == "__main__":
    raise SystemExit(monitor.main())
