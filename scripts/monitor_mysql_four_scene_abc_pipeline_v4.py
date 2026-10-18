#!/usr/bin/env python3
"""Monitor the current Top4 XP C -> B -> A independent pipelines."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import monitor_mysql_four_scene_abc_pipeline_v3 as predecessor


predecessor.STATE = (
    predecessor.base.HISTORY / "v190_to_abc_top4_pipeline_20260826/pipeline_state.json"
)
predecessor.B_LAUNCHER = ROOT / "scripts/launch_mysql_xp_top4_algorithm_b_scene_v5.py"
predecessor.A_LAUNCHER = ROOT / "scripts/launch_mysql_algorithm_a_xp_top4_scene_v2.py"
predecessor.XP = {
    "hybench_xp_cpu": {
        "suffix": "xp_cpu",
        "c": predecessor.base.HISTORY / "v59_to_c_xp_top4_20260825_attempt033_cpu_zero_safe_xp_cpu/run_state.json",
        "b": predecessor.base.HISTORY / "v188_to_b_top4_fresh_20260826_attempt012_xp_cpu/run_state.json",
        "b_launch": predecessor.base.HISTORY / "v188_to_b_top4_pipeline_20260826/xp_cpu.launch.json",
        "a": predecessor.base.HISTORY / "v189_to_a_top4_fresh_20260826_attempt002_xp_cpu/algorithm_a_state.json",
        "a_launch": predecessor.base.HISTORY / "v189_to_a_top4_pipeline_20260826/xp_cpu.launch.json",
    },
    "hybench_xp_io": {
        "suffix": "xp_io",
        "c": predecessor.base.HISTORY / "v186_to_c_top4_io_xp4_20260826_attempt013_xp_io/run_state.json",
        "b": predecessor.base.HISTORY / "v188_to_b_top4_fresh_20260826_attempt012_xp_io/run_state.json",
        "b_launch": predecessor.base.HISTORY / "v188_to_b_top4_pipeline_20260826/xp_io.launch.json",
        "a": predecessor.base.HISTORY / "v189_to_a_top4_fresh_20260826_attempt002_xp_io/algorithm_a_state.json",
        "a_launch": predecessor.base.HISTORY / "v189_to_a_top4_pipeline_20260826/xp_io.launch.json",
    },
}


def main() -> int:
    return predecessor.main()


if __name__ == "__main__":
    raise SystemExit(main())
