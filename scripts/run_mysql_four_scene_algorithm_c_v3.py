#!/usr/bin/env python3
"""Run unchanged Algorithm C using the post-timeout v15 authority chain."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Install the recovered live-binding authority on the shared R0 module before
# importing the Algorithm C implementation.  No convergence or acceptance
# constants are changed here.
from scripts import run_mysql_four_scene_r0_v6 as recovered_r0  # noqa: F401,E402
from scripts import run_mysql_four_scene_algorithm_c_v2 as implementation  # noqa: E402


implementation.R0 = (
    ROOT / "history/mysql_four_scene_20260809"
    / "four_scene_native_iq1_iq7_r0_v27_20260811T170200p0800"
    / "r0_binding.json"
)
implementation.POST_R0_BENCHMARK_ACCOUNTS = (
    ROOT / "history/mysql_four_scene_20260809"
    / "benchmark_accounts_v30_20260811T171800p0800"
    / "benchmark_accounts_binding.json"
)


if __name__ == "__main__":
    raise SystemExit(implementation.main())
