#!/usr/bin/env python3
"""Resume v9 buffer-pool-fixed JOB on four audited workers.

The four-worker runtime config installs the asynchronous authority and thereby
restores the generic measurement adapter.  Rebind v9 immediately after that
installation and assert the live function identity before any baseline or
candidate replay can start.
"""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_job_buffer_pool_preconditioned_measurement_v9 as measurement
from scripts import run_mysql_algorithm_a_job_dynamic_resume_v3 as successor


_ORIGINAL_CONFIGURE = successor.adapter.configure_runtime


def configure_runtime(**kwargs) -> None:
    _ORIGINAL_CONFIGURE(**kwargs)
    measurement._validate_contract()
    successor.current.runtime.alg.measure_batch = measurement.measure_batch
    if successor.current.runtime.alg.measure_batch is not measurement.measure_batch:
        raise RuntimeError("four-worker JOB v9 measurement adapter binding did not persist")


def main() -> int:
    measurement._validate_contract()
    successor.adapter.base = successor.current
    successor.adapter.configure_runtime = configure_runtime
    return successor.adapter.main()


if __name__ == "__main__":
    raise SystemExit(main())
