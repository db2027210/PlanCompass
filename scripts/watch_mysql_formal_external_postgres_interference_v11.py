#!/usr/bin/env python3
"""Run the v62 watchdog with bounded retries for read-only unit inspection.

A transient user-systemd bus stall must not silently remove runtime coverage.
The isolation checks remain fail-closed: only ``systemctl show`` is retried,
three times, and exhaustion still terminates the watchdog with an error.
"""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_co_resident_pg_isolation_v10 as isolation
from scripts import watch_mysql_formal_external_postgres_interference_v5 as confirmed
from scripts import watch_mysql_formal_external_postgres_interference_v1 as watchdog


UNIT_FIELD_ATTEMPTS = 3
UNIT_FIELD_TIMEOUT_SECONDS = 10
UNIT_FIELD_RETRY_SECONDS = 0.75


def unit_fields(unit: str) -> dict[str, str]:
    last_error: BaseException | None = None
    for attempt in range(1, UNIT_FIELD_ATTEMPTS + 1):
        try:
            shown = subprocess.run(
                [
                    "systemctl", "--user", "show", unit,
                    "-p", "ActiveState", "-p", "SubState",
                    "-p", "MainPID", "-p", "Result",
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=UNIT_FIELD_TIMEOUT_SECONDS,
            ).stdout
            return dict(
                line.split("=", 1) for line in shown.splitlines() if "=" in line
            )
        except (subprocess.SubprocessError, OSError) as error:
            last_error = error
            if attempt < UNIT_FIELD_ATTEMPTS:
                time.sleep(UNIT_FIELD_RETRY_SECONDS)
    raise RuntimeError(
        f"systemctl unit inspection failed after {UNIT_FIELD_ATTEMPTS} attempts"
    ) from last_error


def install() -> None:
    isolation.install()
    watchdog.confirmed_workload_isolation_failure = (
        confirmed.confirmed_workload_isolation_failure
    )
    watchdog.unit_fields = unit_fields


def main() -> int:
    install()
    return watchdog.main()


if __name__ == "__main__":
    raise SystemExit(main())
