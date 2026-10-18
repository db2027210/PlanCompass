#!/usr/bin/env python3
"""Dispatch Top4 B using the current per-scene Algorithm C sequence gate."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import launch_mysql_xp_top4_algorithm_b_scene_v1 as launcher


launcher.SCENES["hybench_xp_io"]["c_state"] = (
    launcher.HISTORY
    / "v172_to_c_top4_io_xp4_20260826_attempt006_xp_io/run_state.json"
)


if __name__ == "__main__":
    raise SystemExit(launcher.main())
