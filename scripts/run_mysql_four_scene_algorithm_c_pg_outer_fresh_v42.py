#!/usr/bin/env python3
"""Run fresh MySQL Algorithm C with the frozen PostgreSQL search outer."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_co_resident_pg_isolation_v11 as isolation
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v2 as runtime
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v18 as v18
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v22 as v22
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v24 as v24
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v34 as v34


OPTIMIZATION_CONTRACT = (
    ROOT / "config/formal/frozen_mysql_pg_outer_20260819_v33"
    / "algorithm_c_optimization_contract.json"
)
COORDINATION_CONTRACT = (
    ROOT / "config/formal/frozen_mysql_pg_outer_20260819_v33"
    / "coordination_contract.json"
)


def install() -> None:
    # Bind the changed old controller source to a new immutable coordination
    # release; no predecessor frozen file is edited in place.
    isolation.CONTRACT_PATH = COORDINATION_CONTRACT
    isolation.install()
    v24.isolation = isolation
    v22.isolation = isolation
    v22.v18.isolation_v9 = isolation
    runtime.WAVE_COORDINATION_CONTRACT = COORDINATION_CONTRACT
    runtime.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    v18.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    v34.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT


def main() -> int:
    install()
    return v34.main()


if __name__ == "__main__":
    raise SystemExit(main())
