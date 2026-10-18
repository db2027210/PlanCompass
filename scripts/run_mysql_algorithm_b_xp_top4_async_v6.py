#!/usr/bin/env python3
"""Top4 B v15 on the actual-disjoint four-worker resource successor."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_algorithm_b_fresh_parallel_4x_scheduler_v26 as scheduler_v26
from scripts import mysql_cross_project_wave_coordination_v7 as gate_v7
from scripts import run_mysql_algorithm_b_xp_top4_async_v3 as predecessor


CONTRACT_ROOT = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_b_20260826_v15"
FREEZE = (
    ROOT / "config/formal/frozen_mysql_xp_cpu_disjoint_slots_20260826_v4"
    / "resource_successor.json"
)
FREEZE_SCHEMA = "mysql_algorithm_c_xp_four_worker_after_job_resource_successor_v1"
_original_configure_runtime = predecessor.configure_runtime


def configure_runtime() -> dict[str, Any]:
    predecessor._impl["FREEZE"] = FREEZE
    contract = _original_configure_runtime()
    predecessor._impl["alg"].FREEZE = FREEZE
    predecessor._impl["alg"].r0mod.FREEZE = FREEZE
    predecessor._impl["alg"].r0mod.FREEZE_SCHEMA = FREEZE_SCHEMA
    predecessor._impl["alg"].r0mod.dynamic_gate = gate_v7
    predecessor._impl["no_lease_runtime"].coordination = gate_v7
    return contract


def install() -> None:
    predecessor.CONTRACT_ROOT = CONTRACT_ROOT
    predecessor._impl["scheduler"] = scheduler_v26
    predecessor._impl["FREEZE"] = FREEZE
    predecessor._impl["configure_runtime"] = configure_runtime
    predecessor._impl["no_lease_runtime"].coordination = gate_v7


def main() -> int:
    install()
    return predecessor.main()


if __name__ == "__main__":
    raise SystemExit(main())
