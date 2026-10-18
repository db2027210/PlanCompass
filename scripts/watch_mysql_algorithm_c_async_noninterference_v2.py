#!/usr/bin/env python3
"""Observe asynchronous C using the v56 dynamic isolation successor."""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_cross_project_wave_coordination_v3 as dynamic_gate
from scripts import watch_mysql_algorithm_c_async_noninterference_v1 as predecessor


_legacy_install = predecessor.isolation_v13.install


def install_dynamic_isolation() -> None:
    _legacy_install()
    legacy_evaluate = predecessor.coordination.evaluate_co_resident_pg_isolation

    def evaluate(workloads):
        return dynamic_gate.effective_isolation(legacy_evaluate(workloads))

    predecessor.coordination.evaluate_co_resident_pg_isolation = evaluate


def main() -> int:
    predecessor.isolation_v13.install = install_dynamic_isolation
    return predecessor.main()


if __name__ == "__main__":
    raise SystemExit(main())
