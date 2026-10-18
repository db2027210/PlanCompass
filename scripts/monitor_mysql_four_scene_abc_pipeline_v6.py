#!/usr/bin/env python3
"""Monitor current CPU-B continuation and IO C->B, then launch A."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import monitor_mysql_four_scene_abc_pipeline_v3 as predecessor

HISTORY = predecessor.base.HISTORY
predecessor.STATE = HISTORY / "v236_to_abc_top4_pipeline_20260826/pipeline_state.json"
predecessor.B_LAUNCHER = ROOT / "scripts/launch_mysql_xp_top4_algorithm_b_scene_v13.py"
predecessor.A_LAUNCHER = ROOT / "scripts/launch_mysql_algorithm_a_xp_top4_scene_v4.py"
predecessor.XP = {
    "hybench_xp_cpu": {
        "suffix": "xp_cpu",
        "c": HISTORY / "v59_to_c_xp_top4_20260825_attempt033_cpu_zero_safe_xp_cpu/run_state.json",
        "b": HISTORY / "v234_to_b_top4_fresh_20260826_attempt021_xp_cpu/run_state.json",
        "b_launch": HISTORY / "v234_to_b_top4_pipeline_20260826/xp_cpu.launch.json",
        "a": HISTORY / "v235_to_a_top4_fresh_20260826_attempt004_xp_cpu/algorithm_a_state.json",
        "a_launch": HISTORY / "v235_to_a_top4_pipeline_20260826/xp_cpu.launch.json",
    },
    "hybench_xp_io": {
        "suffix": "xp_io",
        "c": HISTORY / "v225_to_c_top4_io_xp4_20260826_attempt020_xp_io/run_state.json",
        "b": HISTORY / "v230_to_b_top4_fresh_20260826_attempt019_xp_io/run_state.json",
        "b_launch": HISTORY / "v230_to_b_top4_pipeline_20260826/xp_io.launch.json",
        "a": HISTORY / "v235_to_a_top4_fresh_20260826_attempt004_xp_io/algorithm_a_state.json",
        "a_launch": HISTORY / "v235_to_a_top4_pipeline_20260826/xp_io.launch.json",
    },
}

if __name__ == "__main__":
    raise SystemExit(predecessor.main())
