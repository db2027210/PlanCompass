#!/usr/bin/env python3
"""Regression checks for standalone relational timeout recovery."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v42 as runtime


def main() -> int:
    assignments = {
        "tpch_w1": {"rank": 1, "params": {"row_evaluate_cost": 0.1}},
        "tpch_w2": {"rank": 2, "params": {"row_evaluate_cost": 0.2}},
    }
    document = {
        "status": "FAILED_CLOSED",
        "failures": [
            {"task": "tpch_w1", "error": "TimeoutExpired: timed out after 900 seconds"},
            {"task": "tpch_w1_plan_capture", "error": "skipped_after_invalid_workload"},
        ],
        "measurements": {
            "tpch_w1": {
                "hard_gate_passed": False, "coverage_complete": False,
            },
            "tpch_w2": {
                "hard_gate_passed": True, "coverage_complete": True,
            },
        },
    }
    result = runtime.classify_technical_timeout_batch(document, assignments)
    assert result is not None
    assert result["classification"] == "TECHNICAL_TIMEOUT_EXCLUDED"
    assert result["failed_worker_ids"] == ["tpch_w1"]
    assert "tpch_w2" not in result["failed_worker_ids"]
    assert runtime.REUSE_CHECKPOINT_INCUMBENT_BASELINE is True
    assert "scene_checkpoint.verify_or_standard" in runtime._source
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
