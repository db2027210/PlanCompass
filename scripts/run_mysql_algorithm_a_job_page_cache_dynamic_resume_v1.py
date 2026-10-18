#!/usr/bin/env python3
"""Resume page-cache-fixed Algorithm A JOB on four audited workers."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_job_page_cache_preconditioned_measurement_v3 as cache_measurement
from scripts import run_mysql_algorithm_a_job_dynamic_resume_v3 as successor


def main() -> int:
    cache_measurement._validate_contract()
    successor.adapter.base = successor.current
    successor.current.runtime.alg.measure_batch = cache_measurement.measure_batch
    return successor.adapter.main()


if __name__ == "__main__":
    raise SystemExit(main())
