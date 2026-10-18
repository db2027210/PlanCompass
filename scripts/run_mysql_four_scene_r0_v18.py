#!/usr/bin/env python3
"""Run the full eight-worker formal R0 under the sealed v18 envelope."""

from pathlib import Path
import os
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_four_scene_r0_v5 as implementation


PREFLIGHT = os.environ.get("MYSQL_V18_PASS_PREFLIGHT")
PREPARED = os.environ.get("MYSQL_V18_FORMAL_PREPARED")
ACCOUNTS = os.environ.get("MYSQL_V18_FORMAL_ACCOUNTS")
if not all((PREFLIGHT, PREPARED, ACCOUNTS)):
    raise SystemExit("MYSQL_V18_PASS_PREFLIGHT, MYSQL_V18_FORMAL_PREPARED and MYSQL_V18_FORMAL_ACCOUNTS are required")
implementation.FREEZE = ROOT / "config/formal/frozen_mysql_disk2_two_worker_20260813_v18/four_scene_effective_resource_successor.json"
implementation.FREEZE_SCHEMA = "mysql_disk2_four_scene_two_worker_effective_resource_successor_v18"
implementation.PREFLIGHT = Path(PREFLIGHT).resolve()
implementation.RUNTIME_PREFLIGHT = Path(PREFLIGHT).resolve()
implementation.PREPARED = Path(PREPARED).resolve()
implementation.BENCHMARK_ACCOUNTS = Path(ACCOUNTS).resolve()
implementation.COORDINATED_LEASE_HOLDER_PID = int(
    os.environ["MYSQL_V18_COORDINATION_LEASE_HOLDER_PID"]
)
implementation.COORDINATION_CONTRACT = Path(
    os.environ["MYSQL_V18_COORDINATION_CONTRACT"]
).resolve()


if __name__ == "__main__":
    raise SystemExit(implementation.main())
