#!/usr/bin/env python3
"""Launch a two-worker JOB resume with bounded read-only prestart retry."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import launch_mysql_algorithm_a_job_two_worker_resume_v1 as legacy

legacy.CONTROLLER = ROOT / "scripts/run_mysql_algorithm_a_same_cohort_retry_resume_v1.py"
legacy.CONTRACT = (
    ROOT / "config/formal/frozen_mysql_algorithm_a_same_cohort_retry_20260823_v1"
    / "retry_contract.json"
)


if __name__ == "__main__":
    raise SystemExit(legacy.main())
