#!/usr/bin/env python3
"""Run the confirmed watchdog with the v59 control-plane policy."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_co_resident_pg_isolation_v7 as isolation
from scripts import watch_mysql_formal_external_postgres_interference_v5 as predecessor


def main() -> int:
    isolation.install()
    predecessor.watchdog.confirmed_workload_isolation_failure = (
        predecessor.confirmed_workload_isolation_failure
    )
    return predecessor.watchdog.main()


if __name__ == "__main__":
    raise SystemExit(main())
