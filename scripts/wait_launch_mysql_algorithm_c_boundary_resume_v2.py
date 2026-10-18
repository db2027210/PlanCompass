#!/usr/bin/env python3
"""Wait for a v52-isolated window and launch the v47 Algorithm C runtime."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_co_resident_pg_isolation_v2 as isolation
from scripts import wait_launch_mysql_algorithm_c_boundary_resume_v1 as waiter

def main() -> int:
    isolation.install()
    waiter.LAUNCHER = ROOT / "scripts/launch_mysql_algorithm_c_boundary_resume_v3.py"
    return waiter.main()


if __name__ == "__main__":
    raise SystemExit(main())
