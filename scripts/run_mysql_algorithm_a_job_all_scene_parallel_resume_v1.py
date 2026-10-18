#!/usr/bin/env python3
"""Resume corrected JOB Algorithm A without a sibling-scene NVMe lease."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_algorithm_a_all_scene_parallel_policy_v1 as parallel
from scripts import mysql_job_buffer_pool_preconditioned_measurement_v9 as measurement
from scripts import run_mysql_algorithm_a_same_cohort_codefix_resume_v1 as resume


_ORIGINAL_VALIDATE = resume.base.validate_fresh_authority
_ORIGINAL_FRESH_STATE = resume.base.fresh_state


def validate_fresh_authority(**kwargs):
    result = _ORIGINAL_VALIDATE(**kwargs)
    parallel.validate_contract()
    measurement._validate_contract()
    resume.base.runtime.alg.measure_batch = measurement.measure_batch
    if resume.base.runtime.alg.measure_batch is not measurement.measure_batch:
        raise RuntimeError("JOB v9 measurement adapter binding did not persist")
    return result


def fresh_state(**kwargs):
    return parallel.annotate_state(_ORIGINAL_FRESH_STATE(**kwargs))


def main() -> int:
    if "--scene" not in sys.argv or sys.argv[sys.argv.index("--scene") + 1] != "job_memory":
        raise RuntimeError("parallel JOB continuation is JOB-only")
    parallel.validate_contract()
    measurement._validate_contract()
    resume.base.validate_fresh_authority = validate_fresh_authority
    resume.base.fresh_state = fresh_state
    return resume.main()


if __name__ == "__main__":
    raise SystemExit(main())
