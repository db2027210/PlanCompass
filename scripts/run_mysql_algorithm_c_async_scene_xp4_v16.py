#!/usr/bin/env python3
"""Run IO-C physical-wave partial continuation under the v23 contract."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_c_async_scene_xp4_v1 as controller
from scripts import run_mysql_four_scene_algorithm_c_xp4_runtime_v10 as runtime

CONTRACT = ROOT / "config/formal/frozen_mysql_algorithm_c_top4_io_xp4_recovery_20260826_v23"


def main() -> int:
    controller.runtime = runtime
    controller.CONTRACT_ROOT = CONTRACT
    controller.ASYNC_CONTRACT = CONTRACT / "algorithm_c_xp4_async_contract.json"
    controller.OPTIMIZATION_CONTRACT = CONTRACT / "optimization_contract.json"
    controller.COORDINATION_CONTRACT = CONTRACT / "coordination_contract.json"
    return controller.main()


if __name__ == "__main__":
    raise SystemExit(main())
