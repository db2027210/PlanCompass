#!/usr/bin/env python3
"""Launch the corrected outer-1 resume with the post-failure fresh chain."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import launch_mysql_four_scene_algorithm_c_resume_v1 as implementation


implementation.RUNTIME_PREFLIGHT = (
    ROOT / "preflight/mysql_four_scene_20260809/runtime_preflight_v32.json"
)
implementation.PREPARED = (
    ROOT / "history/mysql_four_scene_20260809"
    / "four_scene_live_rebind_v30_20260812T010000p0800"
    / "prepared_binding.json"
)
implementation.BENCHMARK_ACCOUNTS = (
    ROOT / "history/mysql_four_scene_20260809"
    / "benchmark_accounts_v33_20260812T010100p0800"
    / "benchmark_accounts_binding.json"
)


if __name__ == "__main__":
    raise SystemExit(implementation.main())
