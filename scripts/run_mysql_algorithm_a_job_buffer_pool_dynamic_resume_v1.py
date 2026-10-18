#!/usr/bin/env python3
"""Resume v7 buffer-pool-fixed Algorithm A JOB on four audited workers."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_job_buffer_pool_preconditioned_measurement_v7 as measurement
from scripts import run_mysql_algorithm_a_job_dynamic_resume_v3 as successor


def main() -> int:
    measurement._validate_contract()
    successor.adapter.base = successor.current
    successor.current.runtime.alg.measure_batch = measurement.measure_batch
    return successor.adapter.main()


if __name__ == "__main__":
    raise SystemExit(main())
