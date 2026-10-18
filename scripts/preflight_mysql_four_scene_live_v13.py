#!/usr/bin/env python3
"""Run the v15 live gate for the sealed Algorithm C outer-1 checkpoint."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import preflight_mysql_four_scene_live_v11 as v15_defaults  # noqa: F401,E402
from scripts import preflight_mysql_four_scene_live_v8 as implementation  # noqa: E402


implementation.XP_PREPARED = (
    ROOT / "history/mysql_four_scene_20260809"
    / "algorithm_c_outer1_checkpoint_v1_20260811T184000p0800"
    / "prepared_binding.json"
)
implementation.OUTPUT_SCHEMA = "mysql_four_scene_live_runtime_preflight_v17"
implementation.AUTHORITY_VERSION = "v17_algorithm_c_outer1_checkpoint"


if __name__ == "__main__":
    raise SystemExit(implementation.main())
