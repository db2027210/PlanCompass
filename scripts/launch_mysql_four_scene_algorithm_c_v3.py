#!/usr/bin/env python3
"""Launch unchanged Algorithm C with the recovered v15 R0 authority."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import launch_mysql_four_scene_algorithm_c_v2 as implementation


implementation.CONTROLLER = ROOT / "scripts/run_mysql_four_scene_algorithm_c_v3.py"
implementation.R0 = (
    ROOT / "history/mysql_four_scene_20260809"
    / "four_scene_native_iq1_iq7_r0_v27_20260811T170200p0800"
    / "r0_binding.json"
)


if __name__ == "__main__":
    raise SystemExit(implementation.main())
