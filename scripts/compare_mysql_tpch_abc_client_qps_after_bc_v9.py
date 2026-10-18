#!/usr/bin/env python3
"""Run the v8 comparison with its single TPC-H runtime scene installed."""

from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import compare_mysql_tpch_abc_client_qps_after_bc_v8 as predecessor
from scripts import run_mysql_tpch_algorithm_b_client_qps_fresh_v1 as b_runtime


SCENE = "tpch_sf4_memory"


def main() -> int:
    original_install = b_runtime.install_runtime

    def install_runtime():
        core = original_install()
        core._impl["SCENES"] = (SCENE,)
        return core

    b_runtime.install_runtime = install_runtime
    try:
        return int(predecessor.main())
    finally:
        b_runtime.install_runtime = original_install


if __name__ == "__main__":
    raise SystemExit(main())
