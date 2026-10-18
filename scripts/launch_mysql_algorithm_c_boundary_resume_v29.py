#!/usr/bin/env python3
"""Launch the v29 bounded confirmation continuation."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import launch_mysql_algorithm_c_boundary_resume_v28 as v28


def main() -> int:
    launcher = v28.v25.v24.v23.v22.v20.v19.launcher
    launcher.CONTROLLER = (
        ROOT / "scripts/run_mysql_four_scene_algorithm_c_boundary_resume_v29.py"
    )
    return launcher.main()


if __name__ == "__main__":
    raise SystemExit(main())
