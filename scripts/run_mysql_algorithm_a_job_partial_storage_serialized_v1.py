#!/usr/bin/env python3
"""Resume partial JOB Algorithm A evidence with shared-NVMe wave exclusion."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_algorithm_a_shared_nvme_memory_wave_lease_v1 as lease
from scripts import mysql_job_buffer_pool_preconditioned_measurement_v8 as measurement
from scripts import run_mysql_algorithm_a_partial_round_codefix_resume_v1 as resume


def main() -> int:
    if "--scene" not in sys.argv or sys.argv[sys.argv.index("--scene") + 1] != "job_memory":
        raise RuntimeError("storage-serialized JOB continuation is JOB-only")
    measurement._validate_contract()
    resume.base.runtime.alg.measure_batch = measurement.measure_batch
    lease.install(resume.base)
    return resume.main()


if __name__ == "__main__":
    raise SystemExit(main())
