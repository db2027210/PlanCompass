#!/usr/bin/env python3
"""Run Algorithm A JOB with the exact-readback v8 InnoDB primer."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_job_buffer_pool_preconditioned_measurement_v8 as measurement
from scripts import run_mysql_algorithm_a_job_buffer_pool_fixed_v7 as runner

runner.measurement = measurement


if __name__ == "__main__":
    raise SystemExit(runner.main())
