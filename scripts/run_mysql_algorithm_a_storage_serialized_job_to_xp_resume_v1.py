#!/usr/bin/env python3
"""Resume a four-worker XP Algorithm A with the shared-NVMe wave lease."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_algorithm_a_shared_nvme_wave_lease_v1 as lease
from scripts import run_mysql_algorithm_a_async_scene_v3 as current
from scripts import run_mysql_algorithm_a_job_to_xp_resume_v1 as adapter

lease.install(current)
adapter.base = current


if __name__ == "__main__":
    raise SystemExit(adapter.main())
