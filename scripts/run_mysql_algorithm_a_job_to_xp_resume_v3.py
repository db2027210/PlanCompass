#!/usr/bin/env python3
"""Resume an XP lane on four workers with inventory-time PG proof."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_co_resident_pg_isolation_v15 as pg_wrapper
from scripts import run_mysql_algorithm_a_job_to_xp_resume_v2 as predecessor


def main() -> int:
    # v2 resolves this module global from its own namespace at runtime.
    predecessor.pg_wrapper = pg_wrapper
    return predecessor.main()


if __name__ == "__main__":
    raise SystemExit(main())
