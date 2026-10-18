#!/usr/bin/env python3
"""Focused tests for the canonical JOB fixed-worker schedule contract."""

from __future__ import annotations

import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("run_job_fixed_benchmark_capture.py")
SPEC = importlib.util.spec_from_file_location("job_fixed_capture_tested", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def main() -> int:
    sql_dir = Path("/home/anonymous/dataset/pg_data/join-order-benchmark")
    first = MODULE.build_fixed_schedule(
        sql_dir,
        workers=2,
        long_query_fraction=0.2,
        max_concurrent_long_queries=1,
    )
    second = MODULE.build_fixed_schedule(
        sql_dir,
        workers=2,
        long_query_fraction=0.2,
        max_concurrent_long_queries=1,
    )
    assert first == second
    assert first["contract"] == "canonical_job_113_fixed_worker_lanes_v1"
    assert first["worker_assignment_fixed"] is True
    assert first["lane_order_fixed"] is True
    assert first["query_count"] == 113
    assert len(first["lane_assignments"]) == 2
    flattened = [name for lane in first["lane_assignments"] for name in lane]
    assert len(flattened) == 113
    assert len(set(flattened)) == 113
    assert sorted(flattened) == first["query_set"]
    assert set(first["global_lpt_order"]) == set(flattened)
    assert len(first["schedule_sha256"]) == 64
    print("job_fixed_benchmark_capture: PASS", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
