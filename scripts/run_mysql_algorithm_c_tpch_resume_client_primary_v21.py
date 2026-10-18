#!/usr/bin/env python3
"""TPC-H Algorithm C v110 controller with client-QPS normalization."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_tpch_resume_client_primary_v15 as tpch
from scripts import run_mysql_algorithm_c_four_scene_client_primary_v20 as v20


def main() -> int:
    tpch.install_normalization()
    return v20.main()


if __name__ == "__main__":
    raise SystemExit(main())
