#!/usr/bin/env python3
"""Monitor the v263 IO-C recovery and current CPU-B pipeline."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import monitor_mysql_four_scene_abc_pipeline_v8 as predecessor


predecessor.STATE = (
    predecessor.HISTORY / "v264_to_abc_top4_pipeline_20260826/pipeline_state.json"
)
predecessor.XP["hybench_xp_cpu"]["b"] = (
    predecessor.HISTORY
    / "v265_to_b_top4_score_resume_20260826_attempt029_xp_cpu/run_state.json"
)
predecessor.XP["hybench_xp_cpu"]["b_launch"] = (
    predecessor.HISTORY / "v265_to_b_top4_pipeline_20260826/xp_cpu.launch.json"
)
predecessor.XP["hybench_xp_cpu"]["a"] = (
    predecessor.HISTORY
    / "v266_to_a_top4_fresh_20260826_attempt007_xp_cpu/algorithm_a_state.json"
)
predecessor.XP["hybench_xp_cpu"]["a_launch"] = (
    predecessor.HISTORY / "v266_to_a_top4_pipeline_20260826/xp_cpu.launch.json"
)
predecessor.XP["hybench_xp_io"]["c"] = (
    predecessor.HISTORY
    / "v263_to_c_top4_io_empty_wave_20260826_attempt024_xp_io/run_state.json"
)
predecessor.B_LAUNCHER = (
    predecessor.ROOT / "scripts/launch_mysql_xp_top4_algorithm_b_scene_v15.py"
)
predecessor.XP["hybench_xp_io"]["a"] = (
    predecessor.HISTORY
    / "v266_to_a_top4_fresh_20260826_attempt007_xp_io/algorithm_a_state.json"
)
predecessor.XP["hybench_xp_io"]["a_launch"] = (
    predecessor.HISTORY / "v266_to_a_top4_pipeline_20260826/xp_io.launch.json"
)
predecessor.A_LAUNCHER = (
    predecessor.ROOT / "scripts/launch_mysql_algorithm_a_xp_top4_scene_v7.py"
)


if __name__ == "__main__":
    raise SystemExit(predecessor.main())
