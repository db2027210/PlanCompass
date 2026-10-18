#!/usr/bin/env python3
"""Launch a fresh two-worker page-cache-fixed Algorithm A JOB scene."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import launch_mysql_algorithm_a_job_cache_fixed_v1 as legacy

legacy.CONTROLLER = ROOT / "scripts/run_mysql_algorithm_a_job_page_cache_fixed_v2.py"
legacy.CONTRACT = (
    ROOT / "config/formal/frozen_mysql_job_cache_state_20260823_v2"
    / "cache_state_contract.json"
)


if __name__ == "__main__":
    raise SystemExit(legacy.main())
