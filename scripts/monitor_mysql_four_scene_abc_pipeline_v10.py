#!/usr/bin/env python3
"""Monitor corrected v267 CPU-B and the recovered IO-C pipeline."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import monitor_mysql_four_scene_abc_pipeline_v9 as release

predecessor = release.predecessor
predecessor.STATE = (
    predecessor.HISTORY / "v269_to_abc_top4_pipeline_20260826/pipeline_state.json"
)
predecessor.XP["hybench_xp_cpu"]["b"] = (
    predecessor.HISTORY
    / "v267_to_b_top4_score_resume_20260826_attempt030_xp_cpu/run_state.json"
)
predecessor.XP["hybench_xp_cpu"]["b_launch"] = (
    predecessor.HISTORY / "v267_to_b_top4_pipeline_20260826/xp_cpu.launch.json"
)
for scene in ("hybench_xp_cpu", "hybench_xp_io"):
    suffix = "xp_cpu" if scene.endswith("cpu") else "xp_io"
    predecessor.XP[scene]["a"] = (
        predecessor.HISTORY
        / f"v268_to_a_top4_fresh_20260826_attempt008_{suffix}/algorithm_a_state.json"
    )
    predecessor.XP[scene]["a_launch"] = (
        predecessor.HISTORY / f"v268_to_a_top4_pipeline_20260826/{suffix}.launch.json"
    )
predecessor.B_LAUNCHER = (
    predecessor.ROOT / "scripts/launch_mysql_xp_top4_algorithm_b_scene_v16.py"
)
predecessor.A_LAUNCHER = (
    predecessor.ROOT / "scripts/launch_mysql_algorithm_a_xp_top4_scene_v8.py"
)

if __name__ == "__main__":
    raise SystemExit(predecessor.main())
