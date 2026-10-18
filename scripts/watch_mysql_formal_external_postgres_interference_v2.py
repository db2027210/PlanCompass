#!/usr/bin/env python3
"""Run the formal watchdog with the sealed v53 co-resident PG policy."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_co_resident_pg_isolation_v2 as isolation
from scripts import watch_mysql_formal_external_postgres_interference_v1 as watchdog


def main() -> int:
    isolation.install()
    return watchdog.main()


if __name__ == "__main__":
    raise SystemExit(main())
