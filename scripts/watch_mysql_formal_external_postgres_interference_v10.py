#!/usr/bin/env python3
"""Run the formal watchdog with the v62 reset-aware lease budget."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_co_resident_pg_isolation_v10 as isolation
from scripts import watch_mysql_formal_external_postgres_interference_v9 as predecessor


def main() -> int:
    isolation.install()
    return predecessor.main()


if __name__ == "__main__":
    raise SystemExit(main())
