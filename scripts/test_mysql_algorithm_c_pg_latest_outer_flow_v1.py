#!/usr/bin/env python3
"""Static and pure-logic checks for the PG-aligned MySQL C successor."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v41 as runtime


def main() -> int:
    source = runtime._source
    assert 'if int(state["consecutive_failed_rounds"][scene]) != 1:' not in source
    assert ") = _replay_coverage_top10(" in source
    assert "params=params_by_scene[scene],\n                    ranges=initial_ranges_by_scene[scene]" not in source
    assert '"current_interval_plan_coverage_001": 2' in source
    assert '"current_interval_plan_coverage_002": 2' in source
    assert "GLOBAL_COVERAGE_REAL_REPLAY_LIMIT = 10" not in source  # helper-owned
    assert runtime.GLOBAL_COVERAGE_SAMPLE_COUNT == 256
    assert runtime.GLOBAL_COVERAGE_REAL_REPLAY_LIMIT == 10
    assert runtime.LOCAL_REAL_REPLAY_LIMIT == 10
    print("PASS MySQL Algorithm C PG-latest outer-flow checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
