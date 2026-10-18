#!/usr/bin/env python3
"""Run Top4-B with the frozen 300-second native-XP adapter and runner."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import run_mysql_algorithm_b_four_scene_top4_two_worker_v5 as predecessor


CONTRACT_ROOT = ROOT / "config/formal/frozen_mysql_four_scene_top4_algorithm_b_20260826_v49"
CONTRACT = CONTRACT_ROOT / "algorithm_b_top4_contract.json"
COORDINATION = CONTRACT_ROOT / "coordination_contract.json"
HYBENCH_ADAPTIVE_RUNNER_V4 = (
    ROOT / "config/formal/frozen_mysql_async_outer_20260820_v38/runtime"
    / "hybench-adaptive-runner-v4.jar"
)


def install_runtime() -> Any:
    predecessor.CONTRACT_ROOT = CONTRACT_ROOT
    predecessor.CONTRACT = CONTRACT
    predecessor.COORDINATION = COORDINATION
    core = predecessor.install_runtime()
    impl = core._impl
    alg = impl["alg"]
    original_configure_runtime = impl["configure_runtime"]
    original_state_document = impl["state_document"]

    def configure_runtime() -> Mapping[str, Any]:
        contract = original_configure_runtime()
        # The same two-worker predecessor hook that dropped adapter-v2 also
        # left the v3 Java runner installed. The 300-second drain is accepted
        # only when both the Python adapter and Java runner are the v4 pair.
        alg.HYBENCH_ADAPTIVE_RUNNER_JAR = HYBENCH_ADAPTIVE_RUNNER_V4
        return contract

    def state_document(run_id: str) -> dict[str, Any]:
        document = original_state_document(run_id)
        document["native_xp_300s_runner_release"] = {
            "source_path": str(HYBENCH_ADAPTIVE_RUNNER_V4),
            "source_sha256": alg.sha(HYBENCH_ADAPTIVE_RUNNER_V4),
            "requested_graceful_quiescence_seconds": 300,
            "adapter_runner_pair": "mysql_workload_adapter_v2+hybench-adaptive-runner-v4",
            "workload_composition_changed": False,
            "resource_envelope_changed": False,
        }
        return document

    impl["CONTRACT"] = CONTRACT
    impl["COORDINATION_CONTRACT"] = COORDINATION
    impl["configure_runtime"] = configure_runtime
    impl["state_document"] = state_document
    return core


def main() -> int:
    core = install_runtime()
    return int(core._implementation_main())


if __name__ == "__main__":
    raise SystemExit(main())
