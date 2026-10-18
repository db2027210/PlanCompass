#!/usr/bin/env python3
"""Resume durable-confirmation C with complete Docker readback retries."""

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import mysql_co_resident_pg_isolation_v11 as isolation
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v22 as v22
from scripts import run_mysql_four_scene_algorithm_c_boundary_resume_v23 as v23


def main() -> int:
    isolation.validate_release()
    v22.isolation = isolation
    v22.v18.isolation_v9 = isolation
    v22.runtime.WAVE_COORDINATION_CONTRACT = isolation.CONTRACT_PATH
    return v23.main()


if __name__ == "__main__":
    raise SystemExit(main())

