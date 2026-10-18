#!/usr/bin/env python3
"""Run asynchronous C with the five-minute native-XP drain adapter."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mysql_workload_adapter_v2 as workload_adapter_v2
from scripts import mysql_cgroup_resource_readback_v2 as cgroup_readback_v2
from scripts import run_mysql_algorithm_c_async_scene_v49 as v49


runtime = v49.runtime
alg = runtime.alg
ASYNC_CONTRACT = (
    ROOT / "config/formal/frozen_mysql_async_outer_20260820_v38"
    / "algorithm_c_async_contract.json"
)
OPTIMIZATION_CONTRACT = (
    ROOT / "config/formal/frozen_mysql_async_outer_20260819_v37"
    / "algorithm_c_async_optimization_contract.json"
)
HYBENCH_ADAPTIVE_RUNNER = (
    ROOT / "config/formal/frozen_mysql_async_outer_20260820_v38/runtime"
    / "hybench-adaptive-runner-v4.jar"
)


def validate_contract() -> dict[str, Any]:
    if not ASYNC_CONTRACT.is_file() or ASYNC_CONTRACT.is_symlink():
        raise RuntimeError(f"sealed asynchronous C v50 contract absent: {ASYNC_CONTRACT}")
    contract = alg.load(ASYNC_CONTRACT)
    if (
        contract.get("release_version") != "v50"
        or contract.get("status") != "FROZEN_RUNTIME_VALIDATED"
        or contract.get("formal_scope") != "mysql_only"
        or contract.get("cross_engine_comparison_prohibited") is not True
        or contract.get("four_scene_wave_barrier") is not False
        or contract.get("per_scene_outer_state_machine") is not True
        or contract.get("scene_may_advance_immediately") is not True
        or contract.get("candidate_batches") != [[1, 2], [3, 4], [5]]
        or contract.get("post_search_stability_confirmation") is not False
        or (contract.get("xp_quiescence_successor") or {}).get(
            "graceful_quiescence_seconds"
        ) != 300
        or (contract.get("xp_quiescence_successor") or {}).get(
            "predecessor_adapter_unchanged"
        ) is not True
    ):
        raise RuntimeError("v50 Algorithm C asynchronous contract differs")
    for binding in (contract.get("source_bindings") or {}).values():
        path = ROOT / str(binding["path"])
        if (
            not path.is_file() or path.is_symlink()
            or alg.sha(path) != str(binding["sha256"])
        ):
            raise RuntimeError(f"v50 Algorithm C source binding differs: {path}")
    return contract


def install() -> None:
    v49.ASYNC_CONTRACT = ASYNC_CONTRACT
    v49.OPTIMIZATION_CONTRACT = OPTIMIZATION_CONTRACT
    v49.install()
    runtime.alg.run_hybench = workload_adapter_v2.run_hybench
    runtime.alg.HYBENCH_ADAPTIVE_RUNNER_JAR = HYBENCH_ADAPTIVE_RUNNER
    runtime.alg.reset.prep._kernel_resource_readback = (
        cgroup_readback_v2.kernel_resource_readback
    )


def main() -> int:
    validate_contract()
    v49.validate_v65_wave_coordination_contract()
    install()
    return v49.v45.v34.main()


if __name__ == "__main__":
    raise SystemExit(main())
