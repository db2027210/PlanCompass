#!/usr/bin/env python3
"""Fresh Top4-B with four lanes and the frozen 300-second XP drain runner."""

from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mysql_workload_adapter_v2 as workload_adapter_v2
from scripts import mysql_cross_project_wave_coordination_v8 as gate_v8
from scripts import mysql_xp_post_reset_scene_scoped_settle_v1 as scene_settle
from scripts import run_mysql_algorithm_b_xp_top4_async_v13 as predecessor


CONTRACT_ROOT = ROOT / "config/formal/frozen_mysql_xp_top4_algorithm_b_20260826_v32"
HYBENCH_ADAPTIVE_RUNNER_V4 = (
    ROOT / "config/formal/frozen_mysql_async_outer_20260820_v38/runtime"
    / "hybench-adaptive-runner-v4.jar"
)


def run_hybench_300s(*args: Any, **kwargs: Any) -> dict[str, Any]:
    control = kwargs.get("formal_control")
    if control is not None and int(control.get("graceful_quiescence_seconds", 0)) != 300:
        raise RuntimeError("Top4-B 300-second drain authority differs")
    return workload_adapter_v2.run_hybench(*args, **kwargs)


def main() -> int:
    successor = predecessor.successor
    successor.CONTRACT_ROOT = CONTRACT_ROOT
    successor.gate_v7 = gate_v8
    successor.predecessor.install()
    successor.install()
    core = successor.predecessor
    original_configure = core._impl["configure_runtime"]

    def configure_runtime() -> Mapping[str, Any]:
        contract = original_configure()
        core._impl["alg"].run_hybench = run_hybench_300s
        core._impl["alg"].HYBENCH_ADAPTIVE_RUNNER_JAR = HYBENCH_ADAPTIVE_RUNNER_V4
        core._impl["alg"].reset_xp = scene_settle.reset_xp
        return contract

    core._impl["configure_runtime"] = configure_runtime
    core._impl["alg"].reset_xp = scene_settle.reset_xp
    return int(core._implementation_main())


if __name__ == "__main__":
    raise SystemExit(main())
