#!/usr/bin/env python3
"""Run the 15-second watchdog with the v57 read-only isolation profile."""

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_co_resident_pg_isolation_v5 as isolation
from scripts import watch_mysql_formal_external_postgres_interference_v1 as watchdog


CONFIRMATION_SECONDS = 15.0
ORIGINAL_CONFIRM = watchdog.confirmed_workload_isolation_failure


def confirmed_workload_isolation_failure(
    run_id: str, first_result: dict[str, Any], *, target_mems: set[int],
    confirmation_seconds: float = CONFIRMATION_SECONDS,
) -> dict[str, Any] | None:
    return ORIGINAL_CONFIRM(
        run_id, first_result, target_mems=target_mems,
        confirmation_seconds=confirmation_seconds,
    )


def install() -> None:
    isolation.install()
    watchdog.confirmed_workload_isolation_failure = (
        confirmed_workload_isolation_failure
    )


def main() -> int:
    install()
    return watchdog.main()


if __name__ == "__main__":
    raise SystemExit(main())
