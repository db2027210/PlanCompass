#!/usr/bin/env python3
"""Regression checks for v43 coverage symbols and timeout handling."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v43 as runtime


def main() -> int:
    assert runtime._admit_coverage_top10.__module__ == runtime.__name__
    assert runtime._replay_coverage_top10.__module__ == runtime.__name__
    assert runtime.LOCAL_REAL_REPLAY_LIMIT == 10
    assignments = {
        "tpch_w1": {"rank": 1, "params": {}},
        "tpch_w2": {"rank": 2, "params": {}},
    }
    document = {
        "status": "FAILED_CLOSED",
        "failures": [
            {"task": "tpch_w1", "error": "TimeoutExpired: 900 seconds"},
            {"task": "tpch_w1_plan_capture", "error": "skipped_after_invalid_workload"},
        ],
        "measurements": {
            "tpch_w1": {"hard_gate_passed": False, "coverage_complete": False},
            "tpch_w2": {"hard_gate_passed": True, "coverage_complete": True},
        },
    }
    result = runtime.classify_technical_timeout_batch(document, assignments)
    assert result is not None and result["failed_worker_ids"] == ["tpch_w1"]
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
