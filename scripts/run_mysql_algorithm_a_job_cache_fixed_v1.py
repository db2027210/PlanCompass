#!/usr/bin/env python3
"""Run fresh two-worker MySQL Algorithm A JOB with fixed cache state."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_job_cache_preconditioned_measurement_v1 as cache_measurement
from scripts import run_mysql_algorithm_a_async_scene_v3 as predecessor


_ORIGINAL_VALIDATE_FRESH_AUTHORITY = predecessor.validate_fresh_authority


def validate_fresh_authority(**kwargs: Any):
    result = _ORIGINAL_VALIDATE_FRESH_AUTHORITY(**kwargs)
    cache_measurement._validate_contract()
    predecessor.runtime.alg.measure_batch = cache_measurement.measure_batch
    return result


def main() -> int:
    if "--scene" not in sys.argv:
        raise RuntimeError("cache-fixed Algorithm A requires --scene")
    index = sys.argv.index("--scene") + 1
    if index >= len(sys.argv) or sys.argv[index] != "job_memory":
        raise RuntimeError("cache-fixed Algorithm A JOB is JOB-only")
    cache_measurement._validate_contract()
    predecessor.validate_fresh_authority = validate_fresh_authority
    return predecessor.main()


if __name__ == "__main__":
    raise SystemExit(main())
